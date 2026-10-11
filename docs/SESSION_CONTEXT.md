# Session Context — Blue Pill Weather Station via ESP32 SWD (saved 2026-10-10)

## Host
- Fedora 44, user `jithu` (in `dialout`), workspace `/home/jithu/college/mcshi`
- ESP32 port: `/dev/ttyUSB0` = Silicon Labs CP2102 (`10c4:ea60`). NOTE: ESP32 USB
  drops out often (bumps/replugs); `No serial data received` on upload → just retry,
  2nd try almost always succeeds. Background tool calls can get killed on timeout
  and take the flash with them — start long flashes detached (nohup + disown, no
  sleep/cat in the same call).

## Target (discovered this session)
- Blue Pill, MCU marking **CKS32F103C8T6** (ARM Cortex-M3 clone, NOT STM32, NOT CH32/RISC-V)
- SWD identity: DPIDR `0x2BA01477` (DPv2; upstream probe expected `0x1BA01477`),
  CPUID `0x412FC231` (M3 r2p1), DBGMCU `0x20036410` (dev `0x410` medium density),
  64KB flash / 1K pages / single bank, AP 0
- Power: Blue Pill on its OWN USB (ESP 3V3 rail sagged during flash charge-pump and
  wedged the flash controller). Keep direct `GND→GND`. Never feed 3V3 from both sides.
- UART ROM bootloader is a dead end on this unit: `B0=1/B1=0` + RESET and +
  cold power-cycle both kept running blink; all-baud prober `idle=0/PULLUP=1`,
  silent, zero `0x79`. Direct Blue Pill USB enumerates nothing (F103 ROM = UART-only,
  blink has no USB stack). All moot now — SWD programs flash regardless of BOOT.

## SWD wiring (working, keep as-is)
- `ESP21→DIO/PA13`, `ESP22→CLK/PA14` on the SMALL debug header (not main headers —
  those positions are JTAG PA15/PB3 on clones), `ESP19→RST`, `GND→GND`
- Header silkscreen order varies; if DPIDR fails, try DIO/CLK swapped first
- Keep `PA13/PA14` reserved for SWD, `PA9/PA10` reserved for ESP8266 (USART1)

## ESP32 SWD probe (`probe/ESP32SWDProbe/`, from AVATAR3905/esp32-stlink-probe)
- PIO wrapper: `pio_probe/` (`esp32dev`, Arduino, 115200; `.ino`→`main.cpp` needed
  `#include <Arduino.h>` + fwd decls). `SERIAL_ONLY 1`.
- Mandatory patches in `pio_probe/src/main.cpp`: accept any DPIDR (`g_dpidr`;
  CKS32 = `0x2BA01477`); `swd_delay_cycles = 250` (40/120 = garbage over Dupont,
  400 = stable but slow); WAIT retries read 100→1000 / write 40→500; flash timeouts
  erase 200→2000ms, program poll 100→500ms.
- Build/flash: `cd probe/ESP32SWDProbe/pio_probe && pio run -t upload`
- Checks: `probe/ESP32SWDProbe/tests/part_id_check.py /dev/ttyUSB0` (expect `partno=0xc23 PASS`),
  `probe/ESP32SWDProbe/probe_tools/introspect.py` (full ID + vectors). `K failed hdr=01fb` = DPIDR gate;
  `01fc` (-4 WAIT) = stall; `112 (0x70='p')` / `-99` = probe rebooted/desync → RTS reset.
- `write_mem` does proper 16-bit lane writes; single-halfword program verified
  (`0xFFFFABCD`, SR EOP). If target wedges (perpetual WAIT): Blue Pill RESET button
  or power-cycle clears the flash controller; probe RTS-reset clears desync.

## Flashing (slow link — chunk it)
- `flash_cli.py` bulk (15s timeouts) cannot finish 28–44KB at this SWD speed.
- Use chunked flashing (1KB `P` chunks, 180s timeout, 4× retry, full read-back
  verify, target reset at end — see `tools/README.md`): ~23s/chunk @delay400,
  ~14.8s/chunk @delay250. After script ends the target is left HALTED → must send
  `reset + resume` (via probe_client) or it never runs.
- Reflash helper belongs in `tools/` (the old `/tmp/opencode/chunk_flash.py` did not survive reboot).

