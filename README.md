# Smart Weather Station — Blue Pill + ESP8266 uplink owner

STM32F103 "Blue Pill" weather station: DHT11 + rain sensor → 128×64 SSD1306
OLED UI → 5 s telemetry to a NodeMCU that owns WiFi + ThingSpeak cloud
upload. Flashed and debugged over SWD with an ESP32 probe.

## Repo layout

```
.
├── README.md
├── docs/                        # board, wiring, milestones, session notes
│   ├── ESP_UPLINK.md            # current architecture (STM32 senses, ESP networks)
│   ├── MILESTONE_UPLINK.md      # end-to-end sign-off (TELE + TS entry=5)
│   ├── THINGSPEAK_AT.md         # SUPERSEDED stock-AT path (kept for reference)
│   ├── BOARD_DETAILS.md         # target identity, SWD wiring, probe patches
│   ├── MILESTONE_OLED.md        # "hi" on OLED — first VERIFY PASS
│   ├── MILESTONE_WEATHER_V1.md  # weather-station v1 sign-off
│   └── SESSION_CONTEXT.md       # full debug context / next steps
├── firmware/
│   ├── STM32_Weather/           # weather station: sensors + OLED + T: telemetry
│   ├── ESP8266_Bridge/          # esp-uplink v1: WiFi + ThingSpeak + PC monitor
│   └── STM32_OLED/              # SSD1306 "hi" demo (first milestone)
├── legacy/                      # superseded paths (kept for reference)
│   ├── ESP8266_Link/            # custom ESP firmware v1 (ancestor of esp-uplink)
│   ├── STM32Bridge/             # ESP32 UART-bootloader bridge (dead end)
│   └── STM32Prober/             # ESP32 UART bootloader prober (dead end)
├── probe/
│   └── ESP32SWDProbe/           # ESP32-as-SWD-probe (from AVATAR3905/esp32-stlink-probe)
├── third-party/
│   ├── esp8266-at/              # retired stock-AT binaries (reference only)
│   └── stm32flash/              # vendored UART ROM tool (never got ACK here)
└── tools/                       # chunked-SWD flasher + ML forecast loop
```

## Hardware

| Part | Wiring |
|---|---|
| SWD probe | ESP32 GPIO21→DIO/PA13, GPIO22→CLK/PA14 (small debug header), GPIO19→RST, GND→GND. Blue Pill on its **own** USB (3V3 sags under flash charge-pump → `-4 WAIT`). |
| OLED | VCC→3V3, GND→GND, SCL→PB6, SDA→PB7 (I2C1, addr `0x3C`) |
| DHT11 | VCC→3V3, GND→GND, DATA→PB8 + 4.7k–10k pull-up |
| Rain | VCC→3V3, GND→GND, DO→PB9 (LOW = rain), AO→PA0 |
| ESP8266 uplink | NodeMCU D5←PA9, D6→PA10 **@9600**, GND→GND. UART0 stays on PC USB: flashing + monitoring with STM32 connected. Each board on its **own** USB. |

Full details: `docs/ESP_UPLINK.md`, `docs/BOARD_DETAILS.md`.

## Quickstart

**1. Flash the ESP (anytime, no unplugging):**
```bash
cd firmware/ESP8266_Bridge
pio run -t upload && pio device monitor -b 115200
# expect: esp-uplink v1 ... wifi: up ip=...
```

**2. Verify the SWD link / halt-first before bulk ops:**
```bash
python probe/ESP32SWDProbe/tests/part_id_check.py /dev/ttyUSB0
# expect: cpuid partno=0xc23 ... RESULT: PASS
python probe/ESP32SWDProbe/probe_tools/introspect.py /dev/ttyUSB0 --halt
```

**3. Build + flash STM32 over SWD** (slow link — chunked flasher, halt first):
```bash
cd firmware/STM32_Weather && pio run   # → .pio/build/bluepill_f103c8/firmware.bin
python tools/chunk_flash.py firmware/STM32_Weather/.pio/build/bluepill_f103c8/firmware.bin
# skips identical pages, verifies, ends with reset+resume so it runs
```

**4. Watch it live:** NodeMCU USB monitor shows `TELE T=.. H=.. R=..`
every 5 s and `TS: entry=<id>` pushes every 3 min.

## Protocol (STM32 PA9/D5 @9600, text lines)

STM32 emits `T:<temp>,H:<hum>,R:<0|1>` (tenths hand-formatted — `%f`
hard-faults newlib-nano); ESP parses to `TELE`, pushes
`GET /update?api_key=<WRITE>&field1=T&field2=H&field3=R` to ThingSpeak
(entry id from response body), reserves `F:<0-100>` forecast replies for
screen 3 (`SRC:ESP` when fresh, else local heuristic `SRC:LOCAL`).
Details: `docs/ESP_UPLINK.md`.

## Status / roadmap

- [x] OLED "hi" milestone (`docs/MILESTONE_OLED.md`)
- [x] Weather v1 on hardware (`docs/MILESTONE_WEATHER_V1.md`)
- [x] ESP uplink end-to-end (`docs/MILESTONE_UPLINK.md`): TELE @5 s,
  WiFi up, `TS: entry=5`, 3-screen OLED, no overlaps
- [ ] `F:<prob>` forecast fetch → screen 3 `SRC:ESP`
- [ ] `tools/ml_forecast.py loop` → field5 (dormant: nothing reads field5 yet)
- [ ] Optional: FreeRTOS, pressure sensor, rain gauge, mobile app

## Attribution

Probe firmware derives from
[AVATAR3905/esp32-stlink-probe](https://github.com/AVATAR3905/esp32-stlink-probe)
(see `probe/ESP32SWDProbe/LICENSE`); CKS32 patches (`DPIDR 0x2BA01477`,
`swd_delay_cycles`, WAIT/flash timeouts) are documented in
`docs/BOARD_DETAILS.md`. `third-party/stm32flash` is vendored upstream.
