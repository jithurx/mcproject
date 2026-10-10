# How we made an ESP32 program the STM32 Blue Pill

This is the full story of the programming path this repo depends on: a stock
ESP32 DevKit reprogrammed from scratch as an **SWD debug probe** that halts,
erases, programs, verifies, and resets the Blue Pill's flash — with no BOOT0
jumpers and no UART bootloader involved.

## 1. The problem: the UART bootloader was a dead end

The normal cheap way to flash an STM32F103 is its factory ROM bootloader over
USART1: pull `BOOT0=1/BOOT1=0`, reset, send `0x7F` at some baud, get `0x79`
ACK. On this unit every variant of that failed:

- `BOOT0=1/BOOT1=0` + RESET **and** + cold power-cycle both kept running the
  old blink firmware — BOOT0 was never sampled HIGH (jumper/header doubt, never
  resolved).
- An ESP32 UART prober sweeping all bootloader bauds saw `idle=0/PULLUP=1`,
  silence, zero `0x79` — PA9 floating, the chip never entered ROM.
- The Blue Pill's USB enumerates nothing on a PC (F103 ROM is UART-only and the
  blink firmware has no USB stack — expected, not a fault).

The evidence (kept for reference in `legacy/STM32Prober/`, `legacy/STM32Bridge/`,
`third-party/stm32flash/`) all pointed the same way: this board stays in Main
Flash no matter what. So instead of coaxing the chip into its bootloader, we
used the debug port, which programs flash **regardless of BOOT pins**.

## 2. The idea: ESP32 as an SWD probe

Cortex-M chips expose a **Serial Wire Debug (SWD)** port — two pins, SWDIO
(bidirectional data) and SWCLK (clock), plus an optional hardware reset line.
Through SWD a debugger can halt the core, read/write memory and registers, and
drive the on-chip flash controller directly. Ready-made probes (ST-Link, J-Link)
do exactly this; we taught an ESP32 to do it by bit-banging the protocol with
GPIO writes.

