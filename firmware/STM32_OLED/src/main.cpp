#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

void setup() {
  pinMode(PC13, OUTPUT);
  // Blue Pill I2C1: SDA=PB7, SCL=PB6
  Wire.setSDA(PB7);
  Wire.setSCL(PB6);
  Wire.begin();

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    // OLED not found: blink fast to signal wiring/address issue
    while (1) {
      digitalWrite(PC13, LOW);
      delay(150);
      digitalWrite(PC13, HIGH);
      delay(150);
    }
  }
  display.clearDisplay();
  display.setTextSize(4);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(30, 18);
  display.println(F("hi"));
  display.display();
}

void loop() {
  // heartbeat: slow PC13 blink while message stays on OLED
  digitalWrite(PC13, LOW);
  delay(800);
  digitalWrite(PC13, HIGH);
  delay(800);
}
