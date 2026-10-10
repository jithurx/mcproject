# Milestone — "hi" on 128×64 OLED via ESP32 SWD probe (2026-10-10)

## Result
Blue Pill (CKS32F103C8T6) drives a 128×64 SSD1306 OLED over I2C and displays
**hi** (size-4 white text). Verified visually; PC13 slow heartbeat confirms the
application loop is running.

## Chain that made it work
1. **UART ROM bootloader was a dead end on this unit**: `BOOT0=1/BOOT1=0` +
   RESET and + cold power-cycle both kept running blink; all-baud ESP prober
   showed `idle=0/PULLUP=1`, silent, zero `0x79 ACK`. PA9 floating, never in ROM.
2. **ESP32-as-SWD-probe instead of UART**: vendored `AVATAR3905/esp32-stlink-probe`
   into `probe/ESP32SWDProbe/`, wrapped as PlatformIO project in `pio_probe/`.
3. **Two probe patches were mandatory**:
   - Accept clone DPIDR `0x2BA01477` (upstream only accepted STM32 `0x1BA01477` → `-5`).
   - `swd_delay_cycles = 400` (was 40): fast bit-bang is garbage over Dupont wires.
   - Later also raised WAIT retries (read 100→1000, write 40→500) and flash
     timeouts (erase 200→2000ms, program poll 100→500ms) for CKS32 stalls.
4. **Wiring that finally linked**: `ESP21→DIO(PA13)`, `ESP22→CLK(PA14)` on the
   small debug header (NOT main headers), `ESP19→RST`, direct `GND→GND`,
   Blue Pill on its own USB supply (ESP 3V3 rail sagged during flash charge-pump).
5. **Link proof**: `part_id_check PASS` (Cortex-M3 r2p1, 64KB/1K pages, `0x410`),
   `introspect` reset vectors readable.
6. **Flash**: `firmware/STM32_OLED/` (PIO `ststm32`, Arduino, Adafruit SSD1306+GFX) →
   28,124 bytes. `flash_cli.py` bulk timed out on the slow link, so flashed with
   chunked writer (1KB `P` chunks, 180s timeout, retry — see `tools/README.md`): 28/28 OK @ ~23s each
   (~11 min), full read-back **VERIFY PASS**, target reset + resumed.

## OLED wiring (working)
| OLED | → | Blue Pill | note |
|---|---|---|---|
| VCC | → | 3V3 | 3.3V rail |
| GND | → | GND | common with ESP32 |
| SCL | → | PB6 | I2C1 SCL |
| SDA | → | PB7 | I2C1 SDA, addr `0x3C` |

Firmware (`firmware/STM32_OLED/src/main.cpp`): `Wire.setSDA(PB7)/setSCL(PB6)`,
`display.begin(0x3C)`; on failure fast-blinks PC13 (150ms) instead; on success
prints `hi` once and slow-blinks PC13 (800ms) as heartbeat.

## Sign-off
- [x] OLED shows `hi`
- [x] PC13 slow heartbeat
- [x] 28,124-byte VERIFY PASS + vectors `SP 0x20005000 / PC 0x08005281`
- Repro: `docs/BOARD_DETAILS.md` (board/probe) + this file; sources in
  `firmware/STM32_OLED/`, `probe/ESP32SWDProbe/pio_probe/` (chunked-flash
  workflow: `tools/README.md`).
