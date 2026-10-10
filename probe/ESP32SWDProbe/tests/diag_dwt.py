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

def u32(x): return struct.pack("<I", x)

def raw_cmd(s, b, p=b"", n=0):
    s.sendall(bytes([b]) + p)
    head = recvn(s, 2)
    pl = recvn(s, n) if n else b""
    return head, pl

def cmd(s, b, p=b"", n=0):
    head, pl = raw_cmd(s, b, p, n)
    return head[0], head[1], pl

def rd32(s, addr):
    st, det, pl = cmd(s, ord("X"), u32(addr) + struct.pack("<H", 1), n=4)
    return struct.unpack("<I", pl)[0] if st == 0 else None

def wr32(s, addr, val):
    return cmd(s, ord("M"), u32(addr) + u32(4) + u32(val))[:2]

def read_pc(s):
    st, det, pl = cmd(s, ord("G"), n=68)
    return struct.unpack("<17I", pl)[15] if st == 0 else 0

s = conn()
print("A:", cmd(s, ord("A"))[:2])
print("H:", cmd(s, ord("H"))[:2]); time.sleep(0.05)

print("-- DWT register write/readback in isolation --")
wr32(s, 0xE0001020, 0x20003000)
print("  comp0=%08x (want 20003000)" % rd32(s, 0xE0001020))
wr32(s, 0xE0001024, 0x07)
print("  func0=%08x (want 00000007)" % rd32(s, 0xE0001024))
wr32(s, 0xE0001024, 0)
wr32(s, 0xE0001020, 0)

print("-- V wp set via command --")
st, det, pl = cmd(s, ord("V"), bytes([1]) + u32(0x20003000) + bytes([7]), n=1)
print("  V set: st=%d det=%d slot=%d" % (st, det, pl[0] if pl else -1))
print("  comp0=%08x func0=%08x" % (rd32(s, 0xE0001020), rd32(s, 0xE0001024)))

print("-- run routine that writes 0x20003000 --")
cmd(s, ord("M"), u32(RADDR) + u32(len(ROUTINE)) + ROUTINE)
cmd(s, ord("N"), bytes([1]) + u32(0x20003000))
cmd(s, ord("N"), bytes([23]) + u32(1))
cmd(s, ord("N"), bytes([15]) + u32(RADDR)); time.sleep(0.02)
st, det, _ = cmd(s, ord("C")); time.sleep(0.2)
st, det, pl = cmd(s, ord("T"), n=4)
dh = struct.unpack("<I", pl)[0] if st == 0 else 0
pc = read_pc(s)
print("  dhcsr=%08x halted=%d pc=%08x" % (dh, bool(dh & (1 << 17)), pc))

print("-- V wp clear --")
st, det, pl = cmd(s, ord("V"), bytes([0]) + u32(0x20003000) + bytes([7]), n=1)
print("  V clear: st=%d det=%d slot=%d" % (st, det, pl[0] if pl else -1))
print("  func0=%08x" % rd32(s, 0xE0001024))
s.close()
