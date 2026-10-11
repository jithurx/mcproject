// ESP8266 uplink owner: STM32 senses, ESP networks.
// Blue Pill sends "T:28.4,H:81,R:0" on SoftwareSerial (D5/D6); this node
// parses it, pushes to ThingSpeak, and mirrors everything to the PC monitor.
// HW Serial (UART0/GPIO1+3) stays dedicated to PC USB: flashing works with
// the STM32 still connected.
//
// Wiring (NodeMCU Amica <-> Blue Pill, separate USBs):
//   ESP D5/GPIO14 (RX) <- STM32 PA9 (TX)   [both 3V3, direct]
//   ESP D6/GPIO12 (TX) -> STM32 PA10 (RX)   [reserved for F: forecast replies]
//   GND <-> GND.  No 3V3<->3V3.
//
// Protocol:
//   in  "T:<temp>,H:<hum>,R:<0|1>" every ~5 s  -> TELE log + ThingSpeak fields
//   out "F:<0-100>" forecast to STM32 (reserved; sent when known)
//   PC  type "status"/"?" for state; anything else is forwarded to the STM32.
//
// NOTE: WiFi password + ThingSpeak key are baked in (same accepted
// physical-access risk as firmware/STM32_Weather). Rotate in ThingSpeak if
// the repo goes public.

#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <SoftwareSerial.h>
#include <WiFiClient.h>

// ---------- STM32 link ----------
#define STM_RX_PIN 14  // D5: ESP RX <- STM32 PA9 TX
#define STM_TX_PIN 12  // D6: ESP TX -> STM32 PA10 RX
#define STM_BAUD 9600  // SoftwareSerial + WiFi is only stable at low baud;
// 115200 bit-bangs the CPU so hard the Soft WDT fires (crash loop).
// STM32 side must match: ESP_BAUD in firmware/STM32_Weather/src/main.cpp.
SoftwareSerial stmSerial(STM_RX_PIN, STM_TX_PIN);

// ---------- network ----------
#define WIFI_SSID "nickel"
#define WIFI_PASS "acoustic@bun"
#define TS_HOST "api.thingspeak.com"
#define TS_PORT 80
#define TS_WRITE_KEY "RFYKTNB5YD5O96SF"
#define PUSH_EVERY_MS 180000UL  // 3 min (free tier >= 15 s)

#define LED_PIN 2  // onboard LED, active LOW

// ---------- state ----------
static float g_temp = NAN, g_hum = NAN;
static int g_rain = 0;
static uint32_t g_lastRxMs = 0, g_lastTsMs = 0, g_lastPushTry = 0;
static int g_lastTsCode = -999;
static uint32_t g_stmLines = 0;
static String g_line = "";

static void pushThingSpeak() {
  if (WiFi.status() != WL_CONNECTED) return;
  if (isnan(g_temp) || isnan(g_hum)) return;
  if (!g_lastRxMs || millis() - g_lastRxMs > 60000UL) return;  // stale
  g_lastPushTry = millis();
  WiFiClient c;
  if (!c.connect(TS_HOST, TS_PORT)) {
    Serial.println(F("TS: connect failed"));
    return;
  }
  String url = "/update?api_key=" + String(TS_WRITE_KEY) +
               "&field1=" + String(g_temp, 1) +
               "&field2=" + String((int)g_hum) + "&field3=" + String(g_rain);
  c.print(String("GET ") + url + " HTTP/1.1\r\nHost: " + TS_HOST +
          "\r\nConnection: close\r\n\r\n");
  // ThingSpeak replies headers + entry id in the body ("0" = rejected).
  // Read until the server closes, then take the last line.
  String resp = "";
  uint32_t t0 = millis();
  while ((c.connected() || c.available()) && millis() - t0 < 8000UL) {
    while (c.available()) {
      resp += (char)c.read();
      t0 = millis();
    }
    delay(10);
  }
  resp.trim();
  int nl = resp.lastIndexOf('\n');
  String entry = nl >= 0 ? resp.substring(nl + 1) : resp;
  entry.trim();
  g_lastTsCode = entry.toInt();
  g_lastTsMs = millis();
  Serial.printf("TS: entry=%s T=%.1f H=%d R=%d\n", entry.c_str(), g_temp,
                (int)g_hum, g_rain);
}

