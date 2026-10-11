// Smart Weather Station — STM32F103C8T6 Blue Pill + ESP8266 (AT firmware)
// Sensors: DHT11 (temp/humidity) + rain sensor (digital DO, analog AO optional)
// Display: 128x64 SSD1306 OLED (I2C1: SCL=PB6, SDA=PB7, addr 0x3C)
// Uplink: ESP8266 running standard AT firmware on USART1 (TX=PA9, RX=PA10
// @115200). The STM32 is the boss: it drives the ESP with AT commands.
//
//   Telemetry (every PUSH_EVERY_MS): CIPSTART TCP api.thingspeak.com:80,
//     CIPSEND + "GET /update?api_key=<WRITE>&field1=T&field2=H&field3=R ..."
//   Forecast (every FETCH_EVERY_MS): same transport,
//     "GET /channels/<ID>/fields/5/last.txt?api_key=<READ> ..." -> +IPD holds
//     the ML probability 0-100 written by the laptop loop (tools/ml_forecast.py).
// Forecast shown = ESP/ML value when fresh (<ESP_FRESH_MS), else local heuristic
// (rain now -> LIKELY 90; humidity>=80 -> LIKELY scaled; >=65 -> POSSIBLE; else UNLIKELY).
//
// NOTE: the WiFi password and ThingSpeak keys are baked into this firmware.
// Anyone with the .bin can read them; rotate via ThingSpeak if leaked.

#include <Arduino.h>
#include <Wire.h>
#include <DHT.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ---------- pins ----------
#define DHT_PIN   PB8
#define DHT_TYPE  DHT11
#define RAIN_DO   PB9          // LOW = rain detected (typical module)
#define RAIN_AO   PA0          // analog rain intensity (optional)
#define LED_PIN   PC13

// ---------- OLED ----------
#define SCREEN_W 128
#define SCREEN_H 64
Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);

// ---------- ESP8266 / ThingSpeak ----------
#define ESP_BAUD 9600  // must match STM_BAUD on the ESP8266 (SW-serial+WiFi)
#define WIFI_SSID "nickel"
#define WIFI_PASS "acoustic@bun"
#define TS_HOST "api.thingspeak.com"
#define TS_PORT 80
#define TS_CHANNEL_ID 3530859UL
#define TS_WRITE_KEY "RFYKTNB5YD5O96SF"
#define TS_READ_KEY "MZHJULIRGNHOSU8H"
#define PUSH_EVERY_MS 180000UL    // 3 min (free tier: >=15 s; 1-5 min is plenty)
#define FETCH_EVERY_MS 300000UL   // 5 min ML-forecast poll
#define ESP_FRESH_MS 900000UL     // ML value counts as fresh for 15 min
#define JOIN_RETRY_MS 30000UL

// ---------- state ----------
DHT dht(DHT_PIN, DHT_TYPE);
float g_temp = NAN, g_hum = NAN;
bool g_rain = false;
int g_rainAo = 0;
uint32_t g_lastSensorMs = 0;
uint32_t g_lastScreenMs = 0;
int g_screen = 0;              // 0 readings, 1 rain status, 2 forecast
int g_espProb = -1;            // last ML probability via ESP, -1 = none
uint32_t g_espProbMs = 0;
bool g_netUp = false;          // WiFi joined
uint32_t g_lastPushMs = 0;     // (starts 0 -> first push soon after join)
uint32_t g_lastFetchMs = 0;
bool g_pushDoneOnce = false;

// ---------- AT engine ----------
enum EspState {
  E_BOOT,        // ATE0 -> OK
  E_MODE,        // CWMODE=1 -> OK
  E_MUX,         // CIPMUX=0 -> OK (single connection)
  E_JOIN,        // CWJAP -> OK (25 s timeout, JOIN_RETRY_MS backoff)
  E_IDLE,        // schedule push/fetch jobs
  E_CLOSE,       // CIPCLOSE (ignore result) -> then open
  E_START,       // CIPSTART -> CONNECT/OK (15 s)
  E_SEND,        // CIPSEND=len -> '>' (5 s)
  E_DATA,        // GET bytes -> SEND OK (12 s), +IPD captured for fetch
};
static EspState espState = E_BOOT;
static uint32_t espStateMs = 0;
static uint32_t espRetryMs = 0;
static bool espJobIsPush = true;  // E_CLOSE..E_DATA serve push or fetch
static String espBuf = "";
static char espGet[200];
static int espGetLen = 0;

static void espEnter(EspState s) {
  espState = s;
  espStateMs = millis();
  espBuf = "";
}

static bool espTake(const char *token) {
  int i = espBuf.indexOf(token);
  if (i < 0) return false;
  espBuf.remove(0, i + strlen(token));
  return true;
}

