#!/usr/bin/env python3
"""Dump a human-readable snapshot of whatever STM32 target is attached to
the ESP32 SWD probe: chip identity, flash geometry, halt state, and (if
halted) every core register -- or, if left running, the reset vector read
straight out of flash instead.

Usage:
    python introspect.py COM10
    python introspect.py COM10 --halt      # halt first so core registers are readable
    python introspect.py COM10 --json      # machine-readable output
"""
import argparse
import json
import struct
import sys

from probe_client import Probe, ProbeError

# ARMv7-M CPUID PARTNO field (bits [15:4]) -> readable name.
CORTEX_M_PARTS = {
    0xC20: "Cortex-M0",
    0xC60: "Cortex-M0+",
    0xC21: "Cortex-M1",
    0xC23: "Cortex-M3",
    0xC24: "Cortex-M4",
    0xC27: "Cortex-M7",
    0xD21: "Cortex-M33",
}

# STM32F1 DBGMCU_IDCODE low 12 bits -> density/family, per ST's reference
# manual (same table flash_identify() in the firmware uses).
STM32F1_DENSITY = {
    0x412: "low density",
    0x410: "medium density",
    0x414: "high density",
    0x418: "connectivity line",
    0x420: "medium density value line",
    0x428: "high density value line",
    0x430: "XL-density (dual bank)",
}

REG_NAMES = ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
             "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc", "xpsr"]


def snapshot(port, do_halt):
    with Probe(port) as p:
        p.alive()
        if do_halt:
            p.halt()

        dpidr, fsize_reg, idcode = p.chip_info()
        cpuid, page_size, dual_bank, ap_sel = p.part_info()
        dhcsr = p.status()
        halted = bool(dhcsr & (1 << 17))  # DHCSR.S_HALT

        device_id = idcode & 0xFFF
        part_no = (cpuid >> 4) & 0xFFF

        info = {
            "port": port,
            "dpidr": dpidr,
            "dbgmcu_idcode": idcode,
            "device_id": device_id,
            "device_family": STM32F1_DENSITY.get(device_id, "unknown (0x%03x)" % device_id),
            "flash_bytes": (fsize_reg & 0xFFFF) * 1024,
            "flash_page_size": page_size,
            "dual_bank": dual_bank,
            "ap_sel": ap_sel,
            "cpuid": cpuid,
            "cpu_implementer": (cpuid >> 24) & 0xFF,
            "cpu_part_no": part_no,
            "cpu_part_name": CORTEX_M_PARTS.get(part_no, "unknown (0x%03x)" % part_no),
            "cpu_variant": (cpuid >> 20) & 0xF,
            "cpu_revision": cpuid & 0xF,
            "dhcsr": dhcsr,
            "halted": halted,
        }

        if halted:
            regs = p.read_regs()
            info["registers"] = dict(zip(REG_NAMES, regs))
            info["halt_reason_dfsr"] = p.halt_reason()
        else:
            info["registers"] = None
            # No halt needed for this: flash is readable via the AHB-AP
            # regardless of whether the core is halted or running.
            vt = p.read_mem(0x08000000, 8)
            initial_sp, reset_vector = struct.unpack("<II", vt)
            info["vector_table_initial_sp"] = initial_sp
            info["vector_table_reset_vector"] = reset_vector

        return info


def render(info):
    lines = []
    lines.append("=== %s ===" % info["port"])
    lines.append("DPIDR            0x%08x" % info["dpidr"])
    lines.append("CPU              %s (part 0x%03x, r%dp%d)" % (
        info["cpu_part_name"], info["cpu_part_no"], info["cpu_variant"], info["cpu_revision"]))
    lines.append("DBGMCU_IDCODE    0x%08x  (device 0x%03x: %s)" % (
        info["dbgmcu_idcode"], info["device_id"], info["device_family"]))
    lines.append("Flash            %d bytes, %d-byte pages, dual_bank=%s" % (
        info["flash_bytes"], info["flash_page_size"], info["dual_bank"]))
    lines.append("Debug AP index   %d" % info["ap_sel"])
    lines.append("DHCSR            0x%08x  (%s)" % (
        info["dhcsr"], "HALTED" if info["halted"] else "running"))

    if info["registers"]:
        r = info["registers"]
        lines.append("")
        lines.append("Core registers:")
        for i in range(0, 12, 4):
            row = ["r%d" % n for n in range(i, min(i + 4, 12))]
            lines.append("  " + "  ".join("%-4s 0x%08x" % (n, r[n]) for n in row))
        lines.append("  %-4s 0x%08x  %-4s 0x%08x" % ("r12", r["r12"], "sp", r["sp"]))
        lines.append("  %-4s 0x%08x  %-4s 0x%08x  %-4s 0x%08x" % ("lr", r["lr"], "pc", r["pc"], "xpsr", r["xpsr"]))
        lines.append("Halt reason (DFSR) 0x%08x" % info["halt_reason_dfsr"])
    else:
        lines.append("")
        lines.append("Target is running; pass --halt to read core registers too.")
        lines.append("Reset vector (read directly from flash, no halt needed):")
        lines.append("  initial SP    0x%08x" % info["vector_table_initial_sp"])
        lines.append("  reset vector  0x%08x" % info["vector_table_reset_vector"])

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", help="Serial port the ESP32 probe is on, e.g. COM10")
    ap.add_argument("--halt", action="store_true", help="Halt the target first so core registers can be read")
    ap.add_argument("--json", action="store_true", help="Machine-readable output")
    args = ap.parse_args()

    try:
        info = snapshot(args.port, args.halt)
    except ProbeError as e:
        print("Probe error: %s" % e, file=sys.stderr)
        return 1

    print(json.dumps(info, indent=2) if args.json else render(info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
