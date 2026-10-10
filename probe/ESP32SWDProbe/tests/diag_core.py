import socket, time, struct

HOST = "192.168.0.192"; PORT = 5555
ROUTINE = bytes([0x08, 0x46, 0x08, 0x60, 0x00, 0xBF, 0xFE, 0xE7])
RADDR = 0x20002000

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

def rd32(s, addr):
    st, det, pl = cmd(s, ord("X"), u32(addr) + struct.pack("<H", 1), n=4)
    return struct.unpack("<I", pl)[0] if st == 0 else 0

def g(s):
    st, det, pl = cmd(s, ord("G"), n=68)
    if st != 0: return [0]*17
    return struct.unpack("<17I", pl)

def t(s):
    st, det, pl = cmd(s, ord("T"), n=4)
    return struct.unpack("<I", pl)[0] if st == 0 else 0

s = conn()
print("A:", cmd(s, ord("A"))[:2])
cmd(s, ord("H")); time.sleep(0.05)
print("T after H: %08x halted=%d" % (t(s), bool(t(s) & (1 << 17))))

# load routine and read back
cmd(s, ord("M"), u32(RADDR) + u32(len(ROUTINE)) + ROUTINE)
print("routine bytes:", " ".join("%02x" % rd32(s, RADDR + i) for i in range(0, 8, 4)))

cmd(s, ord("N"), bytes([1]) + u32(0x20003000))
cmd(s, ord("N"), bytes([15]) + u32(RADDR)); time.sleep(0.02)
print("pc after N:", hex(g(s)[15]))

cmd(s, ord("C")); time.sleep(0.1)
print("T after C: %08x halted=%d" % (t(s), bool(t(s) & (1 << 17))))
for i in range(3):
    print("  running pc read %d: %08x" % (i, g(s)[15]))
    time.sleep(0.02)

cmd(s, ord("H")); time.sleep(0.05)
print("T after H2: %08x halted=%d" % (t(s), bool(t(s) & (1 << 17))))
print("pc after H2 (clean): %08x  (want %08x if loop ran)" % (g(s)[15], RADDR + 6))
s.close()
