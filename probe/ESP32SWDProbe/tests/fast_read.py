import socket, struct, time, sys

def fast_read(host, port, addr, nwords):
    s = socket.create_connection((host, port), timeout=8)
    s.settimeout(20)
    s.sendall(b"X" + struct.pack("<IH", addr, nwords))
    status = s.recv(1)
    if not status:
        s.close()
        return None, b""
    status = status[0]
    if status != 0:
        s.close()
        return status, b""
    need = nwords * 4
    data = b""
    while len(data) < need:
        chunk = s.recv(need - len(data))
        if not chunk:
            break
        data += chunk
    s.sendall(b"Q")
    s.close()
    return status, data

if __name__ == "__main__":
    host = "192.168.0.192"
    port = 5555
    tests = [
        (0x1FFFF7E0, 2, "IDCODE/FLASH size regs"),
        (0x08000000, 32, "vector table"),
        (0x08000000, 256, "1KB flash"),
        (0x08001000, 256, "4KB region"),
        (0x08004000, 32, "expected-erased region"),
    ]
    for addr, nw, label in tests:
        t0 = time.time()
        st, data = fast_read(host, port, addr, nw)
        dt = time.time() - t0
        if st == 0:
            words = struct.unpack("<%dI" % nw, data)
            print("%-28s %4d words OK in %.0fms: %s" %
                  (label, nw, dt * 1000,
                   " ".join("%08x" % w for w in words[:6])))
        else:
            print("%-28s FAIL status=%d (%.0fms)" % (label, st, dt * 1000))