// +IPD,<len>:<payload...> — payload may still be arriving; returns true once
// the full payload is in hand (payload copied out, buffer consumed).
static bool espTakeIpd(char *out, int outSize, int *outLen) {
  int i = espBuf.indexOf("+IPD,");
  if (i < 0) return false;
  int colon = espBuf.indexOf(':', i);
  if (colon < 0) return false;
  int len = espBuf.substring(i + 5, colon).toInt();
  if (len < 0 || len > 2048) { espBuf.remove(0, colon + 1); return false; }
  if ((int)espBuf.length() < colon + 1 + len) return false;  // still arriving
  int n = len < outSize - 1 ? len : outSize - 1;
  memcpy(out, espBuf.c_str() + colon + 1, n);
  out[n] = '\0';
  *outLen = n;
  espBuf.remove(0, colon + 1 + len);
  return true;
}

static void buildPushGet() {
  // NOTE: no %f here — newlib-nano snprintf prints floats as empty (see
  // MILESTONE_WEATHER_V1.md). Format the decimal by hand.
  int t10 = isnan(g_temp) ? -999 : (int)(g_temp * 10.0f);
  char tstr[10];
  if (t10 == -999) snprintf(tstr, sizeof(tstr), "-99.9");
  else snprintf(tstr, sizeof(tstr), "%d.%d", t10 / 10, abs(t10 % 10));
  snprintf(espGet, sizeof(espGet),
           "GET /update?api_key=%s&field1=%s&field2=%d&field3=%d "
           "HTTP/1.1\r\nHost: %s\r\n\r\n",
           TS_WRITE_KEY, tstr,
           isnan(g_hum) ? -1 : (int)g_hum,
           g_rain ? 1 : 0);
  espGetLen = strlen(espGet);
}

static void buildFetchGet() {
  snprintf(espGet, sizeof(espGet),
           "GET /channels/%lu/fields/5/last.txt?api_key=%s "
           "HTTP/1.1\r\nHost: %s\r\n\r\n",
           (unsigned long)TS_CHANNEL_ID, TS_READ_KEY, TS_HOST);
  espGetLen = strlen(espGet);
}

static void espPump() {
  while (Serial1.available()) {
    char c = (char)Serial1.read();
    espBuf += c;
    if (espBuf.length() > 900) espBuf.remove(0, espBuf.length() - 900);
  }
  // Unsolicited disconnect notice is honored in every state.
  if (espBuf.indexOf("WIFI DISCONNECT") >= 0) {
    g_netUp = false;
  }

  uint32_t now = millis();
  switch (espState) {
    case E_BOOT:
      if (espTake("OK")) {
        Serial1.print("ATE0\r\n");
        espEnter(E_MODE);
      } else if (now - espStateMs > 3000UL) {
        Serial1.print("AT\r\n");
        espEnter(E_BOOT);
      }
      break;

    case E_MODE:
      if (espTake("OK")) {
        Serial1.print("AT+CIPMUX=0\r\n");
        espEnter(E_MUX);
      } else if (now - espStateMs > 5000UL) {
        Serial1.print("AT+CWMODE=1\r\n");
        espEnter(E_MODE);
      }
      break;

    case E_MUX:
      if (espTake("OK")) {
        Serial1.print("AT+CWJAP=\"");
        Serial1.print(WIFI_SSID);
        Serial1.print("\",\"");
        Serial1.print(WIFI_PASS);
        Serial1.print("\"\r\n");
        espEnter(E_JOIN);
      } else if (espTake("ERROR") || now - espStateMs > 5000UL) {
        espEnter(E_MODE);  // retry from mode set
      }
      break;

    case E_JOIN:
      if (espTake("OK")) {
        g_netUp = true;
        espRetryMs = 0;
        espEnter(E_IDLE);
      } else if (espTake("FAIL") || espTake("ERROR") ||
                 now - espStateMs > 25000UL) {
        g_netUp = false;
        espRetryMs = now;  // back off before rejoin
        espEnter(E_IDLE);
      }
      break;

    case E_IDLE: {
      if (!g_netUp) {
        if (espRetryMs == 0 || now - espRetryMs > JOIN_RETRY_MS) {
          Serial1.print("AT+CWJAP=\"");
          Serial1.print(WIFI_SSID);
          Serial1.print("\",\"");
          Serial1.print(WIFI_PASS);
          Serial1.print("\"\r\n");
          espEnter(E_JOIN);
        }
        break;
      }
      bool pushDue = isnan(g_temp) == false &&
                     (g_pushDoneOnce == false || now - g_lastPushMs > PUSH_EVERY_MS);
      bool fetchDue = g_pushDoneOnce && (now - g_lastFetchMs > FETCH_EVERY_MS);
      if (pushDue) {
        espJobIsPush = true;
        buildPushGet();
        Serial1.print("AT+CIPCLOSE\r\n");
        espEnter(E_CLOSE);
      } else if (fetchDue) {
        espJobIsPush = false;
        buildFetchGet();
        Serial1.print("AT+CIPCLOSE\r\n");
        espEnter(E_CLOSE);
      }
      break;
    }

    case E_CLOSE:
      // CIPCLOSE result ignored (ERROR just means nothing was open).
      if (now - espStateMs > 1500UL) {
        Serial1.printf("AT+CIPSTART=\"TCP\",\"%s\",%d\r\n", TS_HOST, TS_PORT);
        espEnter(E_START);
      }
      break;

    case E_START:
      if (espTake("CONNECT") || espTake("ALREADY")) {
        Serial1.printf("AT+CIPSEND=%d\r\n", espGetLen);
        espEnter(E_SEND);
      } else if (espTake("ERROR") || espTake("FAIL") || espTake("CLOSED") ||
                 now - espStateMs > 15000UL) {
        espEnter(E_IDLE);
      }
      break;

    case E_SEND:
      if (espTake(">")) {
        Serial1.print(espGet);
        espEnter(E_DATA);
      } else if (espTake("ERROR") || now - espStateMs > 5000UL) {
        espEnter(E_IDLE);
      }
      break;

    case E_DATA: {
      char ipd[96];
      int ipdLen = 0;
      if (!espJobIsPush && espTakeIpd(ipd, sizeof(ipd), &ipdLen)) {
        String s(ipd);
        s.trim();
        int p = s.toInt();
        // last.txt is "" until the laptop writes field5; "0" is a real forecast.
        if (s.length() > 0 && p >= 0 && p <= 100) {
          g_espProb = p;
          g_espProbMs = millis();
        }
      }
      if (espTake("SEND OK")) {
        if (espJobIsPush) {
          g_lastPushMs = millis();
          g_pushDoneOnce = true;
        } else {
          g_lastFetchMs = millis();
        }
        espEnter(E_IDLE);
      } else if (espTake("ERROR") || now - espStateMs > 12000UL) {
        if (espJobIsPush) g_lastPushMs = millis();  // don't hammer on failure
        else g_lastFetchMs = millis();
        g_pushDoneOnce = true;
        espEnter(E_IDLE);
      }
      break;
    }
  }
}

