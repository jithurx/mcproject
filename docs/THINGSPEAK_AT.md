# SUPERSEDED 2026-10-11 — stock-AT uplink path (kept for reference)

> Retired in favor of [`ESP_UPLINK.md`](ESP_UPLINK.md) (custom `esp-uplink`
> firmware on D5/D6 @9600, STM32 telemetry mode). Why: the AT wiring put
> STM32 `PA9` and the CP2102 on the same `RX/GPIO3` net — NodeMCU flashing
> fought the STM32, USB monitoring died while wired, and zero AT commands
> ever landed (`CWMODE:2`, `No AP`, echo stuck on). Binaries + reflash
> recipe below kept in case stock AT is ever needed.

# ThingSpeak uplink via ESP8266 AT commands + laptop ML loop

Architecture: the STM32 is the boss, the ESP8266 is a dumb WiFi modem running
**stock AT firmware v1.7.6**, and a laptop script closes the ML loop. No custom
ESP code, no server hosting.

```
Blue Pill (STM32)            NodeMCU (AT fw)           ThingSpeak        Laptop
─────────────────            ───────────────           ──────────        ──────
sensors ──► OLED
   │  UART 115200 (PA9/PA10)
   ├── AT+CWJAP ──► join nickel
   ├── AT+CIPSTART TCP api.thingspeak.com:80 ──►
   ├── AT+CIPSEND + GET /update?..&field1=T&field2=H&field3=R ──► field1..3
   │                                                              (every 3 min)
   ├── AT+CIPSEND + GET /channels/3530859/fields/5/last.txt ──►  ◄── field5
   │                    (every 5 min)                        written by laptop
   ▼                                                         every 5 min ◄──┘
OLED forecast screen: SRC:ESP (ML value <15 min old) else SRC:LOCAL
```

## 1. ESP8266 AT firmware (done 2026-10-10)

