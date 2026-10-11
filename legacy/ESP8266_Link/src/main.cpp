// ESP8266 uplink for the Blue Pill Smart Weather Station (NodeMCU Amica).
//
// STM32 (USART1 PA9/PA10 @115200) <-> ESP8266 (hardware Serial @115200):
//   STM32 -> ESP : "T:28.4,H:82,R:0\n" every 5 s   (R: 1 = rain detected)
//   ESP -> STM32 : "F:78\n"  (rain probability 0-100; STM32 shows SRC:ESP
//                  while the value is <60 s old, else falls back to SRC:LOCAL)
//
// This node: parses telemetry, keeps a short history for temp/humidity
// trends, computes a forecast probability (trend-aware heuristic until the
// offline-ML model lands), pushes everything to ThingSpeak (fields below),
// and serves a /status JSON page for debugging.
//
// Wiring (NodeMCU <-> Blue Pill, see README/docs):
//   NodeMCU RX (GPIO3) <- Blue Pill PA9 (TX)     [ divider NOT needed, both 3V3 ]
//   NodeMCU TX (GPIO1) -> Blue Pill PA10 (RX)
//   GND <-> GND.  Power each board from its OWN USB.
//   IMPORTANT: unplug the RX/TX jumpers while flashing the NodeMCU over USB
//   (the USB-serial chip and the STM32 would fight over GPIO3/RX).
//
// Secrets live in include/config.h (gitignored, copied from config.h.example).
// With placeholder secrets the node still parses telemetry and answers F:<prob>
// over serial -- WiFi/ThingSpeak just stay off until you fill them in.

#include <Arduino.h>
#include <ESP8266WebServer.h>
#include <ESP8266WiFi.h>
#include <WiFiClient.h>

#include "config.h"

#define LED_PIN 2  // NodeMCU onboard LED (D4/GPIO2), active LOW

// ---------- telemetry state ----------
static float g_temp = NAN, g_hum = NAN;
static int g_rain = 0;
static uint32_t g_lastRxMs = 0;
static String g_line = "";

// ---------- trend history (ring of samples) ----------
#define HIST_N 12
static float h_temp[HIST_N];
static float h_hum[HIST_N];
static uint32_t h_ms[HIST_N];
static uint8_t h_count = 0, h_idx = 0;
static uint32_t g_lastRainMs = 0;  // last time R:1 was seen (0 = never)

// ---------- misc state ----------
static int g_forecast = -1;
static uint32_t g_lastForecastTxMs = 0;
static uint32_t g_lastTsMs = 0;
static int g_lastTsCode = -999;  // last ThingSpeak entry id, or -http-code on fail
static ESP8266WebServer server(80);

static bool haveSecrets() {
  return strcmp(WIFI_PASS, "REPLACE_ME") != 0;
}
static bool haveTsKey() {
  return strcmp(THINGSPEAK_API_KEY, "REPLACE_ME") != 0;
}

// ----- tiny log ring for /status -----
#define LOG_N 20
static String logBuf[LOG_N];
static uint8_t logIdx = 0;
static void logLine(const String &s) {
  logBuf[logIdx] = s;
  logIdx = (logIdx + 1) % LOG_N;
  Serial.print("# ");
  Serial.println(s);
}

