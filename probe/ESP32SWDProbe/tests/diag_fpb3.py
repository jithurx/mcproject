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

def wr(s, addr, val):
    return cmd(s, ord("M"), u32(addr) + u32(4) + u32(val))[:2]

def wrb(s, addr, val):  # single byte write
    return cmd(s, ord("M"), u32(addr) + u32(1) + bytes([val]))[:2]

def rd(s, addr):
    st, det, pl = cmd(s, ord("X"), u32(addr) + struct.pack("<H", 1), n=4)
    return struct.unpack("<I", pl)[0] if st == 0 else None

def wr32b(s, addr, val):  # write 32-bit via 4 byte writes
    for i in range(4):
        r = wrb(s, addr + i, (val >> (8 * i)) & 0xFF)
        if r != (0, 0): return r
    return (0, 0)

s = conn()
cmd(s, ord("A")); cmd(s, ord("H")); time.sleep(0.05)

print("-- word-write corruption pattern on FPB_COMP0 --")
for v in [0x00002005, 0x20002005, 0xA5A55A5A, 0xFFFFFFFF, 0x00002002]:
    wr(s, 0xE0002008, v)
    print("  wr=%08x -> rd=%08x" % (v, rd(s, 0xE0002008)))
wr(s, 0xE0002008, 0)

print("-- byte-write full word to FPB_COMP0 --")
wr32b(s, 0xE0002008, 0xA5A55A5A)
print("  wr32b=0xA5A55A5A -> rd=%08x" % rd(s, 0xE0002008))
wr32b(s, 0xE0002008, 0x20002005)
print("  wr32b=0x20002005 -> rd=%08x" % rd(s, 0xE0002008))

print("-- word write to FPB_COMP1 --")
wr(s, 0xE000200C, 0xA5A55A5A)
print("  wr=0xA5A55A5A -> rd=%08x" % rd(s, 0xE000200C))

print("-- ENABLE: word vs byte --")
wr(s, 0xE0002000, 0x260)          # word reset
print("  after word 0x260: ctrl=%08x" % rd(s, 0xE0002000))
wrb(s, 0xE0002000, 0x61)          # byte set ENABLE
print("  after byte 0x61:  ctrl=%08x" % rd(s, 0xE0002000))

print("-- full FPB breakpoint via byte writes, then resume --")
cmd(s, ord("M"), u32(RADDR) + u32(len(ROUTINE)) + ROUTINE)
cmd(s, ord("N"), bytes([1]) + u32(0x20003000))   # r1 = scratch
cmd(s, ord("N"), bytes([23]) + u32(1))            # PRIMASK=1
cmd(s, ord("N"), bytes([15]) + u32(RADDR))        # pc = routine
time.sleep(0.02)
wr32b(s, 0xE0002008, 0x20002005)                  # FPB_COMP0 bp @ RADDR+4
wrb(s, 0xE0002000, 0x61)                          # FPB_CTRL ENABLE
print("  ctrl=%08x comp0=%08x" % (rd(s, 0xE0002000), rd(s, 0xE0002008)))
st, det = cmd(s, ord("C"))
time.sleep(0.15)
st, det, pl = cmd(s, ord("T"), n=4)
dhcsr = struct.unpack("<I", pl)[0] if st == 0 else 0
print("  after resume: dhcsr=%08x halted=%d" % (dhcsr, bool(dhcsr & (1 << 17))))
st, det, pl = cmd(s, ord("G"), n=68)
if st == 0:
    pc = struct.unpack("<17I", pl)[15]
    print("  pc=%08x (expect %08x)" % (pc, RADDR + 4))

print("-- cleanup --")
cmd(s, ord("F"), bytes([2]))
s.close()
