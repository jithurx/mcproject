# ESP32 ST-Link SWD Probe

An ESP32-WROOM-32 reprogrammed from scratch as a full SWD debug probe for
STM32 / Cortex-M targets — halt, step, inspect registers, breakpoint,
watchpoint, and program flash while the target firmware runs.

## Wiring (ESP32 → target)

| ESP32 pin | Function          | Target pin   |
|-----------|-------------------|--------------|
| GPIO21    | SWDIO             | PA13 / SWDIO |
| GPIO22    | SWCLK             | PA14 / SWCLK |
| GPIO19    | NRST (active-low) | NRST         |
| GND       | GND               | GND          |

See `WIRING.md` for more detail (short wires, common ground required).

## Capabilities

- Halt / resume / single-step, with interrupts masked while stepping so a
  pending IRQ can't land the core mid-ISR
- Hardware breakpoints (FPB) and watchpoints (DWT, with a software
  single-step fallback for early Cortex-M3 silicon where DWT is clocked but
  never generates a halt)
- Register read/write, arbitrary memory read/write (byte/halfword/word),
  1KB TAR auto-increment boundaries handled correctly on large block
  transfers
- STM32F1 flash erase/program/verify, including dual-bank (XL-density)
  devices — this board is single-bank, so that path is reference-checked
  against known-good source rather than hardware-verified
- Auto-probes which AP index is the actual debug MEM-AP instead of
  assuming AP 0, so the same firmware isn't tied to one specific target
- Reports chip identity (CPUID, flash geometry) via a dedicated command
- Three transports: a compact native binary protocol over USB serial
  (`SERIAL_ONLY 1`, the default — talk to it with `tests/diag_serial.py`),
  OpenOCD's `remote_bitbang` driver over WiFi/TCP, and a GDB Remote Serial
  Protocol server, the latter two behind `SERIAL_ONLY 0`

## Firmware

- `esp32_swd_probe/esp32_swd_probe.ino` — the probe firmware (Arduino / ESP32
  core)
- Build & flash with Arduino CLI (`SERIAL_ONLY 1` is the default transport)

## Build & flash

```
arduino-cli compile --fqbn esp32:esp32:esp32 esp32_swd_probe
arduino-cli upload -p COM10 --fqbn esp32:esp32:esp32 esp32_swd_probe
```

## Verify

```
python tests/diag_serial.py --serial COM10       # byte/halfword/word lane test
python tests/test_stlink2.py --serial COM10      # full halt/step/bp/wp/flash suite
python tests/part_id_check.py COM10              # CPUID / flash geometry / AP index
python tests/tar_rollover_check.py COM10         # 1KB block-read boundary
python tests/dfsr_check.py COM10                 # breakpoint halt reason
```

See `IMPROVEMENT_PLAN.md` for the implementation history — what's verified
against real hardware vs. reference-checked-but-untested, and what's still
on the list (semihosting, SWO/RTT, full CMSIS-DAP compatibility).