// ---------- forecast: trend-aware heuristic (placeholder for ML model) ----------
// Base mirrors the STM32 local table so both ends agree when trends are flat,
// then humidity rise / temperature fall / recent rain push the probability up.
static int computeForecast() {
  if (isnan(g_temp) || isnan(g_hum)) return -1;
  int p;
  if (g_rain) p = 90;
  else if (g_hum >= 85) p = 80;
  else if (g_hum >= 80) p = 70;
  else if (g_hum >= 70) p = 45;
  else if (g_hum >= 65) p = 30;
  else p = 10;

  // trends over the oldest->newest history span (need >=2 samples, >=60 s span)
  if (h_count >= 2) {
    uint8_t oldest = (h_idx + HIST_N - h_count) % HIST_N;
    uint8_t newest = (h_idx + HIST_N - 1) % HIST_N;
    uint32_t spanMs = h_ms[newest] - h_ms[oldest];
    if (spanMs >= 60000UL && !isnan(h_temp[oldest]) && !isnan(h_hum[oldest])) {
      float hrs = spanMs / 3600000.0f;
      float dT = (h_temp[newest] - h_temp[oldest]) / hrs;  // C per hour
      float dH = (h_hum[newest] - h_hum[oldest]) / hrs;    // % per hour
      if (dH > 5.0f) p += 15;                             // humidity climbing fast
      else if (dH > 2.0f) p += 8;
      if (dT < -1.0f && g_hum >= 65) p += 10;             // cooling into damp air
      if (dT > 2.0f && !g_rain) p -= 8;                   // warming up, dry
    }
  }
  // rained in the last 10 min -> keep probability elevated
  if (g_lastRainMs && millis() - g_lastRainMs < 600000UL && p < 60) p = 60;

  if (p < 0) p = 0;
  if (p > 100) p = 100;
  return p;
}

// ---------- STM32 serial ----------
static void pollStm32() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      g_line.trim();
      // expect "T:28.4,H:82,R:0"
      if (g_line.length() > 8 && (g_line[0] == 'T' || g_line[0] == 't')) {
        float t = NAN, h = NAN;
        int r = 0;
        int n = sscanf(g_line.c_str(), "T:%f,H:%f,R:%d", &t, &h, &r);
        if (n == 3) {
          g_temp = t;
          g_hum = h;
          g_rain = (r != 0);
          g_lastRxMs = millis();
          if (g_rain) g_lastRainMs = millis();
          h_temp[h_idx] = t;
          h_hum[h_idx] = h;
          h_ms[h_idx] = millis();
          h_idx = (h_idx + 1) % HIST_N;
          if (h_count < HIST_N) h_count++;
          digitalWrite(LED_PIN, LOW);
          delay(15);
          digitalWrite(LED_PIN, HIGH);
        }
      }
      g_line = "";
    } else if (g_line.length() < 48) {
      g_line += c;
    }
  }
}

static void sendForecast() {
  int p = computeForecast();
  if (p < 0) return;  // no data yet
  g_forecast = p;
  Serial.printf("F:%d\n", p);  // the one line the STM32 actually parses
  g_lastForecastTxMs = millis();
}

// ---------- ThingSpeak ----------
static void pushThingSpeak() {
  if (!haveTsKey() || WiFi.status() != WL_CONNECTED) return;
  if (isnan(g_temp) || isnan(g_hum)) return;
  if (g_lastRxMs == 0 || millis() - g_lastRxMs > 60000UL) return;  // stale data

  float dT = 0, dH = 0;
  if (h_count >= 2) {
    uint8_t oldest = (h_idx + HIST_N - h_count) % HIST_N;
    uint8_t newest = (h_idx + HIST_N - 1) % HIST_N;
    uint32_t spanMs = h_ms[newest] - h_ms[oldest];
    if (spanMs >= 60000UL) {
      float hrs = spanMs / 3600000.0f;
      dT = (h_temp[newest] - h_temp[oldest]) / hrs;
      dH = (h_hum[newest] - h_hum[oldest]) / hrs;
    }
  }

  WiFiClient client;
  if (!client.connect("api.thingspeak.com", 80)) {
    logLine("thingspeak: connect failed");
    return;
  }
  String url = "/update?api_key=" + String(THINGSPEAK_API_KEY) +
               "&field1=" + String(g_temp, 1) +
               "&field2=" + String((int)g_hum) +
               "&field3=" + String(g_rain) +
               "&field4=" + String(g_forecast < 0 ? 0 : g_forecast) +
               "&field5=" + String(dT, 2) +
               "&field6=" + String(dH, 2);
  client.print(String("GET ") + url + " HTTP/1.1\r\nHost: api.thingspeak.com\r\n"
               "Connection: close\r\n\r\n");
  uint32_t t0 = millis();
  while (!client.available() && millis() - t0 < 8000UL) delay(10);
  String resp = client.readStringUntil('\n');
  // ThingSpeak answers the entry id on success, "0" on rate-limit/failure
  g_lastTsCode = resp.toInt();
  g_lastTsMs = millis();
  logLine("thingspeak: entry=" + resp + " T=" + String(g_temp, 1) +
          " H=" + String((int)g_hum) + " R=" + String(g_rain) +
          " F=" + String(g_forecast));
}

