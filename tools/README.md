# tools/

## `chunk_flash.py` — fast chunked flasher for the slow SWD link

`probe/ESP32SWDProbe/probe_tools/flash_cli.py` in bulk mode (15 s timeouts,
program-everything) cannot finish a 28–44 KB image at `swd_delay_cycles =
250–400` (~15–23 s per 1 KB). `chunk_flash.py` fixes that with four moves:

1. **Skip-identical pages** — pre-reads flash (reads are fast, no flash
   busy-waits) and only programs pages that differ. A small-edit reflash
   typically rewrites 1–4 pages instead of 44 (often 10–20x faster).
2. **Erase-only for blank pages** — all-`0xFF` pages use fast `Y` instead of
   the slow halfword-loop `P`, or are skipped if already blank.
3. **4 KB `P` chunks** (firmware max, page-aligned) instead of 1 KB.
4. **240 s per-chunk timeout + 4× retry**, full read-back verify, final
   **reset + resume** (the target is left HALTED after `P` — without this it
   never runs).

```bash
python tools/chunk_flash.py firmware/STM32_Weather/.pio/build/bluepill_f103c8/firmware.bin
python tools/chunk_flash.py firmware.bin /dev/ttyUSB0 --dry-run   # plan only
python tools/chunk_flash.py firmware.bin --full                   # no skip-identical
python tools/chunk_flash.py firmware.bin --no-verify              # trust P's internal verify
python tools/chunk_flash.py firmware.hex                          # needs `pip install intelhex`
```

## Why not just raise the baud rate?

The bottleneck is SWD bit-bang + STM32 flash busy-waits (~99% of chunk time).
Moving 1 KB over USB serial at 115200 takes ~0.1 s — raising `--baud` saves
almost nothing. For more raw speed, lower `swd_delay_cycles` in
`probe/ESP32SWDProbe/pio_probe/src/main.cpp` (250 stable here, 120 = garbage
over Dupont wires), reflash the probe, and re-verify with
`tests/part_id_check.py`.

## Reference commands

```bash
# link check
python probe/ESP32SWDProbe/tests/part_id_check.py /dev/ttyUSB0
python probe/ESP32SWDProbe/probe_tools/introspect.py /dev/ttyUSB0

# backup before flashing
python probe/ESP32SWDProbe/probe_tools/dump_restore.py dump /dev/ttyUSB0 backup.bin

# bulk flash (small images only)
python probe/ESP32SWDProbe/probe_tools/flash_cli.py /dev/ttyUSB0 firmware.bin
```
