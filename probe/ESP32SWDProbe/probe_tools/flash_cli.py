#!/usr/bin/env python3
"""Flash a .bin or .hex file to an STM32 target through the ESP32 SWD probe.

Unlike flash_bridge.py (which goes through the target's own USB-serial
bootloader over an ESP32 UART passthrough), this goes over SWD via
esp32_swd_probe.ino -- no BOOT0 jumper, no bootloader entry sequence, and it
works even if the target firmware never wired up a bootloader-compatible
UART. Requires the probe to be wired to the target's SWDIO/SWCLK/GND (see
esp32_swd_probe/esp32_swd_probe.ino's header comment) and running in its
default SERIAL_ONLY mode.

Usage:
    python flash_cli.py COM10 firmware.bin
    python flash_cli.py COM10 firmware.bin --address 0x08000000
    python flash_cli.py COM10 firmware.hex                    # address from the hex file itself
    python flash_cli.py COM10 firmware.bin --mass-erase-first # wipe the whole chip first
    python flash_cli.py COM10 firmware.bin --no-verify --no-reset

'P' (flash_program) already erases the pages it's about to write, so a
separate erase step is redundant for same-size-or-larger reflashes -- it's
only offered here (--mass-erase-first) for the case where the new image is
*smaller* than whatever was flashed before, and you want the leftover tail
wiped rather than left in place.
"""
import argparse
import sys
import time

from probe_client import Probe, ProbeError

DEFAULT_FLASH_BASE = 0x08000000


def load_image(path, address):
    """Returns (address, data). For .hex, the address comes from the file
    itself (matching flash_bridge.py's handling of Intel HEX images)."""
    if path.lower().endswith(".hex"):
        import intelhex

        ih = intelhex.IntelHex()
        ih.loadhex(path)
        min_addr = ih.minaddr()
        data = bytes(ih.tobinarray(start=min_addr, end=ih.maxaddr()))
        return min_addr, data
    with open(path, "rb") as f:
        return address, f.read()


def flash(port, path, address, mass_erase_first, do_verify, do_reset):
    addr, data = load_image(path, address)
    if len(data) % 2:
        data += b"\xff"  # 'P' requires an even length; pad with erased-flash value

    print("Connecting to %s..." % port)
    with Probe(port) as p:
        p.alive()
        print("Probe alive.")

        cpuid, page_size, dual_bank, ap_sel = p.part_info()
        print("Target: CPUID=0x%08x  page_size=%d  dual_bank=%s  AP=%d"
              % (cpuid, page_size, dual_bank, ap_sel))

        if mass_erase_first:
            print("Mass-erasing target flash...")
            t0 = time.time()
            p.flash_erase(0, 0)
            print("Erased in %.1fs." % (time.time() - t0))

        print("Programming %d bytes at 0x%08x..." % (len(data), addr))
        t0 = time.time()

        def progress(done, total):
            sys.stdout.write("\r  %3d%% (%d/%d bytes)" % (100 * done // total, done, total))
            sys.stdout.flush()

        p.flash_program_bulk(addr, data, on_progress=progress)
        print("\nProgrammed in %.1fs (%.1f KB/s)." % (
            time.time() - t0, len(data) / 1024.0 / max(time.time() - t0, 0.001)))

        if do_verify:
            print("Verifying...")
            readback = p.read_mem(addr, len(data))
            if readback != data:
                first_diff = next(i for i in range(len(data)) if data[i] != readback[i])
                print("VERIFY FAILED at offset 0x%x: wrote 0x%02x, read 0x%02x"
                      % (first_diff, data[first_diff], readback[first_diff]), file=sys.stderr)
                return 1
            print("Verify OK.")

        if do_reset:
            p.reset()
            print("Target reset.")

    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", help="Serial port the ESP32 probe is on, e.g. COM10")
    ap.add_argument("image", help="Firmware file (.bin or .hex)")
    ap.add_argument("--address", type=lambda x: int(x, 0), default=DEFAULT_FLASH_BASE,
                     help="Flash address for a .bin file (default 0x08000000; ignored for .hex)")
    ap.add_argument("--mass-erase-first", action="store_true",
                     help="Erase the whole chip before programming (see module docstring for why "
                          "this usually isn't necessary)")
    ap.add_argument("--no-verify", action="store_true", help="Skip read-back verification")
    ap.add_argument("--no-reset", action="store_true", help="Don't reset the target after flashing")
    args = ap.parse_args()

    try:
        return flash(args.port, args.image, args.address,
                      args.mass_erase_first, not args.no_verify, not args.no_reset)
    except ProbeError as e:
        print("Probe error: %s" % e, file=sys.stderr)
        return 1
    except (FileNotFoundError, OSError) as e:
        print("File error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
