#include <Arduino.h>

// ESP32 DevKit V1 - improved STM32 UART bootloader bridge
// USB Serial (UART0 via CP2102) <-> UART2 <-> STM32 USART1 (PA9/PA10)
//
// Wiring (required):
//   ESP GPIO16 (RX) <- STM32 PA9  (TX)
//   ESP GPIO17 (TX) -> STM32 PA10 (RX)
//   GND <-> GND, 3.3V shared (do NOT use 5V on PA9/PA10)
// Optional auto-bootloader wiring (recommended):
//   ESP GPIO5  -> STM32 BOOT0 (B0 pin, or BOOT0 jumper pin)
//   ESP GPIO18 -> STM32 NRST (RST pin, 3.3V logic, active low)
//   If not wired, firmware still works as transparent bridge (manual jumpers).

static const int STM32_RX = 16;
static const int STM32_TX = 17;
static const int STM32_BOOT0 = 5;    // output to STM32 BOOT0
static const int STM32_NRST = 18;    // output to STM32 NRST (active low)
static const int LED_PIN = 2;        // built-in LED for activity

static const uint32_t BAUD = 57600;  // must match: stm32flash -b <BAUD> -m 8e1

static uint8_t bufA[256];
static uint8_t bufB[256];
static uint32_t lastActivity = 0;

void forceBootloader() {
  // BOOT0=HIGH + NRST pulse -> STM32 ROM bootloader (USART1, 8E1)
  digitalWrite(STM32_BOOT0, HIGH);
  digitalWrite(STM32_NRST, LOW);
  delay(120);
  digitalWrite(STM32_NRST, HIGH);
  delay(120);  // bootloader init time
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  pinMode(STM32_BOOT0, OUTPUT);
  pinMode(STM32_NRST, OUTPUT);
  digitalWrite(STM32_BOOT0, HIGH);  // hold bootloader mode while bridging
  digitalWrite(STM32_NRST, HIGH);

  Serial.setRxBufferSize(1024);
  Serial2.setRxBufferSize(1024);
  Serial.begin(BAUD, SERIAL_8E1);
  Serial2.begin(BAUD, SERIAL_8E1, STM32_RX, STM32_TX);
  Serial.setTimeout(5);
  Serial2.setTimeout(5);

  delay(300);
  forceBootloader();

  // flush any garbage from reset
  while (Serial.available()) Serial.read();
  while (Serial2.available()) Serial2.read();
}

void loop() {
  bool active = false;

  // PC -> STM32 (bulk, preserves 8E1 framing byte-for-byte)
  size_t n = Serial.available();
  if (n) {
    if (n > sizeof(bufA)) n = sizeof(bufA);
    size_t r = Serial.readBytes(bufA, n);
    if (r) {
      Serial2.write(bufA, r);
      Serial2.flush(false);
      active = true;
    }
  }

  // STM32 -> PC
  n = Serial2.available();
  if (n) {
    if (n > sizeof(bufB)) n = sizeof(bufB);
    size_t r = Serial2.readBytes(bufB, n);
    if (r) {
      Serial.write(bufB, r);
      Serial.flush(false);
      active = true;
    }
  }

  if (active) {
    digitalWrite(LED_PIN, HIGH);
    lastActivity = millis();
  } else if (millis() - lastActivity > 60) {
    digitalWrite(LED_PIN, LOW);
  }

  if (!active) delayMicroseconds(200);
}
