#!/usr/bin/env python3
"""Fast chunked flasher for the ESP32 SWD probe (slow bit-banged link).

Why this exists: `probe_tools/flash_cli.py` uses 15 s timeouts and programs
everything unconditionally. On this board's link (`swd_delay_cycles` 250-400,
~15-23 s per 1 KB) a 44 KB image times out part-way. This script:

  1. Pre-reads flash over the image range (reads are much faster than
     programs -- no flash busy-waits) and SKIPS pages that already match.
     Reflashing after a small edit typically rewrites only 1-4 pages
     instead of all 44 -- the single biggest speedup (often 10-20x).
  2. Sends all-0xFF pages as ERASE-only (`Y`, fast) instead of PROGRAM (`P`,
     slow halfword-by-halfword loop), and skips them entirely if already blank.
  3. Uses 4 KB `P` chunks (the firmware max) instead of 1 KB, halving the
     halt/erase-setup round trips. 4 KB is a multiple of the 1 KB page size,
     so chunk boundaries stay page-aligned and no chunk's erase can clobber
     a previously written chunk.
  4. Generous per-chunk timeouts (default 240 s) with retry, full read-back
     verify, and a final reset+resume (the target is left HALTED after `P`).

Usage:
    python chunk_flash.py firmware.bin [port] [options]
    python chunk_flash.py firmware.hex /dev/ttyUSB0
    python chunk_flash.py firmware.bin --dry-run        # plan only, no hardware
    python chunk_flash.py firmware.bin --full           # disable skip-identical
    python chunk_flash.py firmware.bin --no-verify      # trust P's internal verify

About "faster upload speed": the bottleneck on this link is SWD bit-bang time
on the ESP32 plus STM32 flash busy-waits (~99% of a chunk's time). The USB
serial transfer of a 1 KB chunk at 115200 baud takes ~0.1 s, so raising
--baud (or the probe's Serial baud) does not meaningfully help -- skipping
unneeded work (this script's strategy) does. If you still want more raw
speed, the knob is `swd_delay_cycles` in `probe/ESP32SWDProbe/pio_probe/src/main.cpp`
(250 stable here, 120 = garbage over Dupont wires); re-verify the link with
`tests/part_id_check.py` after changing it.
"""

import argparse
import os
import struct
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROBE_TOOLS = os.path.normpath(os.path.join(
    SCRIPT_DIR, "..", "probe", "ESP32SWDProbe", "probe_tools"))
sys.path.insert(0, PROBE_TOOLS)

from probe_client import Probe, ProbeError  # noqa: E402

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_ADDRESS = 0x08000000
MAX_P_LEN = 4096
READ_LEN = 4096


def load_image(path, address):
    if path.lower().endswith(".hex"):
        try:
            import intelhex
        except ImportError:
            sys.exit("ERROR: .hex needs the intelhex package "
                     "(`pip install intelhex`), or pass a .bin instead.")
        ih = intelhex.IntelHex()
        ih.loadhex(path)
        base = ih.minaddr()
        data = bytes(ih.tobinarray(start=base, end=ih.maxaddr()))
        return base, data
    with open(path, "rb") as f:
        return address, f.read()


def flash_program_timeout(p, addr, data, timeout):
    """`P` with a caller-chosen timeout (probe_client's 15 s default is too
    short for multi-KB chunks on the slow link)."""
    if len(data) > MAX_P_LEN or len(data) % 2 or addr % 2:
        raise ValueError("flash_program: addr/len must be even, len <= 4096")
    p._cmd(ord("P"), struct.pack("<II", addr, len(data)) + data,
           timeout=timeout)


def flash_erase_timeout(p, addr, length, timeout=60):
    p._cmd(ord("Y"), struct.pack("<II", addr, length), timeout=timeout)


def read_exact(p, addr, length, timeout=60):
    out = bytearray()
    a, remaining = addr, length
    while remaining:
        n = min(remaining, READ_LEN)
        nwords = (n + 3) // 4
        if nwords > 1024:
            nwords = 1024
            n = min(remaining, nwords * 4)
        body = struct.pack("<IH", a, nwords)
        chunk = p._cmd(ord("X"), body, reply_len=nwords * 4, timeout=timeout)
        out += chunk[:n]
        a += n
        remaining -= n
    return bytes(out)


