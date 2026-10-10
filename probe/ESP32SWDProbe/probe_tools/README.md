# probe_tools

Reusable CLI utilities built on top of the ESP32 SWD probe's native binary
protocol (`esp32_swd_probe/esp32_swd_probe.ino`, `SERIAL_ONLY` mode). Unlike
the one-off scripts in `tests/`, each of these shares one client
implementation (`probe_client.py`) instead of hand-rolling the protocol
per-script.

**Verified against a real STM32F103 on COM10**: `introspect.py` (plain,
`--halt`, `--json`), a full 128KB `dump`, and a `flash_cli.py` write plus a
`dump_restore.py restore` write -- both to a scratch flash region, both
independently confirmed with a separate read-back rather than trusting each
tool's own self-check, followed by a full `tests/test_stlink2.py` pass to
confirm nothing else regressed. Caught one real bug during code review
before any of that: `bp_clear`/`bp_clear_all`/`wp_clear`/`wp_clear_all` in
`probe_client.py` were missing `reply_len=1` -- the firmware always appends
a trailing slot byte on success, even for clear operations, and dropping it
would have desynced the next command's reply framing.

## Files

- `probe_client.py` -- shared `Probe` class: connect, wait for the ESP32's
  boot banner to clear, then typed methods for every native command
  (halt/step/resume/reset, register read/write, breakpoints, watchpoints,
  memory read/write, flash erase/program, AP/DP register access, chip and
  part identification). Import this; don't run it directly.

- `flash_cli.py` -- flash a `.bin` or `.hex` image over SWD, no bootloader
  or BOOT0 jumper needed.
  ```
  python flash_cli.py COM10 firmware.bin
  python flash_cli.py COM10 firmware.hex
  ```

- `dump_restore.py` -- full-chip flash backup and restore.
  ```
  python dump_restore.py dump COM10 backup.bin
  python dump_restore.py restore COM10 backup.bin
  ```

- `introspect.py` -- human-readable (or `--json`) snapshot of whatever
  chip is attached: CPUID decode, flash geometry, dual-bank flag, AP index,
  halt state, and (with `--halt`) every core register.
  ```
  python introspect.py COM10
  python introspect.py COM10 --halt
  ```

Run any of them with `--help` for the full flag list.
