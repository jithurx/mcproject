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

def halt_setup(s, code, raddr, r1):
    cmd(s, ord("H")); time.sleep(0.05)
    cmd(s, ord("M"), u32(raddr) + u32(len(code)) + code)
    cmd(s, ord("N"), bytes([1]) + u32(r1))
    cmd(s, ord("N"), bytes([15]) + u32(raddr)); time.sleep(0.02)

STRW = bytes([0x08, 0x46, 0x08, 0x60, 0x00, 0xBF, 0xFE, 0xE7])
STRB = bytes([0x08, 0x46, 0x08, 0x70, 0x00, 0xBF, 0xFE, 0xE7])

s = conn()
print("A:", cmd(s, ord("A"))[:2])

print("== CYCCNT functional test ==")
cmd(s, ord("H")); time.sleep(0.05)
wr32(s, 0xE0001004, 0xFFFFFFFF)   # seed CYCCNT
wr32(s, 0xE0001000, 1)            # CYCCNTENA
halt_setup(s, STRW, 0x20002000, 0x20003000)
cmd(s, ord("C")); time.sleep(0.2)
cmd(s, ord("H")); time.sleep(0.05)
print("  CYCCNT after ~200ms run: %08x" % rd32(s, 0xE0001004))
wr32(s, 0xE0001000, 0)

def wp_test(s, label, comp, func, code, r1):
    halt_setup(s, code, 0x20002000, r1)
    wr32(s, 0xE0001020, comp)
    wr32(s, 0xE0001024, func)
    cmd(s, ord("C")); time.sleep(0.25)
    dh = t(s)
    fn = rd32(s, 0xE0001024)
    wr32(s, 0xE0001024, 0); wr32(s, 0xE0001020, 0)
    cmd(s, ord("H")); time.sleep(0.05)
    pc = g_pc(s)
    print("  %-42s fired=%s dh=%08x fn=%08x pc=%08x" % (
        label, bool(dh & (1 << 17)), dh, fn, pc))

print("== watchpoint tests ==")
wp_test(s, "str word to 0x20003000 comp=20003000 f=0C", 0x20003000, 0x0C, STRW, 0x20003000)
wp_test(s, "str word to 0x20003000 comp=20003000 f=0C (re-arm)", 0x20003000, 0x0C, STRW, 0x20003000)
wp_test(s, "str byte to 0x20003000 comp=20003000 f=0C", 0x20003000, 0x0C, STRB, 0x20003000)
wp_test(s, "str byte to 0x20003000 comp=20003000 f=0C2(MATCHED bit)", 0x20003000, 0x0C0, STRB, 0x20003000)
wp_test(s, "str byte to 0x20003000 comp=20003000 f=0x08 read", 0x20003000, 0x08, STRB, 0x20003000)
wp_test(s, "str word to 0x20003002 comp=20003002 f=0C", 0x20003002, 0x0C, STRW, 0x20003002)
wp_test(s, "str word to 0x20003000 comp=20003000 f=0x10 rw", 0x20003000, 0x10, STRW, 0x20003000)
wp_test(s, "str word to 0x20003000 comp=20003000 f=0x12 rw+wp", 0x20003000, 0x12, STRW, 0x20003000)
wp_test(s, "str word to 0x20003000 comp=20003000 f=0x0E write+wp", 0x20003000, 0x0E, STRW, 0x20003000)
wp_test(s, "str word to 0x20003000 comp=20003000 f=0x7 data addr val", 0x20003000, 0x07, STRW, 0x20003000)
s.close()