def with_retry(desc, retries, fn):
    last = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except ProbeError as e:
            last = e
            print("  %s: attempt %d/%d failed: %s"
                  % (desc, attempt, retries, e))
            if attempt < retries:
                time.sleep(1.0)
    raise last


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="Firmware file (.bin or .hex)")
    ap.add_argument("port", nargs="?", default=DEFAULT_PORT,
                    help="Probe serial port (default %s)" % DEFAULT_PORT)
    ap.add_argument("--address", type=lambda x: int(x, 0),
                    default=DEFAULT_ADDRESS,
                    help="Flash address for .bin (default 0x08000000; ignored for .hex)")
    ap.add_argument("--chunk-kb", type=int, default=4, choices=(1, 2, 4),
                    help="P chunk size in KB, page-aligned (default 4 = firmware max)")
    ap.add_argument("--retries", type=int, default=4,
                    help="Retries per chunk (default 4)")
    ap.add_argument("--timeout", type=int, default=240,
                    help="Per-P-chunk timeout in seconds (default 240)")
    ap.add_argument("--baud", type=int, default=115200,
                    help="Probe serial baud (default 115200; must match probe firmware)")
    ap.add_argument("--full", action="store_true",
                    help="Program everything; disable skip-identical optimization")
    ap.add_argument("--mass-erase-first", action="store_true",
                    help="Mass-erase the chip first (only useful if the new image is "
                         "smaller than old flash contents and you want the tail wiped)")
    ap.add_argument("--no-verify", action="store_true",
                    help="Skip the final full read-back verify (P already verifies each "
                         "chunk internally)")
    ap.add_argument("--no-reset", action="store_true",
                    help="Leave the target halted instead of reset+resume at the end")
    ap.add_argument("--dry-run", action="store_true",
                    help="Load the image and print the chunk plan without touching hardware")
    args = ap.parse_args()

    addr, data = load_image(args.image, args.address)
    orig_len = len(data)
    if len(data) % 2:
        data += b"\xff"
    print("Image: %s  %d bytes (%d on flash after pad) at 0x%08x"
          % (args.image, orig_len, len(data), addr))

    chunk_len = args.chunk_kb * 1024
    n_chunks = (len(data) + chunk_len - 1) // chunk_len
    print("Plan: %d x %d KB P-chunks (firmware max 4 KB, page-aligned)"
          % (n_chunks, args.chunk_kb))
    if args.dry_run:
        for i in range(n_chunks):
            off = i * chunk_len
            print("  chunk %d: flash 0x%08x len %d"
                  % (i, addr + off, min(chunk_len, len(data) - off)))
        print("Dry run -- no hardware touched. "
              "Skip-identical planning happens live after the pre-read.")
        return 0

    t_all = time.time()
    with Probe(args.port, baud=args.baud) as p:
        p.alive()
        print("Probe alive on %s @%d." % (args.port, args.baud))
        cpuid, page_size, dual_bank, ap_sel = p.part_info()
        print("Target: CPUID=0x%08x page_size=%d dual_bank=%s AP=%d"
              % (cpuid, page_size, dual_bank, ap_sel))
        if addr % page_size:
            sys.exit("ERROR: address 0x%08x is not page-aligned (page %d)."
                     % (addr, page_size))

        if args.mass_erase_first:
            print("Mass-erasing...")
            t0 = time.time()
            with_retry("mass erase", args.retries,
                       lambda: flash_erase_timeout(p, 0, 0, timeout=120))
            print("Erased in %.1fs." % (time.time() - t0))
            existing = b"\xff" * len(data)
        elif not args.full:
            print("Pre-reading %d bytes for skip-identical planning..." % len(data))
            t0 = time.time()
            existing = with_retry(
                "pre-read", args.retries,
                lambda: read_exact(p, addr, len(data)))
            dt = time.time() - t0
            print("Pre-read in %.1fs (%.1f KB/s)."
                  % (dt, len(data) / 1024.0 / max(dt, 0.001)))
        else:
            existing = None

        # ---- page-granular plan, then coalesce PROGRAM runs into P calls ----
        pages = []
        for off in range(0, len(data), page_size):
            want = data[off:off + page_size]
            have = existing[off:off + page_size] if existing is not None else None
            if have is not None and have == want:
                pages.append(("skip", off))
            elif want.strip(b"\xff") == b"":
                pages.append(("skip" if have is not None and have == want
                              else "erase", off))
            else:
                pages.append(("program", off))

        n_skip = sum(1 for k, _ in pages if k == "skip")
        n_erase = sum(1 for k, _ in pages if k == "erase")
        n_prog = sum(1 for k, _ in pages if k == "program")
        print("Pages (%d B): %d skip-identical, %d erase-only, %d to program"
              % (page_size, n_skip, n_erase, n_prog))
        if n_prog == 0 and n_erase == 0:
            print("Flash already matches image -- nothing to do.")
        else:
            # erase-only pages first (fast Y calls)
            for kind, off in pages:
                if kind != "erase":
                    continue
                a = addr + off
                t0 = time.time()

                def do_erase(a=a):
                    flash_erase_timeout(p, a, page_size)
                with_retry("erase 0x%08x" % a, args.retries, do_erase)
                print("  erase 0x%08x (%.1fs)" % (a, time.time() - t0))

            # coalesce consecutive program pages into <= chunk_len P calls
            runs, cur = [], []
            for kind, off in pages:
                if kind == "program":
                    cur.append(off)
                    if len(cur) * page_size >= chunk_len:
                        runs.append(cur)
                        cur = []
                elif cur:
                    runs.append(cur)
                    cur = []
            if cur:
                runs.append(cur)

            total_prog = sum(len(r) for r in runs) * page_size
            done_prog = 0
            t_prog = time.time()
            for run in runs:
                # split runs longer than chunk_len (only if page < chunk)
                for i in range(0, len(run), chunk_len // page_size):
                    grp = run[i:i + chunk_len // page_size]
                    off0, off1 = grp[0], grp[-1] + page_size
                    a, blob = addr + off0, data[off0:off1]
                    t0 = time.time()

                    def do_prog(a=a, blob=blob):
                        flash_program_timeout(p, a, blob, args.timeout)
                    with_retry("program 0x%08x+%d" % (a, len(blob)),
                               args.retries, do_prog)
                    done_prog += len(blob)
                    el = time.time() - t_prog
                    print("  wrote 0x%08x +%d (%d/%d B, %3d%%, %.1f KB/s, %.1fs)"
                          % (a, len(blob), done_prog, total_prog,
                             100 * done_prog // total_prog,
                             done_prog / 1024.0 / max(el, 0.001),
                             time.time() - t0))

        if not args.no_verify:
            print("Full read-back verify...")
            t0 = time.time()
            readback = with_retry(
                "verify read", args.retries,
                lambda: read_exact(p, addr, len(data)))
            if readback != data:
                i = next(j for j in range(len(data)) if data[j] != readback[j])
                print("VERIFY FAILED at offset 0x%x (flash 0x%08x): "
                      "wrote 0x%02x, read 0x%02x"
                      % (i, addr + i, data[i], readback[i]), file=sys.stderr)
                return 1
            print("VERIFY PASS (%d bytes, %.1fs)."
                  % (len(data), time.time() - t0))

        if not args.no_reset:
            p.reset()
            time.sleep(0.3)
            p.resume()
            print("Target reset + resumed (running).")
        else:
            print("Left halted (--no-reset).")

    print("Done in %.1fs total." % (time.time() - t_all))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ProbeError as e:
        print("Probe error: %s" % e, file=sys.stderr)
        print("Hints: -4 WAIT = target stall (Blue Pill RESET button, retry); "
              "-99 = probe desync (close monitor, reopen port); "
              "-5 DPIDR = check SWD wiring/GND.", file=sys.stderr)
        sys.exit(1)
    except (FileNotFoundError, OSError) as e:
        print("File error: %s" % e, file=sys.stderr)
        sys.exit(1)