// Local heuristic placeholder (replaced by laptop/ML value when fresh)
int localForecast(float hum, bool rain) {
  if (rain) return 90;
  if (isnan(hum)) return 0;
  if (hum >= 85) return 80;
  if (hum >= 80) return 70;
  if (hum >= 70) return 45;
  if (hum >= 65) return 30;
  return 10;
}

const char *probLabel(int p) {
  if (p >= 60) return "LIKELY";
  if (p >= 35) return "POSSIBLE";
  return "UNLIKELY";
}

void readSensors() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (!isnan(t) && !isnan(h)) { g_temp = t; g_hum = h; }
  g_rain = (digitalRead(RAIN_DO) == LOW);
  g_rainAo = analogRead(RAIN_AO);
}

void drawCentered(const char *s, int y, int size) {
  display.setTextSize(size);
  int16_t x1, y1; uint16_t w, h;
  display.getTextBounds(s, 0, 0, &x1, &y1, &w, &h);
  display.setCursor(max(0, (SCREEN_W - (int)w) / 2), y);
  display.print(s);
}

void drawScreens() {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  char buf[32];

  if (g_screen == 0) {
    drawCentered("WEATHER STATION", 2, 1);
    display.drawLine(0, 12, 127, 12, SSD1306_WHITE);
    // size 2 = 12 px/char: keep every row <= 10 chars (120 px), left at x=4.
    display.setTextSize(2);
    display.setCursor(4, 18);
    if (isnan(g_temp)) display.print("T:--.-C");
    else { display.print("T:"); display.print(g_temp, 1); display.print("C"); }
    display.setCursor(4, 40);
    if (isnan(g_hum)) display.print("H:--%");
    else { snprintf(buf, sizeof(buf), "H:%d%%", (int)g_hum); display.print(buf); }
  } else if (g_screen == 1) {
    drawCentered("RAIN STATUS", 2, 1);
    display.drawLine(0, 12, 127, 12, SSD1306_WHITE);
    // "RAIN DETECTED" at size 2 is 156 px -> overflows; split sizes instead.
    if (g_rain) drawCentered("RAIN!", 20, 3);
    else drawCentered("NO RAIN", 26, 2);
    display.setTextSize(1);
    snprintf(buf, sizeof(buf), "analog: %d", g_rainAo);
    int16_t x1, y1; uint16_t w, h;
    display.getTextBounds(buf, 0, 0, &x1, &y1, &w, &h);
    display.setCursor((SCREEN_W - w) / 2, 54);
    display.print(buf);
  } else {
    drawCentered("RAIN FORECAST", 2, 1);
    display.drawLine(0, 12, 127, 12, SSD1306_WHITE);
    bool useEsp = (g_espProb >= 0 && (millis() - g_espProbMs) < ESP_FRESH_MS);
    int p = useEsp ? g_espProb : localForecast(g_hum, g_rain);
    display.setTextSize(2);
    display.setCursor(4, 18);
    display.print(probLabel(p));  // longest "POSSIBLE" = 96 px, fits
    display.setCursor(4, 40);
    snprintf(buf, sizeof(buf), "%d %%", p);
    display.print(buf);
    // right-align source tag so SRC:LOCAL (54 px) never runs off-edge
    const char *src = useEsp ? "SRC:ESP" : "SRC:LOCAL";
    display.setTextSize(1);
    int16_t sx1, sy1; uint16_t sw, sh;
    display.getTextBounds(src, 0, 0, &sx1, &sy1, &sw, &sh);
    display.setCursor(SCREEN_W - sw - 2, 54);
    display.print(src);
  }
  display.display();
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  pinMode(RAIN_DO, INPUT);
  // PB6/PB7 I2C1
  Wire.setSDA(PB7);
  Wire.setSCL(PB6);
  Wire.begin();
  dht.begin();
  Serial1.begin(ESP_BAUD);   // PA9 TX / PA10 RX -> ESP8266 (D5/D6 sw-serial)
  espBuf.reserve(960);

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    while (1) {  // OLED missing: fast blink distress signal
      digitalWrite(LED_PIN, LOW); delay(150);
      digitalWrite(LED_PIN, HIGH); delay(150);
    }
  }
  display.clearDisplay();
  drawCentered("WEATHER", 8, 2);
  drawCentered("STATION", 30, 2);
  display.display();
  delay(1200);
  readSensors();
  // Telemetry mode: the ESP8266 owns WiFi/cloud. We only emit T:... lines
  // (5 s block in loop()) and accept F:<0-100> forecast lines back.
  // (AT engine further down retired, kept for reference.)
}