The firmware derives from
[AVATAR3905/esp32-stlink-probe](https://github.com/AVATAR3905/esp32-stlink-probe)
(vendored at `probe/ESP32SWDProbe/`, Arduino/ESP32 core, wrapped for PlatformIO
in `probe/ESP32SWDProbe/pio_probe/`). It implements the SWD transport from
scratch: line reset, DP/AP register access, memory access port setup, Cortex-M
halt/step/resume, breakpoints/watchpoints, and the STM32F1 flash sequence.

## 3. How SWD programming actually works (the 30-second version)

```
ESP32 GPIO toggling                    Blue Pill (CKS32F103C8T6, Cortex-M3)
────────────────────                   ────────────────────────────────────
SWDIO (GPIO21) ◄────────────────────► PA13
SWCLK (GPIO22) ──────────────────────► PA14
NRST  (GPIO19) ──────────────────────► NRST
GND ───────────────────────────────── GND
```

A flash cycle is a conversation, not a file copy:

1. **Connect**: SWD line-reset → read DPIDR (debug port ID) → power up the
   debug domain → find the memory-access AP → halt the Cortex-M3 core.
2. **Unlock flash**: write the two key words (`0x45670123`, `0xCDEF89AB`) to
   `FLASH_KEYR` — the flash controller ignores everything until then.
3. **Erase** each covered page (`PER` + `STRT` in `FLASH_CR`, poll `BSY` in
   `FLASH_SR`). Erased flash reads `0xFF`.
4. **Program** halfword by halfword (`PG` bit latched once per run, write to
   the target address, poll `BSY` after every halfword — STM32F1 flash only
   writes 16 bits at a time).
5. **Verify** by reading the region back over SWD and comparing.
6. **Lock** the controller, **reset + resume** the core so the new firmware runs.

Every one of those steps is an SWD transaction: short request/response frames
with parity and WAIT/FAULT acknowledgements. On this build each GPIO edge is
followed by a `swd_delay()` spin (`swd_delay_cycles = 250`), so a single 1 KB
page costs thousands of bit-banged transactions — roughly 15–23 seconds. That
slowness shapes the whole host-side design (§7).

## 4. The probe firmware and its protocol

- **Transport**: `SERIAL_ONLY 1` (default) — a compact native binary protocol
  over USB serial @115200 (`/dev/ttyUSB0`). (Behind `SERIAL_ONLY 0` there are
  also OpenOCD `remote_bitbang` over WiFi/TCP and a GDB server; we don't use
  them.)
- **Protocol**: one command byte, optional body, 2-byte `<status, detail>`
  reply plus payload. The authoritative definition is `handle_native_cmd()`
  in `probe/ESP32SWDProbe/pio_probe/src/main.cpp`; `probe_tools/probe_client.py`
  mirrors it 1:1 with typed methods — import that, don't hand-roll bytes.

| Cmd | Method | Does |
|---|---|---|
| `A` | `alive()` | liveness check |
| `I` / `K` | `chip_info()` / `part_info()` | DPIDR + flash size + IDCODE / CPUID + page size + AP |
| `H` / `C` / `S` / `U` | `halt()` / `resume()` / `step()` / `reset()` | core control (`U` = AIRCR reset) |
| `X` / `M` | `read_block()` / `write_mem()` | memory access (≤4 KB per call) |
| `Y` | `flash_erase()` | page/mass erase |
| `P` | `flash_program()` | halt + erase + program + verify, ≤4096 B |
| `G` / `N` / `T` / `J` | regs / write-reg / status / halt reason | inspection |
| `F` / `V` | breakpoints / watchpoints | FPB + DWT |
| `D` / `W` | DP/AP register peek/poke | low-level debug |

Error codes come back as negative numbers (`-2` parity … `-4` WAIT exhausted,
`-5` DPIDR mismatch, `-10` flash errors, `-11` busy timeout, `-13` verify
mismatch, `-99` = probe never replied/desync).

## 5. The three patches that made it work on this board

Upstream assumed a genuine STM32 on short wires. Our CKS32 clone on Dupont
jumpers needed three changes (all in `pio_probe/src/main.cpp`, all documented
in `docs/BOARD_DETAILS.md`):

1. **Accept any DPIDR** (`g_dpidr`). Upstream rejected anything but STM32's
   `0x1BA01477` with `-5`; our CKS32 reports ARM DPv2 **`0x2BA01477`**.
   The probe now records whatever DPIDR it sees and `I` reports it.
2. **`swd_delay_cycles = 250`** (was 40). Fast bit-bang is marginal over
   Dupont wires — before the fix: `-5` mismatches, `-4` WAIT flakiness,
   `0x00800000` garbage reads. History: 40 → 400 (stable but slow) → 120
   (garbage again) → **250** (stable, ~14.8 s/KB). 400 also works (~23 s/KB).
3. **Patience for a slow clone**: WAIT retries raised (read 100→1000, write
   40→500), flash timeouts raised (erase 200→2000 ms, program poll 100→500 ms),
   plus `#include <Arduino.h>` and forward declarations so the `.ino` builds
   as PlatformIO `.cpp` (Arduino IDE auto-generates prototypes, PIO doesn't).

Build/flash the probe itself with:
```bash
cd probe/ESP32SWDProbe/pio_probe && pio run -t upload
# "No serial data received" → just retry, 2nd try almost always succeeds
```

## 6. Wiring that finally linked (and the gotchas)

| ESP32 DevKit V1 | → | Blue Pill |
|---|---|---|
| GPIO21 | → | DIO/SWDIO — **small 4-pin debug header**, PA13 |
| GPIO22 | → | CLK/SWCLK — **small debug header**, PA14 |
| GPIO19 | → | RST/NRST (main header) |
| GND | → | GND (direct Dupont, **mandatory**) |

Gotchas that cost real time:

- PA13/PA14 live on the **small debug header**, not the long main headers
  (those positions are JTAG PA15/PB3 on clones). Silkscreen order varies — if
  DPIDR fails, try DIO/CLK swapped before anything else.
- The Blue Pill runs on its **own USB supply**. Powering it from the ESP32's
  3V3 rail sags during the flash charge-pump and wedges the flash controller
  (perpetual WAIT). Never feed 3V3 from both sides; common GND always.
- Keep `PA13/PA14` reserved for SWD and `PA9/PA10` for the ESP8266 link.

Link proof: `python probe/ESP32SWDProbe/tests/part_id_check.py /dev/ttyUSB0`
→ `partno=0xc23 … RESULT: PASS` (Cortex-M3 r2p1, 64 KB flash, 1 K pages,
device `0x410`).

## 7. Host-side toolchain: why flashing is chunked and differential

`probe_tools/` holds the reusable CLIs (`flash_cli.py`, `dump_restore.py`,
`introspect.py`, all on `probe_client.py`); `tests/` holds one-off diag
scripts. But `flash_cli.py`'s 15 s timeouts can't finish 28–44 KB at ~15 s/KB,
so real flashes go through `tools/chunk_flash.py`:

1. **Pre-read** the image range (reads are fast — no flash busy-waits) and
   **skip identical pages**. A small-edit reflash rewrites 1–4 pages, not 44.
2. All-`0xFF` pages go as fast **erase-only** (`Y`), or are skipped if blank.
3. **4 KB `P` chunks** (firmware max, page-aligned so no chunk's erase clobbers
   a finished chunk), 240 s timeout, 4× retry.
4. Full **read-back verify**, then **reset + resume** — `P` leaves the target
   halted, and without reset+resume it never runs.

```bash
python tools/chunk_flash.py firmware/STM32_Weather/.pio/build/bluepill_f103c8/firmware.bin
```

End to end, flashing the weather station looks like:

```
pio run  →  firmware.bin (43.8 KB)
   │  tools/chunk_flash.py: pre-read → skip 40/44 pages → P ×1 → VERIFY PASS
   ▼
Blue Pill resets, 3-screen OLED UI runs, USART1 streams T:../H:../R:..
```

## 8. Reproduce / verify checklist

- [ ] Probe alive: `part_id_check.py` → `RESULT: PASS`
- [ ] Full identity: `probe_tools/introspect.py` (DPIDR `0x2BA01477`, CPUID
      `0x412FC231`, 64 KB / 1 K pages / single bank, reset vectors sane)
- [ ] Backup: `dump_restore.py dump /dev/ttyUSB0 backup.bin`
- [ ] Flash: `tools/chunk_flash.py <firmware.bin>` → `VERIFY PASS` → reset+resume
- [ ] Target runs: PC13 heartbeat + OLED UI + `T:../H:../R:..` on USART1

## 9. References

- `docs/BOARD_DETAILS.md` — identity, wiring, patches (the condensed spec)
- `docs/MILESTONE_OLED.md` — first VERIFY PASS through this chain
- `docs/SESSION_CONTEXT.md` — full session context and next steps
- `probe/ESP32SWDProbe/README.md` + `WIRING.md` — upstream probe docs
- `probe/ESP32SWDProbe/IMPROVEMENT_PLAN.md` — hardening notes vs Black Magic
  Debug / CMSIS-DAP / OpenOCD references
- `tools/README.md` — chunked-flash contract and the baud-rate non-issue