## Firmware projects (all PIO, `ststm32` + Arduino unless noted)
- `firmware/STM32_OLED/` — SSD1306 "hi" demo (27.8KB). First VERIFY PASS milestone.
- `firmware/STM32_Weather/` — Smart Weather Station v1 (43.8KB, VERIFY PASS, running):
  DHT11 `PB8` (+4.7k–10k pull-up), rain `DO→PB9` (LOW=rain) + `AO→PA0`,
  OLED I2C1 `PB6/PB7` @`0x3C`; 2s sensors / 3s 3-screen cycle / 5s telemetry;
  USART1 `PA9/PA10` @115200 speaks `T:28.4,H:82,R:0` out, parses `F:78` in;
  forecast = ESP value if fresh (<60s, `SRC:ESP`) else local heuristic (`SRC:LOCAL`).
  BUG LESSON: newlib-nano `snprintf %f` prints empty → use `print(float, 1)`.
- `legacy/STM32Bridge/` (old UART bridge, superseded), `legacy/STM32Prober/` (old UART prober,
  superseded), `third-party/stm32flash/` (ROM tool, never got ACK — obsolete while SWD works).

## Docs
- `BOARD_DETAILS.md` (board/probe/wiring), `MILESTONE_OLED.md` ("hi" chain),
  `MILESTONE_WEATHER_V1.md` (v1 sign-off). Project brief supplied by user defines
  full scope (sensors → STM32 → ESP8266 → cloud → ML → forecast → OLED).

## Next steps
1. Attach ESP8266 (`TX→PA10, RX←PA9, GND→GND`, USART1 115200): cloud dashboard
   (ThingSpeak/Blynk/custom), data logging.
2. Offline ML (Decision Tree start): features temp/humidity/ΔT/ΔH/recent-rain,
   labels from rainfall records → `F:<prob>` back to screen 3 as `SRC:ESP`.
3. Optional: FreeRTOS tasks, pressure sensor, rain gauge, mobile app.

---
# Session 2026-10-11 — ESP uplink owner (STM32 senses, ESP networks)

## What changed
- **Stock-AT path retired** (`docs/THINGSPEAK_AT.md` marked SUPERSEDED):
  UART0 contention (CP2102 vs PA9 on RX/GPIO3) blocked flashing + monitoring
  while wired; zero AT commands ever landed (`CWMODE:2`, `No AP`, echo on).
- **New firmware `firmware/ESP8266_Bridge/` (`esp-uplink v1`, `nodemcuv2`)**:
  STM32 link on SoftwareSerial **D5/GPIO14←PA9, D6/GPIO12→PA10 @9600**,
  HW Serial @115200 stays on PC USB (flash + monitor with STM32 attached);
  WiFi STA join (30 s backoff), ThingSpeak push /3 min (fresh <60 s, entry
  id from response **body** last line), `TELE`/`STM:`/`TS:` logs, 60 s
  auto-status, `status`/`?`, PC→STM32 forward, slow/fast LED. Secrets baked
  in source (risk accepted, rotate if public).
- **`firmware/STM32_Weather/` telemetry mode** (46,700 B, vector
  `0x08008469`, VERIFY PASS): sensors + OLED unchanged, `T:` TX /5 s on
  `Serial1 @9600`, `F:` RX parse, AT engine retired in-source. OLED rows
  re-measured for 128 px (`T:../H:..`, `RAIN!` size 3, right-aligned SRC tag).
- **Verified live**: `wifi: up ip=10.150.128.21 rssi=-40..-54`,
  `TELE T=28.6 H=70 R=0` /5.0 s, finger test moved T/H + flipped `R:0→1`,
  `TS: entry=5`. See `docs/ESP_UPLINK.md`, `docs/MILESTONE_UPLINK.md`.

## Bug lessons (do not regress)
- **`%f` in `printf` hard-faults this target** (not just empty `snprintf`):
  `Serial1.printf("T:%.1f..")` froze MCU ~5 s after boot (stuck RAIN STATUS
  + dead LED). Hand-format tenths (`%d.%d`); `Print::print(f, n)` is safe.
- **SoftwareSerial@115200 + WiFi = Soft WDT crash loop** (`Exception (4)`
  /8 s). Stable at @9600 both sides (`STM_BAUD` ↔ `ESP_BAUD`).
- **SWD bulk reads fail while target runs, pass halted**: halt first via
  `introspect.py --halt`; piecemeal 8 KB dumps + byte-compare as verify
  fallback; `reset + resume` after (failed runs leave target halted). `-4`
  flurries also mean weak 3V3 — own-USB power. First touch after USB swap
  usually desyncs — RTS reset / retry ×3.
- **ThingSpeak entry id is the last body line**, not the status line; drain
  with `(connected() || available())`.

## Next steps
1. `F:<prob>` forecast fetch on ESP → screen 3 `SRC:ESP` (reserved).
2. `tools/ml_forecast.py loop` → field5 (dormant until (1) reads it).
3. Optional: FreeRTOS, pressure sensor, rain gauge, mobile app.
