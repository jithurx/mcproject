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

BP = 0x08001520
replace = 1 if (BP & 2) == 0 else 2
comp = (BP & 0xFFFFFFFC) | (replace << 30) | 1

s = conn()
cmd(s, ord("A")); cmd(s, ord("H")); time.sleep(0.05)

print("BP=%08x replace=%d comp=%08x" % (BP, replace, comp))
wr(s, 0xE0002008, comp)
print("  comp0 rd=%08x" % rd(s, 0xE0002008))
wr(s, 0xE0002000, 0x80000261)   # KEY(bit31) | ENABLE
print("  ctrl  rd=%08x (want 0x260|1)" % rd(s, 0xE0002000))
wr(s, 0xE000200C, comp)

cmd(s, ord("N"), bytes([15]) + u32(BP))
time.sleep(0.02)
st, det, _ = cmd(s, ord("C"))
time.sleep(0.15)
st, det, pl = cmd(s, ord("T"), n=4)
dhcsr = struct.unpack("<I", pl)[0] if st == 0 else 0
print("  after resume: dhcsr=%08x halted=%d" % (dhcsr, bool(dhcsr & (1 << 17))))
st, det, pl = cmd(s, ord("G"), n=68)
if st == 0:
    regs = struct.unpack("<17I", pl)
    print("  pc=%08x (want %08x)" % (regs[15], BP))

cmd(s, ord("F"), bytes([2]))
s.close()
