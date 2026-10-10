# Smart Weather Station — Blue Pill (CKS32F103C8T6) via ESP32 SWD Probe

STM32F103 "Blue Pill" weather station flashed and debugged **over SWD with an
ESP32 probe** (UART ROM bootloader was a dead end on this unit). DHT11 +
rain sensor → 128×64 SSD1306 OLED UI → USART1 telemetry stub for an ESP8266 /
ML forecast uplink.

## Repo layout

```
.
├── README.md
├── docs/                        # board, wiring, milestones, session notes
│   ├── HOW_ESP32_PROGRAMS_STM32.md  # how the ESP32 probe flashes the Blue Pill
│   ├── BOARD_DETAILS.md         # target identity, SWD wiring, probe patches
│   ├── MILESTONE_OLED.md        # "hi" on OLED — first VERIFY PASS
│   ├── MILESTONE_WEATHER_V1.md  # weather-station v1 sign-off
│   └── SESSION_CONTEXT.md       # full debug context / next steps
├── firmware/
│   ├── STM32_OLED/              # SSD1306 "hi" demo (PlatformIO, ststm32+Arduino)
│   ├── STM32_Weather/           # weather station v1 (DHT11 + rain + OLED + ESP link)
│   └── ESP8266_Link/            # NodeMCU uplink: telemetry → ThingSpeak + F:<prob> back
│       ├── src/main.cpp         # parser, trend forecast, ThingSpeak, /status page
│       └── include/             # config.h (gitignored secrets) + config.h.example
├── probe/
│   └── ESP32SWDProbe/           # ESP32-as-SWD-probe (from AVATAR3905/esp32-stlink-probe)
│       ├── pio_probe/           # PlatformIO wrapper (SERIAL_ONLY, CKS32 patches)
│       ├── esp32_swd_probe/     # upstream Arduino sketch (reference)
│       ├── probe_tools/         # flash_cli / dump_restore / introspect (+ probe_client)
│       └── tests/               # one-off diag scripts (see probe_tools first)
├── legacy/                      # superseded UART path (kept for reference)
│   ├── STM32Bridge/             # ESP32 UART-bootloader bridge (dead end on this unit)
│   └── STM32Prober/             # ESP32 UART bootloader prober (dead end on this unit)
├── third-party/
│   └── stm32flash/              # vendored UART ROM tool (never got ACK here)
└── tools/                       # flashing notes (chunked-SWD workflow)
```

## Hardware

| Part | Wiring |
|---|---|
| SWD probe | ESP32 GPIO21→DIO/PA13, GPIO22→CLK/PA14 (small debug header), GPIO19→RST, GND→GND. Blue Pill on its **own** USB (ESP 3V3 sags under flash charge-pump). |
| OLED | VCC→3V3, GND→GND, SCL→PB6, SDA→PB7 (I2C1, addr `0x3C`) |
| DHT11 | VCC→3V3, GND→GND, DATA→PB8 + 4.7k–10k pull-up |
| Rain | VCC→3V3, GND→GND, DO→PB9 (LOW = rain), AO→PA0 |
| ESP8266 uplink | NodeMCU RX←PA9, TX→PA10, GND→GND (each board on its **own** USB; unplug RX/TX while USB-flashing the NodeMCU) |

Full details: `docs/BOARD_DETAILS.md`.

## Quickstart

**1. Flash the ESP32 probe** (once):
```bash
cd probe/ESP32SWDProbe/pio_probe
pio run -t upload        # /dev/ttyUSB0 @115200; retry once on "No serial data received"
```

**2. Verify the SWD link:**
```bash
python probe/ESP32SWDProbe/tests/part_id_check.py /dev/ttyUSB0
# expect: cpuid partno=0xc23 ... RESULT: PASS
python probe/ESP32SWDProbe/probe_tools/introspect.py /dev/ttyUSB0
```

**3. Build firmware:**
```bash
cd firmware/STM32_Weather
pio run                  # → .pio/build/bluepill_f103c8/firmware.bin (43.8 KB)
```

**4. Flash over SWD** (slow link — chunked flasher, see `tools/README.md`):
```bash
python tools/chunk_flash.py \
  firmware/STM32_Weather/.pio/build/bluepill_f103c8/firmware.bin
# skips identical pages, verifies, ends with reset+resume so it runs
```

## Firmware protocol (ESP8266 uplink, USART1 PA9/PA10 @115200)

- STM32 out (every 5 s): `T:28.4,H:82,R:0`
- ESP in: `F:78` (rain probability 0–100, shown as `SRC:ESP`; otherwise local heuristic `SRC:LOCAL`)
- ESP8266 serves `/status` JSON on port 80; ThingSpeak fields: 1=temp, 2=hum,
  3=rain, 4=forecast, 5=temp-trend/h, 6=hum-trend/h. Secrets in
  `firmware/ESP8266_Link/include/config.h` (see `config.h.example`).

## Status / roadmap

- [x] OLED "hi" milestone (`docs/MILESTONE_OLED.md`)
- [x] Weather v1 on hardware (`docs/MILESTONE_WEATHER_V1.md`)
- [~] ESP8266 uplink firmware written + building (`firmware/ESP8266_Link/`);
  needs WiFi password + ThingSpeak key, then USB flash + wiring
- [ ] Offline ML (Decision Tree) → `F:<prob>` back to screen 3
- [ ] Optional: FreeRTOS, pressure sensor, rain gauge, mobile app

## Attribution

Probe firmware derives from
[AVATAR3905/esp32-stlink-probe](https://github.com/AVATAR3905/esp32-stlink-probe)
(see `probe/ESP32SWDProbe/LICENSE`); CKS32 patches (`DPIDR 0x2BA01477`,
`swd_delay_cycles`, WAIT/flash timeouts) are documented in
`docs/BOARD_DETAILS.md`. `third-party/stm32flash` is vendored upstream.
