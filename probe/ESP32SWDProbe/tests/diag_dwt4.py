import socket, time, struct

HOST = "192.168.0.192"; PORT = 5555
ROUTINE = bytes([0x08, 0x46, 0x08, 0x60, 0x00, 0xBF, 0xFE, 0xE7])
RADDR = 0x20002000
WATCH = 0x20003000

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

def setup_routine(s):
    cmd(s, ord("H")); time.sleep(0.05)
    cmd(s, ord("M"), u32(RADDR) + u32(len(ROUTINE)) + ROUTINE)
    cmd(s, ord("N"), bytes([1]) + u32(WATCH))
    cmd(s, ord("N"), bytes([15]) + u32(RADDR)); time.sleep(0.02)

s = conn()
print("A:", cmd(s, ord("A"))[:2])
cmd(s, ord("H")); time.sleep(0.05)

print("== DWT registers ==")
for r, name in [(0xE0001000, "DWT_CTRL"), (0xE0001004, "DWT_CYCCNT"),
                (0xE0001010, "DWT_COMP0"), (0xE0001020, "DWT_FUNCTION0"),
                (0xE0001014, "DWT_COMP1"), (0xE0001024, "DWT_FUNCTION1"),
                (0xE0001018, "DWT_COMP2"), (0xE0001028, "DWT_FUNCTION2"),
                (0xE000101C, "DWT_COMP3"), (0xE000102C, "DWT_FUNCTION3")]:
    print("  %-18s %08x" % (name, rd32(s, r)))

print("== wp write-0x20003000, FUNCTION=0x0C, then run ==")
setup_routine(s)
wr32(s, 0xE0001020, WATCH)
wr32(s, 0xE0001024, 0x0C)
cmd(s, ord("C")); time.sleep(0.25)
dh = t(s)
print("  T=%08x halted=%d" % (dh, bool(dh & (1 << 17))))
print("  store landed at 0x20003000: %08x (expect 20003000)" % rd32(s, WATCH))
print("  DWT_FUNCTION0 after run: %08x" % rd32(s, 0xE0001024))
print("  DWT_CTRL after run:      %08x" % rd32(s, 0xE0001000))
cmd(s, ord("H")); time.sleep(0.05)
print("  pc after H: %08x" % g_pc(s))
wr32(s, 0xE0001024, 0); wr32(s, 0xE0001020, 0)

print("== try DWT_CTRL write (enable bits) ==")
print("  DWT_CTRL write 1 -> read %08x" % (wr32(s, 0xE0001000, 1) and rd32(s, 0xE0001000)))

print("== wp on code addr (0x20002006, read) FUNCTION=0x08 ==")
setup_routine(s)
wr32(s, 0xE0001020, 0x20002006)
wr32(s, 0xE0001024, 0x08)
cmd(s, ord("C")); time.sleep(0.25)
dh = t(s)
print("  T=%08x halted=%d" % (dh, bool(dh & (1 << 17))))
print("  DWT_FUNCTION0 after run: %08x" % rd32(s, 0xE0001024))
cmd(s, ord("H")); time.sleep(0.05)
print("  pc after H: %08x" % g_pc(s))
wr32(s, 0xE0001024, 0); wr32(s, 0xE0001020, 0)
s.close()
