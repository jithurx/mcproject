import socket, time, struct

HOST = "192.168.0.192"; PORT = 5555
ROUTINE = bytes([0x08, 0x46, 0x08, 0x60, 0x00, 0xBF, 0xFE, 0xE7])
RADDR = 0x20002000
BP = 0x08001520

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

def rd(s, addr):
    st, det, pl = cmd(s, ord("X"), u32(addr) + struct.pack("<H", 1), n=4)
    return struct.unpack("<I", pl)[0] if st == 0 else 0

def wr(s, addr, val):
    return cmd(s, ord("M"), u32(addr) + u32(4) + u32(val))[:2]

def wrb(s, addr, val):
    return cmd(s, ord("M"), u32(addr) + u32(1) + bytes([val]))[:2]

def read_pc(s):
    st, det, pl = cmd(s, ord("G"), n=68)
    return struct.unpack("<17I", pl)[15] if st == 0 else 0

s = conn()
print("A alive:", cmd(s, ord("A"))[:2])
print("H halt:", cmd(s, ord("H"))[:2]); time.sleep(0.05)

print("-- FPB via F command (flash bp @ %08x) --" % BP)
st, det, pl = cmd(s, ord("F"), bytes([1]) + u32(BP), n=1)
print("  F bp set: st=%d det=%d slot=%d" % (st, det, pl[0] if pl else -1))
print("  ctrl =%08x comp0=%08x (want 00000261 / 48001521)" % (rd(s, 0xE0002000), rd(s, 0xE0002008)))
cmd(s, ord("N"), bytes([15]) + u32(BP)); time.sleep(0.02)
print("  C resume:", cmd(s, ord("C"))[:2]); time.sleep(0.15)
st, det, pl = cmd(s, ord("T"), n=4)
dh = struct.unpack("<I", pl)[0] if st == 0 else 0
print("  T dhcsr=%08x halted=%d pc=%08x (want halted, %08x)" % (dh, bool(dh & (1 << 17)), read_pc(s) or 0, BP))
print("  F bp clear:", cmd(s, ord("F"), bytes([0]) + u32(BP))[:2])
print("  comp0 after clear=%08x" % rd(s, 0xE0002008))

print("-- FPB SRAM address should be rejected (V1) --")
st, det, pl = cmd(s, ord("F"), bytes([1]) + u32(0x20002004), n=1)
print("  F bp set SRAM: st=%d det=%d" % (st, det))
if st == 0:
    cmd(s, ord("F"), bytes([0]) + u32(0x20002004))

print("-- DWT watchpoint via V command --")
cmd(s, ord("M"), u32(RADDR) + u32(len(ROUTINE)) + ROUTINE)
cmd(s, ord("N"), bytes([1]) + u32(0x20003000))
cmd(s, ord("N"), bytes([23]) + u32(1))
cmd(s, ord("N"), bytes([15]) + u32(RADDR)); time.sleep(0.02)
st, det, pl = cmd(s, ord("V"), bytes([1]) + u32(0x20003000) + bytes([7]), n=1)
print("  V wp set: st=%d det=%d slot=%d" % (st, det, pl[0] if pl else -1))
print("  dwt_comp0=%08x dwt_func0=%08x" % (rd(s, 0xE0001020), rd(s, 0xE0001024)))
print("  C resume:", cmd(s, ord("C"))[:2]); time.sleep(0.15)
st, det, pl = cmd(s, ord("T"), n=4)
dh = struct.unpack("<I", pl)[0] if st == 0 else 0
pc = read_pc(s)
print("  T dhcsr=%08x halted=%d pc=%08x (want halted, %08x or %08x)" % (dh, bool(dh & (1 << 17)), pc or 0, RADDR + 2, RADDR + 4))
print("  V wp clear:", cmd(s, ord("V"), bytes([0]) + u32(0x20003000) + bytes([7]))[:2])

print("-- D AP reads --")
st, det, pl = cmd(s, ord("D"), bytes([1, 0xF, 0xC]), n=4)
print("  AP IDR: st=%d det=%d idr=%08x (want 24770011)" % (st, det, struct.unpack("<I", pl)[0] if st == 0 else 0))
st, det, pl = cmd(s, ord("D"), bytes([0, 0, 0]), n=4)
print("  DPIDR: st=%d det=%d dpidr=%08x (want 1ba01477)" % (st, det, struct.unpack("<I", pl)[0] if st == 0 else 0))

cmd(s, ord("F"), bytes([2])); cmd(s, ord("V"), bytes([2]))
s.close()
