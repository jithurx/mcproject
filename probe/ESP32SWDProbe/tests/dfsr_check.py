"""One-off check: set a breakpoint, resume, and read back the halt state and
DFSR halt reason via the new 'J' native command.

NOTE: as of this writing, this reproducibly FAILS -- DHCSR stays
"running" after 'C' with an FPB breakpoint armed, i.e. the breakpoint does
not actually halt the core. test_stlink2.py's "F bp hit on resume" check
only compares a register readback (pc == FLASH_LOOP) without checking the
halted bit, so it reports PASS on a stale DCRDR value even when the core
never halted -- that assertion should be tightened once the underlying FPB
issue is root-caused. See IMPROVEMENT_PLAN.md section 6 ("FPB resume timing
FAIL"). Suspect: FP_CTRL's KEY bit may not be bit31 as bp_set()/handle
assume -- worth checking against a reference (blackmagic cortexm.c) rather
than guessing.
"""
import struct
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM10"
FLASH_LOOP = 0x08001520


def wait_boot(s):
    s.timeout = 1.0
    t0 = time.time()
    last_data = time.time()
    while time.time() - t0 < 10:
        if s.in_waiting:
            s.read(s.in_waiting)
            last_data = time.time()
        elif time.time() - last_data > 1.0:
            break
        else:
            time.sleep(0.05)
    s.reset_input_buffer()


def recvall(s, n, timeout=5):
    s.timeout = timeout
    data = b""
    while len(data) < n:
        chunk = s.read(n - len(data))
        if not chunk:
            break
        data += chunk
    return data


def cmd(s, op, body=b"", n=0):
    s.write(bytes([op]) + body)
    hdr = recvall(s, 2)
    if len(hdr) != 2:
        return (-99, 0, b"")
    st, det = hdr[0], hdr[1]
    pl = recvall(s, n) if n and st == 0 else b""
    return (st, det, pl)


def u32(v):
    return struct.pack("<I", v)


def write_reg(s, sel, val):
    st, det, _ = cmd(s, ord("N"), bytes([sel]) + u32(val))
    return st == 0


def main():
    s = serial.Serial(PORT, 115200, timeout=5)
    wait_boot(s)

    cmd(s, ord("U"))
    time.sleep(0.7)
    cmd(s, ord("H"))
    time.sleep(0.05)
    write_reg(s, 15, FLASH_LOOP)
    st, det, pl = cmd(s, ord("F"), bytes([1]) + u32(FLASH_LOOP), n=1)
    print("bp set:", st, det, pl.hex() if pl else "")

    cmd(s, ord("C"))
    time.sleep(0.15)
    st, det, pl = cmd(s, ord("T"), n=4)
    dhcsr = struct.unpack("<I", pl)[0] if st == 0 else 0
    halted = bool(dhcsr & (1 << 17))
    print("dhcsr=%08x halted=%s" % (dhcsr, halted))

    st, det, pl = cmd(s, ord("J"), n=4)
    dfsr = struct.unpack("<I", pl)[0] if st == 0 else None
    print("J halt reason: st=%d dfsr=%s" % (st, ("%08x" % dfsr) if dfsr is not None else "None"))
    if dfsr is not None:
        names = []
        if dfsr & 0x01: names.append("HALTED")
        if dfsr & 0x02: names.append("BKPT")
        if dfsr & 0x04: names.append("DWTTRAP")
        if dfsr & 0x08: names.append("VCATCH")
        if dfsr & 0x10: names.append("EXTERNAL")
        print("  decoded:", ",".join(names) if names else "(none)")

    cmd(s, ord("F"), bytes([0]) + u32(FLASH_LOOP), n=1)
    cmd(s, ord("C"))
    cmd(s, ord("U"))
    s.close()

    ok = halted and dfsr is not None and (dfsr & 0x02)
    print("RESULT:", "PASS (BKPT bit set)" if ok else "FAIL (core did not actually halt on breakpoint)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