// Forecast lines from the ESP ("F:<0-100>"); anything else ignored.
void pollEsp() {
  static char line[32];
  static uint8_t n = 0;
  while (Serial1.available()) {
    char c = (char)Serial1.read();
    if (c == '\n' || c == '\r') {
      line[n] = '\0';
      int p = -1;
      if (n > 2 && (line[0] == 'F' || line[0] == 'f') &&
          sscanf(line, "F:%d", &p) == 1 && p >= 0 && p <= 100) {
        g_espProb = p;
        g_espProbMs = millis();
      }
      n = 0;
    } else if (n < sizeof(line) - 1) {
      line[n++] = c;
    }
  }
}

void loop() {
  static uint32_t ledMs = 0;
  static bool ledOn = false;
  uint32_t now = millis();
  if (now - g_lastSensorMs >= 2000UL) { g_lastSensorMs = now; readSensors(); }
  // Plain telemetry for bench monitoring (e.g. ESP8266 bridge on D5/D6):
  // parsed as TELE, harmless to the AT engine (no OK/ERROR/CONNECT tokens).
  static uint32_t teleMs = 0;
  if (now - teleMs >= 5000UL) {
    teleMs = now;
    if (!isnan(g_temp)) {
      // NOTE: no %f in printf here — newlib-nano has no float support and
      // faults the MCU (frozen screen + dead LED). Format tenths by hand.
      int t10 = (int)(g_temp * 10.0f);
      char tstr[10];
      snprintf(tstr, sizeof(tstr), "%d.%d", t10 / 10, abs(t10 % 10));
      Serial1.printf("T:%s,H:%d,R:%d\n", tstr, (int)g_hum, g_rain ? 1 : 0);
    }
  }
  pollEsp();  // ESP forecast lines (F:<0-100>), drains Serial1 every iteration
  if (now - g_lastScreenMs >= 3000UL) {
    g_lastScreenMs = now;
    g_screen = (g_screen + 1) % 3;
    drawScreens();
  }
  // heartbeat: slow once sensors read, fast if they never do
  uint32_t period = !isnan(g_temp) ? 1500UL : 300UL;
  if (now - ledMs >= period) {
    ledMs = now;
    ledOn = !ledOn;
    digitalWrite(LED_PIN, ledOn ? LOW : HIGH);  // PC13 LED is active-low
  }
}
