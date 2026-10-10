#!/usr/bin/env python3
"""Full-chip flash backup and restore for an STM32 target, via the ESP32
SWD probe.

Usage:
    python dump_restore.py dump COM10 backup.bin
    python dump_restore.py dump COM10 backup.bin --address 0x08000000 --length 0x20000
    python dump_restore.py restore COM10 backup.bin

`dump` auto-detects the target's flash size (via the probe's 'K'/'I'
commands) unless --length is given, so a plain `dump COM10 backup.bin`
reads exactly the flash that's actually there instead of a hardcoded size.
`restore` calls the same chunked, self-verifying flash_program_bulk() that
flash_cli.py uses, so restoring is just "flash this exact byte-for-byte
image back."
"""
import argparse
import sys
import time

from probe_client import Probe, ProbeError

FLASH_BASE = 0x08000000


def do_dump(port, out_path, address, length, halt_first):
    with Probe(port) as p:
        p.alive()
        if length is None:
            length = p.flash_bytes_total()
            print("Auto-detected flash size: %d bytes (0x%x)" % (length, length))
        if halt_first:
            p.halt()
            print("Target halted (read-while-running also works; halting just "
                  "guarantees a consistent snapshot instead of racing a running core).")

        print("Dumping 0x%08x .. 0x%08x (%d bytes) from %s..." % (address, address + length, length, port))
        t0 = time.time()
        data = bytearray()
        a, remaining = address, length
        while remaining:
            n = min(remaining, 4096)
            data += p.read_mem(a, n)
            a += n
            remaining -= n
            sys.stdout.write("\r  %3d%%" % (100 * (length - remaining) // length))
            sys.stdout.flush()
        elapsed = time.time() - t0
        print("\nRead %d bytes in %.1fs (%.1f KB/s)." % (len(data), elapsed, len(data) / 1024.0 / max(elapsed, 0.001)))

        with open(out_path, "wb") as f:
            f.write(data)
        print("Saved to %s" % out_path)
    return 0


def do_restore(port, in_path, address, do_verify, do_reset):
    with open(in_path, "rb") as f:
        data = f.read()
    if len(data) % 2:
        data += b"\xff"

    print("Connecting to %s..." % port)
    with Probe(port) as p:
        p.alive()
        cpuid, page_size, dual_bank, ap_sel = p.part_info()
        print("Target: CPUID=0x%08x  page_size=%d  dual_bank=%s" % (cpuid, page_size, dual_bank))

        print("Restoring %d bytes to 0x%08x..." % (len(data), address))
        t0 = time.time()

        def progress(done, total):
            sys.stdout.write("\r  %3d%%" % (100 * done // total))
            sys.stdout.flush()

        p.flash_program_bulk(address, data, on_progress=progress)
        print("\nRestored in %.1fs." % (time.time() - t0))

        if do_verify:
            print("Verifying...")
            readback = p.read_mem(address, len(data))
            if readback != data:
                first_diff = next(i for i in range(len(data)) if data[i] != readback[i])
                print("VERIFY FAILED at offset 0x%x" % first_diff, file=sys.stderr)
                return 1
            print("Verify OK.")

        if do_reset:
            p.reset()
            print("Target reset.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)

    d = sub.add_parser("dump", help="Read the whole (or a range of) flash to a file")
    d.add_argument("port")
    d.add_argument("out_file")
    d.add_argument("--address", type=lambda x: int(x, 0), default=FLASH_BASE)
    d.add_argument("--length", type=lambda x: int(x, 0), default=None,
                   help="Bytes to read (default: auto-detect full flash size)")
    d.add_argument("--no-halt", action="store_true",
                   help="Read without halting first (fine for flash, since it's read-only from "
                        "the AHB-AP's perspective regardless of core state)")

    r = sub.add_parser("restore", help="Program a previously dumped file back to flash")
    r.add_argument("port")
    r.add_argument("in_file")
    r.add_argument("--address", type=lambda x: int(x, 0), default=FLASH_BASE)
    r.add_argument("--no-verify", action="store_true")
    r.add_argument("--no-reset", action="store_true")

    args = ap.parse_args()
    try:
        if args.action == "dump":
            return do_dump(args.port, args.out_file, args.address, args.length, not args.no_halt)
        return do_restore(args.port, args.in_file, args.address, not args.no_verify, not args.no_reset)
    except ProbeError as e:
        print("Probe error: %s" % e, file=sys.stderr)
        return 1
    except (FileNotFoundError, OSError) as e:
        print("File error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
