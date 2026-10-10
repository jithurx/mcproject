import socket, struct, time

HOST, PORT = "192.168.0.192", 5555

def recvall(s, n, timeout=5):
    s.settimeout(timeout)
    data = b""
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

def xread(s, addr, nw):
    st, det, pl = cmd_payload(s, ord("X"), u32(addr) + struct.pack("<H", nw), n=nw * 4)
    return (st, det, pl)

def mwrite(s, addr, data):
    return cmd_payload(s, ord("M"), u32(addr) + u32(len(data)) + data)

def main():
    s = socket.create_connection((HOST, PORT), timeout=5)
    s.settimeout(5)
    f = 0x0801FC00

    cmd_payload(s, ord("A"))
    cmd_payload(s, ord("I"), n=12)
    cmd_payload(s, ord("U"))
    time.sleep(0.3)
    cmd_payload(s, ord("T"), n=4)
    cmd_payload(s, ord("H"))
    st, det, pl = cmd_payload(s, ord("T"), n=4)
    print("halted dhcsr=%s" % pl.hex())

    print("Y erase:", cmd_payload(s, ord("Y"), u32(f) + u32(1024)))
    print("post-erase 4w:", xread(s, f, 4))

    print("hw write @+0  (0xAA,0xBB):", mwrite(s, f + 0, bytes([0xAA, 0xBB])))
    print("  read 2b @+0:", xread(s, f, 1)[2][:2].hex())
    print("hw write @+2  (0xCC,0xDD):", mwrite(s, f + 2, bytes([0xCC, 0xDD])))
    print("  read 4b @+0:", xread(s, f, 1)[2].hex())
    print("hw write @+4  (0xEE,0xFF):", mwrite(s, f + 4, bytes([0xEE, 0xFF])))
    print("  read 8b @+0:", xread(s, f, 2)[2].hex())

    print("word write @+0 (0x11,0x22,0x33,0x44):", mwrite(s, f + 0, bytes([0x11, 0x22, 0x33, 0x44])))
    print("  read 4b @+0:", xread(s, f, 1)[2].hex())

    print("Y erase:", cmd_payload(s, ord("Y"), u32(f) + u32(1024)))
    print("byte write @+0 0x55:", mwrite(s, f + 0, bytes([0x55])))
    print("  read 2b @+0:", xread(s, f, 1)[2][:2].hex())
    print("byte write @+2 0x77:", mwrite(s, f + 2, bytes([0x77])))
    print("  read 4b @+0:", xread(s, f, 1)[2].hex())

    s.close()

if __name__ == "__main__":
    main()
