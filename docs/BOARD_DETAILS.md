# Board Details — Blue Pill + ESP32 SWD Probe (2026-10-10)

## Target board
- Blue Pill form factor, MCU marking: **CKS32F103C8T6** (`NSWSJ 2230 A`)
  - CKS32 = Chinese STM32F103 clone, ARM Cortex-M3 (not RISC-V)
- State on arrival: PWR LED on, PC13 blinking (old blink firmware in Main Flash)
- Power: from ESP32 3V3 (PWR on, blink runs)

## SWD identity (verified via ESP32 probe)
- DPIDR: `0x2BA01477` (ARM DPv2 — NOT STM32's `0x1BA01477`; strict check must accept it)
- CPUID: `0x412FC231` → Cortex-M3 r2p1, implementer `0x41`, part `0xC23`
- DBGMCU_IDCODE: `0x20036410` → device `0x410` (medium density), rev `0x2003`
- Flash: 65536 bytes, 1024-byte pages, single bank (`page_size=0x400`, `dual_bank=0`), AP 0
- Reset vector read from flash: SP `0x20000400`, PC `0x08000145` (blink)

## SWD wiring (working)
| ESP32 DevKit V1 | → | Blue Pill |
|---|---|---|
| GPIO21 | → | DIO/SWDIO (small debug header, PA13) |
| GPIO22 | → | CLK/SWCLK (small debug header, PA14) |
| GPIO19 | → | RST/NRST (main header) |
| GND | → | GND (direct Dupont, required) |

- NOTE: PA13/PA14 are on the small 4-pin debug header (`DIO`/`CLK`), NOT the long
  main headers (those positions are JTAG `PA15/PB3` on clones). Header silkscreen
  order varies — if DPIDR fails, try DIO/CLK swapped before anything else.
- Keep wires short; common GND mandatory. Target powered separately or from ESP 3V3.

## Probe firmware (`probe/ESP32SWDProbe/pio_probe/`)
- Source: `AVATAR3905/esp32-stlink-probe` (`esp32_swd_probe.ino` → `pio_probe/src/main.cpp`)
- Transport: `SERIAL_ONLY 1`, USB serial 115200 (`/dev/ttyUSB0`, CP2102 `10c4:ea60`)
- Patches vs upstream (required for this board/wiring):
  1. Accept any DPIDR (`g_dpidr`), CKS32 reports `0x2BA01477` — old code returned `-5`.
  2. `swd_delay_cycles = 400` (was 40) — fast bit-bang is marginal over Dupont wires
     (symptoms before fix: `-5` mismatch, `-4` WAIT flakiness, `0x00800000` garbage reads).
  3. Added `#include <Arduino.h>` + forward decls (`handle_bitbang/handle_gdb/handle_native`)
     for PlatformIO `.cpp` build (Arduino IDE auto-generates prototypes, PIO does not).
- Build/flash: `cd probe/ESP32SWDProbe/pio_probe && pio run -t upload` (if
  `No serial data received`, retry — ESP32 quirk, usually succeeds 2nd try).

## Checks
- `python3 probe/ESP32SWDProbe/tests/part_id_check.py /dev/ttyUSB0` → expect `cpuid partno=0xc23 ... RESULT: PASS`
- `python3 probe/ESP32SWDProbe/probe_tools/introspect.py /dev/ttyUSB0` → full ID + flash geometry + vectors
- Raw DPIDR: send `I` @115200 → `00 00 + DPIDR(4) + FSIZE(4) + IDCODE(4)`

## Flashing (BOOT0/UART not needed)
- `python3 probe/ESP32SWDProbe/probe_tools/flash_cli.py /dev/ttyUSB0 firmware.bin` (or `.hex`)
- Backup first: `python3 probe/ESP32SWDProbe/probe_tools/dump_restore.py dump /dev/ttyUSB0 backup.bin`

## UART/BOOT notes (dead end on this unit)
- `BOOT0=1/BOOT1=0` + RESET and + cold power-cycle both still blinked → BOOT0 never
  sampled HIGH (jumper/header doubt, never verified with direct 3V3→B0 wire + meter).
- UART probes (all bauds/combos): `idle=0/PULLUP=1`, silent, no `0x79` — PA9 floating
  (user firmware never inits USART1). Consistent with stuck-in-Main-Flash, not a baud issue.
- Direct Blue Pill USB to host: enumerates nothing (F103 ROM is UART-only; blink has no
  USB stack). Expected — not a fault.
- Moot now: SWD programs flash regardless of BOOT pins.