static void handleStmLine(String s) {
  s.trim();
  if (!s.length()) return;
  g_stmLines++;
  float t = NAN, h = NAN;
  int r = -1;
  if ((s[0] == 'T' || s[0] == 't') &&
      (sscanf(s.c_str(), "T:%f,H:%f,R:%d", &t, &h, &r) == 3 ||
       sscanf(s.c_str(), "t:%f,h:%f,r:%d", &t, &h, &r) == 3)) {
    g_temp = t;
    g_hum = h;
    g_rain = (r != 0);
    g_lastRxMs = millis();
    Serial.printf("TELE T=%.1f H=%d R=%d\n", t, (int)h, g_rain);
    return;
  }
  Serial.print(F("STM: "));
  Serial.println(s);
}

static void wifiConnect(bool first) {
  if (first) {
    WiFi.mode(WIFI_STA);
    Serial.printf("wifi: joining %s ...\n", WIFI_SSID);
  }
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000UL) {
    digitalWrite(LED_PIN, LOW);
    delay(120);
    digitalWrite(LED_PIN, HIGH);
    delay(120);
  }
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("wifi: up ip=%s rssi=%d\n", WiFi.localIP().toString().c_str(),
                  WiFi.RSSI());
  else
    Serial.println(F("wifi: FAILED, retry in loop"));
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);
  Serial.begin(115200);
  stmSerial.begin(STM_BAUD);
  delay(300);
  Serial.println();
  Serial.println(F("esp-uplink v1: STM32 link D5/D6 @115200, PC @115200"));
  wifiConnect(true);
}

void loop() {
  static uint32_t ledMs = 0, wifiMs = 0;
  String pc = "";
  while (stmSerial.available()) {
    char c = (char)stmSerial.read();
    if (c == '\n' || c == '\r') {
      if (g_line.length()) {
        handleStmLine(g_line);
        g_line = "";
      }
    } else if (g_line.length() < 120) {
      g_line += c;
    }
  }
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      pc.trim();
      if (pc.length()) {
        if (pc.equalsIgnoreCase("status") || pc == "?") {
          Serial.printf("up=%lus wifi=%s ip=%s stm=%lu tele_age=%lus ts=%d\n",
                        (unsigned long)millis() / 1000,
                        WiFi.status() == WL_CONNECTED ? "up" : "DOWN",
                        WiFi.localIP().toString().c_str(),
                        (unsigned long)g_stmLines,
                        g_lastRxMs ? (unsigned long)(millis() - g_lastRxMs) / 1000 : 9999,
                        g_lastTsCode);
        } else {
          stmSerial.println(pc);  // forward raw to STM32
          Serial.print(F("FWD> "));
          Serial.println(pc);
        }
        pc = "";
      }
    } else if (pc.length() < 120) {
      pc += c;
    }
  }
  uint32_t now = millis();
  static uint32_t statMs = 0;
  if (now - statMs > 60000UL) {  // heartbeat on the monitor, no typing needed
    statMs = now;
    Serial.printf("up=%lus wifi=%s ip=%s stm=%lu tele_age=%lus ts=%d\n",
                  now / 1000, WiFi.status() == WL_CONNECTED ? "up" : "DOWN",
                  WiFi.localIP().toString().c_str(), (unsigned long)g_stmLines,
                  g_lastRxMs ? (now - g_lastRxMs) / 1000 : 9999, g_lastTsCode);
  }
  if (WiFi.status() != WL_CONNECTED && now - wifiMs > 30000UL) {
    wifiMs = now;
    wifiConnect(false);
  }
  if (!g_lastTsMs || now - g_lastTsMs >= PUSH_EVERY_MS) pushThingSpeak();
  bool ok = WiFi.status() == WL_CONNECTED && g_lastRxMs &&
            now - g_lastRxMs < 30000UL;
  if (now - ledMs >= (ok ? 1500UL : 300UL)) {
    ledMs = now;
    digitalWrite(LED_PIN, LOW);
    delay(25);
    digitalWrite(LED_PIN, HIGH);
  }
}
