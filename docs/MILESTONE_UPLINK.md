# Milestone — ESP uplink end-to-end (2026-10-11)

STM32 senses, ESP8266 networks. Live sensor data reaches the PC monitor
every 5 s and ThingSpeak accepts pushes. Verified on hardware.

## Result

* ESP8266 (`firmware/ESP8266_Bridge`, `esp-uplink v1`): `wifi: up
  ip=10.150.128.21 rssi=-40..-54` on `nickel`.
* `TELE T=28.6 H=70 R=0` every 5.0 s on the monitor; finger on DHT11 moved
  T 28.6→30.8 / H 70→95 live; rain grid flipped `R:0→1`.
* `TS: entry=5` — ThingSpeak push accepted (entry 5). Push cadence 3 min,
  fresh-data gate <60 s.
* Blue Pill (`firmware/STM32_Weather`, 46,700 B, vector `0x08008469`,
  VERIFY PASS): OLED cycles all 3 screens / 3 s, slow-blink LED both ends.
* OLED layout re-measured for 128 px: `T:28.4C`/`H:82%` rows (old
  `Temp: 28.4C` was 132 px and wrapped `C` into humidity), `RAIN!` size 3
  (old `RAIN DETECTED` was 156 px), right-aligned `SRC:*` tag (old x=80 ran
  off-edge).

## Bugs killed this round

1. **`%f` printf hard-fault (STM32).** Telemetry used
   `Serial1.printf("T:%.1f...")` — newlib-nano has no float printf; MCU
   faulted ~5 s after boot (display frozen on RAIN STATUS, LED dead).
   Fixed with hand-formatted tenths (`%d.%d`), same as the old push code.
   Rule stands extended: **no `%f` in `snprintf` *or* `printf`** on this
   target (`Print::print(float, n)` is fine — own formatter).
2. **SoftwareSerial@115200 + WiFi Soft WDT crash loop (ESP8266).**
   Endless `Soft WDT reset, Exception (4)` every ~8 s; bit-bang starves
   WiFi interrupts. Fixed at `@9600` both sides, zero crashes since.
3. **ThingSpeak entry-id parse (ESP8266).** First cut read the status line
   (`entry=HTTP/1.1 200 OK`), then an empty body on close. Fixed: read
   until close (`connected() || available()`), take last body line.
4. **SWD bulk reads vs running core.** Pre-read/verify `-4 WAIT` while
   running; halt-first (`introspect.py --halt`) fixes it. Piecemeal 8 KB
   dumps + byte-compare as verify fallback; `reset + resume` after.

## Wiring (working)

* SWD: `ESP21→DIO/PA13, ESP22→CLK/PA14, ESP19→RST, GND→GND` (probe).
* Uplink: `NodeMCU D5←PA9, D6→PA10 @9600, GND↔GND`. UART0 untouched
  (flash + monitor with STM32 connected).
* Sensors: DHT11 `PB8` + pull-up, rain `DO→PB9/AO→PA0`, OLED I2C1
  `PB6/PB7 @0x3C`. Each board on its own USB.

## Sign-off

- [x] Live `TELE` on PC monitor @5 s cadence
- [x] WiFi join + `TS: entry=5` on ThingSpeak ch 3530859
- [x] OLED 3-screen cycle, no overlaps, slow-blink heartbeat
- [ ] `F:<prob>` forecast fetch → screen 3 `SRC:ESP` (reserved)
- [ ] `tools/ml_forecast.py loop` → field5 (dormant: nothing reads field5 yet)
