"""One-off check: read a block that straddles the AHB-AP TAR 1KB
auto-increment boundary and confirm it matches two smaller reads that don't
straddle it. Exercises the swd_read_block() TAR-rewrite fix directly."""
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


def xread(s, addr, nwords):
    import struct
    s.write(bytes([ord("X")]) + struct.pack("<IH", addr, nwords))
    hdr = recvall(s, 2)
    if len(hdr) != 2 or hdr[0] != 0:
        raise RuntimeError("X failed: hdr=%r" % hdr)
    return recvall(s, nwords * 4)


def main():
    s = serial.Serial(PORT, 115200, timeout=5)
    wait_boot(s)

    base = 0x08000000
    # 300 words = 1200 bytes, crosses the 256-word/1KB boundary once.
    big = xread(s, base, 300)
    # Two reads that don't straddle the boundary, covering the same range.
    part1 = xread(s, base, 256)
    part2 = xread(s, base + 256 * 4, 44)

    ok = (big[:256 * 4] == part1) and (big[256 * 4:] == part2)
    print("big[0:1024]   == part1 (pre-boundary) :", big[:256 * 4] == part1)
    print("big[1024:1200]== part2 (post-boundary) :", big[256 * 4:] == part2)
    print("RESULT:", "PASS" if ok else "FAIL")
    s.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
