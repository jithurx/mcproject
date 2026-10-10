import socket, time, struct

HOST = "192.168.0.192"; PORT = 5555
STRW = bytes([0x08, 0x46, 0x08, 0x60, 0x00, 0xBF, 0xFE, 0xE7])

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

def wr32(s, addr, val):
    return cmd(s, ord("M"), u32(addr) + u32(4) + u32(val))[:2]

def g_pc(s):
    st, det, pl = cmd(s, ord("G"), n=68)
    return struct.unpack("<17I", pl)[15] if st == 0 else 0

def t(s):
    st, det, pl = cmd(s, ord("T"), n=4)
    return struct.unpack("<I", pl)[0] if st == 0 else 0

s = conn()
print("A:", cmd(s, ord("A"))[:2])

print("DEMCR read:      %08x" % rd32(s, 0xE000EDFC))
print("DEMCR write 0xA5A55A5A -> read %08x" % (wr32(s, 0xE000EDFC, 0xA5A55A5A) and rd32(s, 0xE000EDFC)))
print("DEMCR write TRCENA(bit24) -> read %08x" % (wr32(s, 0xE000EDFC, 0x01000000) and rd32(s, 0xE000EDFC)))

def wp_test(s, label, comp, func):
    cmd(s, ord("H")); time.sleep(0.05)
    cmd(s, ord("M"), u32(0x20002000) + u32(len(STRW)) + STRW)
    cmd(s, ord("N"), bytes([1]) + u32(comp))
    cmd(s, ord("N"), bytes([15]) + u32(0x20002000)); time.sleep(0.02)
    wr32(s, 0xE0001020, comp)
    wr32(s, 0xE0001024, func)
    cmd(s, ord("C")); time.sleep(0.25)
    dh = t(s)
    fn = rd32(s, 0xE0001024)
    wr32(s, 0xE0001024, 0); wr32(s, 0xE0001020, 0)
    cmd(s, ord("H")); time.sleep(0.05)
    pc = g_pc(s)
    print("  %-46s fired=%s dh=%08x fn=%08x pc=%08x" % (
        label, bool(dh & (1 << 17)), dh, fn, pc))

print("== after TRCENA set ==")
wp_test(s, "str word comp=20003000 f=0C", 0x20003000, 0x0C)
wp_test(s, "str word comp=20003000 f=0E", 0x20003000, 0x0E)
wp_test(s, "str word comp=20003000 f=0A read wp", 0x20003000, 0x0A)
wp_test(s, "str word comp=20003000 f=09 read dbev", 0x20003000, 0x09)
wp_test(s, "str word comp=20003000 f=0D write dbev", 0x20003000, 0x0D)
s.close()
