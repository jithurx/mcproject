# ESP uplink architecture (current, verified 2026-10-11)

STM32 senses, ESP8266 networks. The stock-AT interlude (`THINGSPEAK_AT.md`,
retired) is gone: UART0 contention made it unflashable/unmonitorable while
wired, so the STM32 link moved to a contention-free SoftwareSerial port and
the ESP runs custom firmware that owns WiFi + cloud.

```
Blue Pill (STM32)          NodeMCU (esp-uplink)         ThingSpeak     PC monitor
─────────────────          ────────────────────         ──────────     ──────────
sensors → OLED (3 screens, 3 s cycle)
  │  sw-serial 9600 (PA9→D5, PA10←D6, GND)
  ├── "T:28.6,H:70,R:0" every 5 s ──► parse ──► TELE log ──► USB @115200
  │                                    ├── push every 3 min ──► field1..3
  │                                    │   (fresh <60 s only; entry id
  │                                    │    parsed from response body)
  │                                    └── wifi join/retry (30 s backoff)
  └── "F:<0-100>" forecast ◄── (reserved; screen 3 = SRC:LOCAL until then)
```

## Firmware

* `firmware/ESP8266_Bridge/src/main.cpp` (`esp-uplink v1`, `nodemcuv2`):
  `SoftwareSerial(D5/D6) @9600` ↔ STM32, HW `Serial @115200` ↔ PC,
  `ESP8266WiFi` STA join + `WiFiClient` push, `TELE`/`STM:`/`TS:` logs,
  60 s auto-status, `status`/`?` commands, PC→STM32 raw forwarding,
  LED slow = healthy / fast = no-WiFi-or-stale.
* `firmware/STM32_Weather/src/main.cpp` (telemetry mode, 46,700 B):
  DHT11 `PB8` + rain `PB9`/`PA0` + OLED I2C1 `PB6/PB7`; 2 s sensors,
  3 s screens, 5 s `T:` TX on `Serial1 @9600`, `F:` RX parse; AT engine
  retired in-source. OLED rows re-measured for 128 px (see milestone).

## Protocol (USART @9600, `\n`-terminated text)

| Dir | Line | Meaning |
|---|---|---|
| STM32 → ESP | `T:<t1>,H:<h>,R:<0\|1>` | telemetry (tenths hand-formatted, no `%f`) |
| ESP → STM32 | `F:<0-100>` | forecast probability (reserved) |
| ESP → PC | `TELE T=.. H=.. R=..` | parsed telemetry |
| ESP → PC | `STM: <raw>` | non-telemetry STM32 lines |
| ESP → PC | `TS: entry=<id> ...` | ThingSpeak result (`0` = rejected) |
| ESP → PC | `up=.. wifi=.. stm=.. tele_age=.. ts=..` | 60 s heartbeat / `status` |
| PC → ESP | `status`, `?` | local commands (not forwarded) |
| PC → ESP | anything else | forwarded raw to STM32 (`FWD>` echo) |

## Verification (2026-10-11, live)

* `wifi: up ip=10.150.128.21 rssi=-40..-54` — `nickel` + password confirmed.
* `TELE T=28.6 H=70 R=0` every 5.0 s; finger-on-sensor moved T/H and
  flipped `R:0→1` live.
* `TS: entry=5` — ThingSpeak accepted the push (entry 5).
* OLED cycles all 3 screens; LED slow-blink both ends.

## Retired alternative

Stock AT v1.7.6 on ESP + AT driver on STM32 (`docs/THINGSPEAK_AT.md`):
killed by UART0 contention (CP2102 vs PA9 on `RX/GPIO3` — flashing fought,
USB monitoring died while wired, commands never landed: `CWMODE:2`, `No
AP`, echo stuck on). Binaries kept in `third-party/esp8266-at/` for
reference; reflash recipe in that doc §1 if ever needed.
