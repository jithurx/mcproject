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
    cmd(s, ord("N"), bytes([23]) + u32(1))
    cmd(s, ord("N"), bytes([15]) + u32(RADDR)); time.sleep(0.02)

def run_wp(s, func):
    setup_routine(s)
    wr32(s, 0xE0001020, WATCH)
    wr32(s, 0xE0001024, func)
    rb = rd32(s, 0xE0001024)
    cmd(s, ord("C")); time.sleep(0.25)
    dh = t(s)
    fired = bool(dh & (1 << 17))
    cmd(s, ord("H")); time.sleep(0.05)
    pc = g_pc(s)
    wr32(s, 0xE0001024, 0)
    wr32(s, 0xE0001020, 0)
    return fired, dh, pc, rb

s = conn()
print("A:", cmd(s, ord("A"))[:2])
cmd(s, ord("H")); time.sleep(0.05)

print("== writable DWT_FUNC0 bits ==")
wr32(s, 0xE0001024, 0x3FF)
print("  write 0x3FF -> read %08x" % rd32(s, 0xE0001024))
wr32(s, 0xE0001024, 0x3CC)
print("  write 0x3CC -> read %08x" % rd32(s, 0xE0001024))
wr32(s, 0xE0001024, 0)

print("== watchpoint fire tests (halt+pc check) ==")
for func in [0x0C, 0x3CC, 0x0D, 0x3CD, 0x08, 0x10, 0x07, 0x3C7, 0x2C]:
    fired, dh, pc, rb = run_wp(s, func)
    print("  func=%02x (rb=%08x) fired=%s dhcsr=%08x pc=%08x" % (
        func, rb, fired, dh, pc))
s.close()
