#include <Arduino.h>

// STM32 deep prober - runs ON the ESP32, talks directly to STM32 via UART2.
// USB log at 115200 8N1. UART2 cycles all bootloader bauds, forces bootloader
// via BOOT0/NRST, sends 0x7F, reports ANY reply (looking for 0x79 ACK).
// Wiring needed for full test:
//   ESP16 <- PA9, ESP17 -> PA10, ESP5 -> BOOT0, ESP19 -> BOOT1/PB2,
//   ESP18 -> NRST, GND common. REMOVE B0/B1 jumpers so ESP drives alone.

static const int STM32_RX = 16;
static const int STM32_TX = 17;
static const int BOOT0_PIN = 5;
static const int BOOT1_PIN = 19;
static const int NRST_PIN = 18;

static const long BAUDS[] = {9600, 19200, 38400, 57600, 115200};
static const int NBAUDS = sizeof(BAUDS) / sizeof(BAUDS[0]);

void forceBootloader(int b0, int b1) {
  digitalWrite(BOOT0_PIN, b0 ? HIGH : LOW);
  digitalWrite(BOOT1_PIN, b1 ? HIGH : LOW);
  digitalWrite(NRST_PIN, LOW);
  delay(150);
  digitalWrite(NRST_PIN, HIGH);
  delay(200);
}

void senseBootPins(const char *tag) {
  // BOOT0/BOOT1 are outputs when ESP drives them; this reports what
  // ESP is driving. Real check: meter Blue Pill B0/B1 pins directly.
  Serial.printf("%s ESP drives BOOT0=%d BOOT1=%d NRST=HIGH\n", tag,
                digitalRead(BOOT0_PIN), digitalRead(BOOT1_PIN));
}

void probeOnce(long baud) {
  // Float/pull-up sense BEFORE attaching UART (never pinMode the RX pin
  // while Serial2 owns it - that detaches the UART and bootloops).
  Serial2.end();
  delay(30);
  pinMode(STM32_RX, INPUT);
  delay(10);
  int idle = digitalRead(STM32_RX);
  pinMode(STM32_RX, INPUT_PULLUP);
  delay(20);
  int idlePu = digitalRead(STM32_RX);
  pinMode(STM32_RX, INPUT);
  Serial.printf("[baud %ld] RX idle=%d (expect 1) PULLUP=%d (1=float/disconnected, 0=hard short/driven LOW)\n",
                baud, idle, idlePu);

  Serial2.begin(baud, SERIAL_8E1, STM32_RX, STM32_TX);
  Serial2.setTimeout(300);
  delay(100);
  while (Serial2.available()) Serial2.read();

  // passive listen 400ms: healthy idle bootloader sends NOTHING
  uint32_t t0 = millis();
  String spont;
  while (millis() - t0 < 400) {
    while (Serial2.available()) {
      uint8_t b = Serial2.read();
      char h[5];
      snprintf(h, sizeof(h), "%02X ", b);
      spont += h;
      if (spont.length() > 120) break;
    }
    if (spont.length() > 120) break;
  }
  if (spont.length())
    Serial.printf("[baud %ld] SPONTANEOUS %d chars: %s%s\n", baud, (int)spont.length() / 3, spont.c_str(), spont.length() > 120 ? "..." : "");
  else
    Serial.printf("[baud %ld] spontaneous: (silent, good)\n", baud);

  // send 0x7F init, wait for 0x79 ACK (or 0x1F NACK)
  Serial2.write(0x7F);
  Serial2.flush(false);
  delay(150);
  uint8_t resp[32];
  int n = 0;
  while (Serial2.available() && n < (int)sizeof(resp)) resp[n++] = Serial2.read();
  // extra window for slow clones
  if (n == 0) {
    delay(400);
    while (Serial2.available() && n < (int)sizeof(resp)) resp[n++] = Serial2.read();
  }
  if (n == 0) {
    Serial.printf("[baud %ld] TX 0x7F -> RX (nothing)\n", baud);
  } else {
    String hs;
    for (int i = 0; i < n; i++) { char h[5]; snprintf(h, sizeof(h), "%02X ", resp[i]); hs += h; }
    Serial.printf("[baud %ld] TX 0x7F -> RX %d bytes: %s", baud, n, hs.c_str());
    if (resp[0] == 0x79) Serial.printf("  <== ACK! BOOTLOADER ALIVE\n");
    else if (resp[0] == 0x1F) Serial.printf("  <== NACK (alive, resend)\n");
    else Serial.printf("  (garbage/wrong baud/floating)\n");
  }
}

void setup() {
  pinMode(BOOT0_PIN, OUTPUT);
  pinMode(BOOT1_PIN, OUTPUT);
  pinMode(NRST_PIN, OUTPUT);
  pinMode(STM32_RX, INPUT);
  digitalWrite(BOOT0_PIN, HIGH);
  digitalWrite(BOOT1_PIN, LOW);
  digitalWrite(NRST_PIN, HIGH);
  Serial.begin(115200);
  delay(500);
  Serial.println("\n==== STM32 PROBER v2 (BOOT0+BOOT1) ====");
  // Correct combo first, then others to catch clones/mislabels
  const int combos[][2] = {{1, 0}, {1, 1}, {0, 0}};
  for (int c = 0; c < 3; c++) {
    forceBootloader(combos[c][0], combos[c][1]);
    Serial.printf("==== BOOT0=%d BOOT1=%d + NRST pulse ====\n", combos[c][0], combos[c][1]);
    for (int i = 0; i < NBAUDS; i++) probeOnce(BAUDS[i]);
  }
  Serial.println("==== pass done, repeating in 8s ====");
}

void loop() {
  static uint32_t last = 0;
  if (millis() - last > 8000) {
    last = millis();
    const int combos[][2] = {{1, 0}, {1, 1}, {0, 0}};
    for (int c = 0; c < 3; c++) {
      forceBootloader(combos[c][0], combos[c][1]);
      Serial.printf("==== BOOT0=%d BOOT1=%d + NRST pulse ====\n", combos[c][0], combos[c][1]);
      for (int i = 0; i < NBAUDS; i++) probeOnce(BAUDS[i]);
    }
    Serial.println("---- repeat done ----");
  }
}