- Module: NodeMCU Amica (ESP-12E, 4 MB flash, `10c4:ea60`).
- Firmware: NonOS AT **v1.7.6** (`AT version:1.7.6.0`, SDK 3.0.6-dev).
- Hard-won flash recipe — the obvious layouts **boot-loop**:
  - `dio + 4MB` with `user1.2048.new.5.bin` → `mismatch map 5, spi_size_map 4`
  - `qio + 4MB-c1` → `mismatch map 5, spi_size_map 6`
  - ✅ `erase_flash`, then **`dio + 2MB-c1`** (wastes 2 MB, proven per
    espressif/ESP8266_NONOS_SDK#179):
    ```
    esptool.py -p /dev/ttyUSB0 -b 460800 write_flash -ff 40m -fm dio -fs 2MB-c1 \
      0x00000 boot_v1.7.bin 0x01000 user1.2048.new.5.bin \
      0x1fc000 esp_init_data_default_v08.bin \
      0xfe000 blank.bin 0x1fe000 blank.bin 0x1fb000 blank.bin
    ```
  - Verify: `AT+GMR` → `AT version:1.7.6.0 … OK`; `AT+CWMODE?` → `+CWMODE:2`.
- Binaries vendored in `third-party/esp8266-at/` (from
  `espressif/ESP8266_NONOS_SDK` master: `boot_v1.7.bin`,
  `at/1024+1024/user1.2048.new.5.bin`, `esp_init_data_default_v08.bin`,
  `blank.bin`, plus the `at4mb.img` dump that did NOT boot — kept as a
  warning, do not flash it).

## 2. STM32 AT driver (`firmware/STM32_Weather/src/main.cpp`)

- Non-blocking state machine pumped every `loop()` (no `delay()` anywhere —
  the 64-byte Serial1 buffer would overflow mid-burst otherwise):
  `BOOT(AT/ATE0) → MODE(CWMODE=1) → MUX(CIPMUX=0) → JOIN(CWJAP, 25 s timeout,
  30 s backoff) → IDLE → CLOSE → START(CIPSTART) → SEND(CIPSEND→">") →
  DATA(GET→SEND OK, +IPD parsed for fetch)`.
- **Push** (every 3 min): `GET /update?api_key=<WRITE>&field1=28.4&field2=81&field3=0`.
  Plain HTTP port 80 — HTTPS is unreliable on AT firmware.
- **Fetch** (every 5 min): `GET /channels/3530859/fields/5/last.txt?api_key=<READ>`;
  `+IPD` payload parsed as 0–100 (empty = never written yet → keep old).
- Display: screen 3 shows `SRC:ESP` while the ML value is <15 min old,
  else `SRC:LOCAL` heuristic. LED: slow blink once pushes succeed, fast blink
  until then (also fast if telemetry goes stale).
- Float formatting: **no `%f` in `snprintf`** (newlib-nano prints empty —
  same lesson as `MILESTONE_WEATHER_V1.md`); temperature is formatted by hand.
- Credentials baked in source (`WIFI_SSID/PASS`, `TS_WRITE_KEY`,
  `TS_READ_KEY`, `TS_CHANNEL_ID`). Physical-access risk accepted; rotate keys
  in ThingSpeak if the repo goes public.

## 3. Wiring (unchanged pins, new roles)

| NodeMCU (AT fw) | ↔ | Blue Pill | Note |
|---|---|---|---|
| RX (GPIO3) | ← | PA9 (TX) | AT commands/responses @115200 |
| TX (GPIO1) | → | PA10 (RX) | |
| GND | ↔ | GND | mandatory common ground |

Each board on its **own** USB. **Unplug RX/TX while USB-flashing the NodeMCU**
(CP2102 and PA9 fight over RX). SWD probe wiring untouched (PA13/PA14/RST).

## 4. ML loop (`tools/ml_forecast.py`, laptop side)

- Fields: 1=temp, 2=humidity, 3=rain(0/1) from STM32; **5=ML probability**
  from laptop. (Add field5 in the channel settings for a clean label.)
- `python tools/ml_forecast.py train` — downloads `feeds.csv?results=8000`,
  features `[T, H, dT/dt, dH/dt, recent_rain, hour]`, label = rain within next
  30 min, Decision Tree (depth 5, balanced), time-ordered holdout accuracy,
  saves `tools/rain_model.pkl` (gitignored). Needs 50+ samples with both
  classes — until the station logs real rainy days it exits with guidance.
- `python tools/ml_forecast.py loop [--once] [--interval 300]` — predicts from
  latest feeds, writes `field5` (entry = forecast, other fields null — normal
  for ThingSpeak). Skips writes on stale/thin data; survives network hiccups.
- Cadence math: STM32 push 3 min + laptop write 5 min ≪ 15 s limit and message
  quota. Reads (`last.txt`, `feeds.csv`) don't count against the update limit.
- Deps: `pip install -r tools/ml-requirements.txt` (scikit-learn, numpy).

## 5. Bring-up order

1. ESP8266: AT firmware flashed ✅ → `AT+GMR` OK ✅
2. STM32: flash new firmware via `tools/chunk_flash.py` (needs ESP32 probe
   back on USB; ~46 KB, mostly rewritten → ~10 min, run detached)
3. Wire NodeMCU ↔ Blue Pill (§3); power both; watch screen 3 flip to `SRC:LOCAL`
   (pushes landing → check channel), then run the laptop loop → `SRC:ESP`
4. `train` once rainy-day history exists; keep `loop` running

## 6. References

- Channel 3530859 (private; read key needed for `feeds.csv`/`last.txt`)
- `firmware/STM32_Weather/src/main.cpp` — AT driver source of truth
- `tools/ml_forecast.py`, `tools/ml-requirements.txt`
- `third-party/esp8266-at/` — exact binaries flashed
- `legacy/ESP8266_Link/` — retired custom-ESP approach (why AT won)