// ---------- web status ----------
static void handleStatus() {
  uint32_t ageS = g_lastRxMs ? (millis() - g_lastRxMs) / 1000 : 9999;
  String j = "{";
  j += "\"wifi\":\"" + String(haveSecrets() ? WiFi.SSID() : "disabled (no secrets)") + "\",";
  j += "\"rssi\":" + String(WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0) + ",";
  j += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  j += "\"temp\":" + String(isnan(g_temp) ? -99.9 : g_temp, 1) + ",";
  j += "\"hum\":" + String(isnan(g_hum) ? -1 : (int)g_hum) + ",";
  j += "\"rain\":" + String(g_rain) + ",";
  j += "\"telemetry_age_s\":" + String(ageS) + ",";
  j += "\"forecast\":" + String(g_forecast) + ",";
  j += "\"thingspeak_last\":" + String(g_lastTsCode) + ",";
  j += "\"log\":[";
  for (int i = 0; i < LOG_N; i++) {
    uint8_t k = (logIdx + i) % LOG_N;
    if (logBuf[k].length() == 0) continue;
    if (!j.endsWith("[")) j += ",";
    j += "\"" + logBuf[k] + "\"";
  }
  j += "]}";
  server.send(200, "application/json", j);
}

// ---------- wifi ----------
static void wifiConnect() {
  if (!haveSecrets()) {
    logLine("no WiFi secrets -- station offline, serial forecast only");
    WiFi.mode(WIFI_OFF);
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  logLine("wifi: connecting to " + String(WIFI_SSID) + "...");
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000UL) {
    digitalWrite(LED_PIN, LOW);
    delay(100);
    digitalWrite(LED_PIN, HIGH);
    delay(100);
  }
  if (WiFi.status() == WL_CONNECTED) {
    logLine("wifi: connected, ip=" + WiFi.localIP().toString());
  } else {
    logLine("wifi: FAILED -- retrying in loop, serial forecast continues");
  }
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);
  Serial.begin(115200);
  Serial.setRxBufferSize(512);
  delay(300);
  Serial.println();
  logLine("weather-uplink v1 boot (NodeMCU, STM32 link @115200)");

  wifiConnect();
  server.on("/status", handleStatus);
  server.on("/", handleStatus);
  server.begin();
  logLine("http: /status on port 80");
}

void loop() {
  static uint32_t lastLedMs = 0, lastWifiRetryMs = 0;
  pollStm32();

  uint32_t now = millis();
  if (g_lastForecastTxMs == 0 || now - g_lastForecastTxMs >= FORECAST_TX_MS) {
    sendForecast();
  }
  if (haveTsKey() && (g_lastTsMs == 0 || now - g_lastTsMs >= THINGSPEAK_INTERVAL_MS)) {
    pushThingSpeak();
  }
  if (haveSecrets() && WiFi.status() != WL_CONNECTED &&
      now - lastWifiRetryMs > 30000UL) {
    lastWifiRetryMs = now;
    logLine("wifi: retry...");
    WiFi.begin(WIFI_SSID, WIFI_PASS);
  }

  // heartbeat: fast blink = no wifi/data problem, slow = healthy
  bool problem = (haveSecrets() && WiFi.status() != WL_CONNECTED) ||
                 (g_lastRxMs == 0) || (now - g_lastRxMs > 30000UL);
  uint32_t period = problem ? 250UL : 1500UL;
  if (now - lastLedMs >= period) {
    lastLedMs = now;
    digitalWrite(LED_PIN, LOW);
    delay(30);
    digitalWrite(LED_PIN, HIGH);
  }

  server.handleClient();
  yield();
}
