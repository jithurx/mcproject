# ESP8266 uplink owner (WiFi + cloud; STM32 only senses)

NodeMCU firmware: STM32 sends `T:temp,H:hum,R:rain` on SoftwareSerial
(D5/D6 @9600); the ESP joins WiFi, pushes to ThingSpeak, and mirrors
everything to the PC monitor. HW Serial (UART0) stays on PC USB — flashing
works with the STM32 connected.

## Wiring

| NodeMCU | ↔ | Blue Pill |
|---|---|---|
| D5 / GPIO14 (RX) | ← | PA9 (TX) @9600 |
| D6 / GPIO12 (TX) | → | PA10 (RX, reserved for `F:` replies) |
| GND | ↔ | GND (only common wire, no 3V3) |

Each board on its own USB. D5/D6 are boot-safe (not strapping pins).

## Flash / monitor

```bash
cd firmware/ESP8266_Bridge
pio run -t upload   # /dev/ttyUSB0, works with STM32 connected
pio device monitor -b 115200   # PC side stays 115200; STM32 link is 9600
```

Expect: `esp-uplink v1 ...` + `wifi: up ip=... rssi=...`. Auto-`status`
every 60 s; type `status`/`?` anytime. LED: slow blink = WiFi + fresh
telemetry, fast = problem.

## Behavior

* `TELE T=.. H=.. R=..` per STM32 line; anything else → `STM: <raw>`.
* ThingSpeak push every 3 min when telemetry is fresh (<60 s):
  `GET /update?api_key=<WRITE>&field1=T&field2=H&field3=R`,
  entry id parsed from the response **body** (last line), `0` = rejected.
* Anything typed on the PC (except `status`/`?`) is forwarded raw to STM32.
* `F:<0-100>` replies to STM32 are reserved for a future forecast fetch
  (screen 3 shows `SRC:LOCAL` until then).

## Hard lessons (do not regress)

* **SoftwareSerial + WiFi only coexists at low baud.** `@115200` the
  bit-bang starves WiFi interrupts → endless `Soft WDT reset, Exception
  (4)` crash loop. `@9600` is stable. Both sides must match (`STM_BAUD`
  here, `ESP_BAUD` in `firmware/STM32_Weather/src/main.cpp`).
* **UART0 is PC-only.** The old stock-AT wiring (`RX/GPIO3←PA9`) put the
  STM32 and the CP2102 on the same net: flashing fought, and monitoring
  was impossible while wired. See `docs/THINGSPEAK_AT.md` (retired).

## STM32 side

`firmware/STM32_Weather` runs telemetry mode: sensors + OLED unchanged,
`T:<tenth>,H:<hum>,R:<0|1>` every 5 s on `Serial1 @9600` (tenths
hand-formatted — `%f` hard-faults newlib-nano, see `docs/SESSION_CONTEXT.md`),
`F:<0-100>` parsed back for screen 3. AT engine retired (kept in-source
for reference).

## Secrets

WiFi password + ThingSpeak write key are baked into `src/main.cpp`
(physical-access risk accepted). Rotate the ThingSpeak key if the repo
goes public.
