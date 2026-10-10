"""One-off check for the 'K' native command (extended part identification:
CPUID, flash page size, dual-bank flag, auto-probed AP index).

Expects a Cortex-M3 (CPUID PartNo 0xC23) on this board's STM32F103, medium
density (0x400-byte pages, single bank), found on AP 0."""
import struct
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM10"


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


def main():
    s = serial.Serial(PORT, 115200, timeout=5)
    wait_boot(s)

    s.write(bytes([ord("K")]))
    hdr = recvall(s, 2)
    if len(hdr) != 2 or hdr[0] != 0:
        print("K failed: hdr=%r" % hdr)
        return 1
    pl = recvall(s, 10)
    cpuid, page_size = struct.unpack("<II", pl[:8])
    dual_bank, ap_sel = pl[8], pl[9]
    part_no = (cpuid >> 4) & 0xFFF
    implementer = (cpuid >> 24) & 0xFF

    print("cpuid=0x%08x (implementer=0x%02x partno=0x%03x) page_size=0x%x dual_bank=%d ap_sel=%d"
          % (cpuid, implementer, part_no, page_size, dual_bank, ap_sel))

    ok = (implementer == 0x41 and part_no == 0xC23 and
          page_size == 0x400 and dual_bank == 0 and ap_sel == 0)
    print("RESULT:", "PASS" if ok else "FAIL")
    s.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
