import socket, struct, sys, time

HOST, PORT = "192.168.0.192", 5555

def recvall(s, n, timeout=5):
    s.settimeout(timeout)
    data = b""
    t0 = time.time()
    while len(data) < n:
        try:
            chunk = s.recv(n - len(data))
        except socket.timeout:
            break
        if not chunk:
            break
        data += chunk
    return data

def cmd_payload(s, op, body=b"", n=0):
    s.sendall(bytes([op]) + body)
    hdr = recvall(s, 2)
    if len(hdr) != 2:
        return (-99, 0, b"")
    st, det = hdr[0], hdr[1]
    pl = recvall(s, n) if n and st == 0 else b""
    return (st, det, pl)

def u32(v):
    return struct.pack("<I", v)

def main():
    s = socket.create_connection((HOST, PORT), timeout=5)
    s.settimeout(5)

    print("A alive:", cmd_payload(s, ord("A")))
    print("I info:", cmd_payload(s, ord("I"), n=12))
    print("U sysreset:", cmd_payload(s, ord("U")))
    time.sleep(0.3)
    st, det, pl = cmd_payload(s, ord("T"), n=4)
    print("T after reset:", (st, det), pl.hex())
    print("H halt:", cmd_payload(s, ord("H")))
    st, det, pl = cmd_payload(s, ord("T"), n=4)
    print("T halted:", (st, det), pl.hex())

    faddr = 0x0801FC00
    fdata = bytes((i * 7) & 0xFF for i in range(128))

    print("Y erase 1024 @ 0801FC00:", cmd_payload(s, ord("Y"), u32(faddr) + u32(1024)))
    st, det, pl = cmd_payload(s, ord("X"), u32(faddr) + struct.pack("<H", 8), n=32)
    print("X after erase 8w:", st, det, pl.hex())
    assert all(b == 0xFF for b in pl)

    st, det, _ = cmd_payload(s, ord("P"), u32(faddr) + u32(len(fdata)) + fdata)
    print("P program 128:", (st, det))

    st, det, pl = cmd_payload(s, ord("X"), u32(faddr) + struct.pack("<H", len(fdata) // 4), n=len(fdata))
    print("X readback 128:", (st, det))
    for i in range(0, 128, 16):
        e = fdata[i:i+16]
        g = pl[i:i+16]
        m = "OK " if e == g else "DIFF"
        print("  +%03x  got %s  exp %s  %s" % (i, g.hex(), e.hex(), m))
    print("readback==data:", pl == fdata)

    st, det, pl = cmd_payload(s, ord("X"), u32(faddr) + struct.pack("<H", 256), n=1024)
    print("X full page readback first 32:", pl[:128].hex())
    print("X full page readback tail:", pl[896:].hex())

    s.close()

if __name__ == "__main__":
    main()
