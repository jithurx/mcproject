import socket, struct, time, sys

def recv_exact(s, n, timeout=15):
    s.settimeout(timeout)
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    return data

def fast_read(host, port, addr, nwords, timeout=20):
    s = socket.create_connection((host, port), timeout=6)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        s.sendall(b"X" + struct.pack("<IH", addr, nwords))
        hdr = recv_exact(s, 2, timeout)
        if len(hdr) < 2:
            return (-1, 0), b""
        status, detail = hdr[0], hdr[1]
        if status != 0:
            return (status, detail), b""
        data = recv_exact(s, nwords * 4, timeout)
        return (0, 0), data
    finally:
        try:
            s.sendall(b"Q")
        except Exception:
            pass
        s.close()

# wait for probe to come up after flash reset
deadline = time.time() + 45
while time.time() < deadline:
    try:
        s = socket.create_connection(("192.168.0.192", 5555), timeout=3)
        s.close()
        print("probe TCP up after %.1fs" % (45 - (deadline - time.time())))
        break
    except Exception:
        time.sleep(1)
else:
    print("probe never came up")
    sys.exit(1)

tests = [
    (0x08000000, 1024, "vector+startup (4KB)"),
    (0x08001000, 1024, "flash 4K-8K"),
    (0x08002000, 1024, "flash 8K-12K"),
    (0x08003000, 1024, "flash 12K-16K"),
]
for addr, nw, label in tests:
    t0 = time.time()
    try:
        st, data = fast_read("192.168.0.192", 5555, addr, nw)
        dt = time.time() - t0
        if st[0] == 0:
            words = struct.unpack("<%dI" % nw, data)
            print("%-22s %4d words OK in %.0fms: %s" %
                  (label, nw, dt * 1000, " ".join("%08x" % w for w in words[:8])))
        else:
            print("%-22s FAIL st=%s (%.0fms)" % (label, st, dt * 1000))
    except Exception as e:
        print("%-22s EXC %s" % (label, e))

# save full 16KB
allbuf = bytearray()
for addr in range(0x08000000, 0x08004000, 0x400):
    st, data = fast_read("192.168.0.192", 5555, addr, 0x100)
    if st[0] != 0:
        print("dump chunk at 0x%08x FAIL st=%s" % (addr, st))
        break
    allbuf += data
open(r"C:\Users\cecilia\AppData\Local\Temp\opencode\stm32_dump_0x08000000_16k.bin", "wb").write(bytes(allbuf))
print("saved %d bytes to stm32_dump_0x08000000_16k.bin" % len(allbuf))
