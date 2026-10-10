import socket, time, struct

HOST = "192.168.0.192"; PORT = 5555

def conn():
    s = socket.create_connection((HOST, PORT), timeout=5); s.settimeout(5); return s

def recvn(s, n, timeout=8):
    s.settimeout(timeout); data = b""
    while len(data) < n:
        c = s.recv(n - len(data))
        if not c: raise RuntimeError("closed")
        data += c
    return data

def cmd(s, b, p=b"", n=0):
    s.sendall(bytes([b]) + p)
    st, det = recvn(s, 2)
    return (st, det, recvn(s, n) if n else b"")

def u32(x): return struct.pack("<I", x)

def wr(s, addr, val):
    return cmd(s, ord("M"), u32(addr) + u32(4) + u32(val))[:2]

def rd(s, addr):
    st, det, pl = cmd(s, ord("X"), u32(addr) + struct.pack("<H", 1), n=4)
    return struct.unpack("<I", pl)[0] if st == 0 else None

s = conn()
cmd(s, ord("A"))
cmd(s, ord("H")); time.sleep(0.05)

tests = [
    (0xE0002000, "FPB_CTRL"),
    (0xE0002008, "FPB_COMP0"),
    (0xE0002018, "FPB_COMP2"),
    (0xE0001020, "DWT_COMP0"),
    (0xE0001024, "DWT_FUNC0"),
    (0x20004000, "SRAM"),
    (0xE000EDFC, "DEMCR"),
]
val = 0xA5A55A5A
for addr, name in tests:
    wr(s, addr, val)
    r = rd(s, addr)
    print("%-12s @%08x wr=%08x rd=%08x  %s" % (name, addr, val, r, "OK" if r == val else "MISMATCH"))
s.close()
