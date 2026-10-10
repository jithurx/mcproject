// Smart Weather Station — STM32F103C8T6 Blue Pill
// Sensors: DHT11 (temp/humidity) + rain sensor (digital DO, analog AO optional)
// Display: 128x64 SSD1306 OLED (I2C1: SCL=PB6, SDA=PB7, addr 0x3C)
// Uplink (ESP8266 attaches later on USART1: TX=PA9, RX=PA10 @115200):
//   STM32 -> ESP : "T:28.4,H:82,R:0\n" every 5s  (R: 1 = rain detected)
//   ESP -> STM32 : "F:78\n"  (rain probability 0-100 from ML/cloud; optional)
// Forecast shown = ESP value when fresh (<60s), else local heuristic placeholder
// (rain now -> LIKELY 90; humidity>=80 -> LIKELY scaled; >=65 -> POSSIBLE; else UNLIKELY).

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

// ---------- ESP link ----------
#define ESP_BAUD 115200

// ---------- state ----------
DHT dht(DHT_PIN, DHT_TYPE);
float g_temp = NAN, g_hum = NAN;
bool g_rain = false;
int g_rainAo = 0;
uint32_t g_lastSensorMs = 0;
uint32_t g_lastScreenMs = 0;
uint32_t g_lastTxMs = 0;
int g_screen = 0;              // 0 readings, 1 rain status, 2 forecast
int g_espProb = -1;            // last probability from ESP8266, -1 = none
uint32_t g_espProbMs = 0;
String g_rxLine = "";

// Local heuristic placeholder (replaced by ESP/ML value when fresh)
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

void pollEsp() {
  while (Serial1.available()) {
    char c = (char)Serial1.read();
    if (c == '\n' || c == '\r') {
      g_rxLine.trim();
      // expect "F:<0-100>"
      if (g_rxLine.length() > 2 && (g_rxLine[0] == 'F' || g_rxLine[0] == 'f') && g_rxLine[1] == ':') {
        int p = g_rxLine.substring(2).toInt();
        if (p >= 0 && p <= 100) { g_espProb = p; g_espProbMs = millis(); }
      }
      g_rxLine = "";
    } else if (g_rxLine.length() < 24) {
      g_rxLine += c;
    }
  }
}

void sendTelemetry() {
  Serial1.print("T:");
  Serial1.print(isnan(g_temp) ? -99.9 : g_temp, 1);
  Serial1.print(",H:");
  Serial1.print(isnan(g_hum) ? -1 : (int)g_hum);
  Serial1.print(",R:");
  Serial1.println(g_rain ? 1 : 0);
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
    display.setTextSize(2);
    display.setCursor(4, 18);
    if (isnan(g_temp)) display.print("Temp: --.-C");
    else { display.print("Temp: "); display.print(g_temp, 1); display.print("C"); }
    display.setCursor(4, 38);
    if (isnan(g_hum)) display.print("Hum:  -- %");
    else { snprintf(buf, sizeof(buf), "Hum:  %2d %%", (int)g_hum); display.print(buf); }
  } else if (g_screen == 1) {
    drawCentered("RAIN STATUS", 2, 1);
    display.drawLine(0, 12, 127, 12, SSD1306_WHITE);
    if (g_rain) drawCentered("RAIN DETECTED", 26, 2);
    else drawCentered("NO RAIN", 26, 2);
    display.setTextSize(1);
    snprintf(buf, sizeof(buf), "analog: %d", g_rainAo);
    int16_t x1, y1; uint16_t w, h;
    display.getTextBounds(buf, 0, 0, &x1, &y1, &w, &h);
    display.setCursor((SCREEN_W - w) / 2, 52);
    display.print(buf);
  } else {
    drawCentered("RAIN FORECAST", 2, 1);
    display.drawLine(0, 12, 127, 12, SSD1306_WHITE);
    bool useEsp = (g_espProb >= 0 && (millis() - g_espProbMs) < 60000UL);
    int p = useEsp ? g_espProb : localForecast(g_hum, g_rain);
    display.setTextSize(2);
    display.setCursor(4, 18);
    display.print(probLabel(p));
    display.setCursor(4, 38);
    snprintf(buf, sizeof(buf), "%d %%", p);
    display.print(buf);
    display.setTextSize(1);
    display.setCursor(80, 40);
    display.print(useEsp ? "SRC:ESP" : "SRC:LOCAL");
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
  Serial1.begin(ESP_BAUD);   // PA9 TX / PA10 RX -> ESP8266 (attach later)

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
}

void loop() {
  uint32_t now = millis();
  if (now - g_lastSensorMs >= 2000UL) { g_lastSensorMs = now; readSensors(); }
  pollEsp();
  if (now - g_lastTxMs >= 5000UL) { g_lastTxMs = now; sendTelemetry(); }
  if (now - g_lastScreenMs >= 3000UL) {
    g_lastScreenMs = now;
    g_screen = (g_screen + 1) % 3;
    drawScreens();
  }
  // heartbeat
  digitalWrite(LED_PIN, LOW); delay(60);
  digitalWrite(LED_PIN, HIGH); delay(120);
}
