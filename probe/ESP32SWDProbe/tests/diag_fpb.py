import socket, time, struct

HOST = "192.168.0.192"
PORT = 5555
ROUTINE = bytes([0x08, 0x46, 0x08, 0x60, 0x00, 0xBF, 0xFE, 0xE7])  # mov/str/nop/b.
RADDR = 0x20002000

def conn():
    s = socket.create_connection((HOST, PORT), timeout=5)
    s.settimeout(5)
    return s

def recvn(s, n, timeout=8):
    s.settimeout(timeout)
    data = b""
    while len(data) < n:
        c = s.recv(n - len(data))
        if not c:
            raise RuntimeError("closed got %d/%d" % (len(data), n))
        data += c
    return data

def cmd(s, b, p=b"", n=0):
    s.sendall(bytes([b]) + p)
    st, det = recvn(s, 2)
    pl = recvn(s, n) if n else b""
    return st, det, pl

def u32(x):
    return struct.pack("<I", x)

def rd32(s, addr):
    st, det, pl = cmd(s, ord("X"), u32(addr) + struct.pack("<H", 1), n=4)
    return struct.unpack("<I", pl)[0] if st == 0 else None

def halt(s):
    return cmd(s, ord("H"))[0] == 0

s = conn()
print("A:", cmd(s, ord("A"))[:2])
print("I:", [hex(x) for x in struct.unpack("<III", cmd(s, ord("I"), n=12)[2])])
halt(s); time.sleep(0.05)
print("halted:", cmd(s, ord("T"), n=4)[2].hex())
# load routine
print("M routine:", cmd(s, ord("M"), u32(RADDR) + u32(len(ROUTINE)) + ROUTINE)[:2])
# set r1, pc
print("set r1:", cmd(s, ord("N"), bytes([1]) + u32(0x20003000))[:2])
print("set pc:", cmd(s, ord("N"), bytes([15]) + u32(RADDR))[:2])
print("PRIMASK=1:", cmd(s, ord("N"), bytes([23]) + u32(1))[:2])

# set bp
st, det, pl = cmd(s, ord("F"), bytes([1]) + u32(RADDR + 4), n=1)
print("F bp set:", st, det, "slot", pl[0] if pl else None)

print("DEMCR   = %08x (TRCENA=1 expected)" % (rd32(s, 0xE000EDFC) or 0))
print("FPB_CTRL= %08x (ENABLE bit0=1 expected)" % (rd32(s, 0xE0002000) or 0))
print("FPB_COMP0=%08x (expected %08x)" % ((rd32(s, 0xE0002008) or 0), (RADDR + 4) | 1))
print("DWT_FUNC0=%08x" % (rd32(s, 0xE0001024) or 0))

# resume, wait, then halt
print("resume:", cmd(s, ord("C"))[:2])
time.sleep(0.2)
st, det, pl = cmd(s, ord("T"), n=4)
print("T after resume:", "st=%d" % st, pl.hex() if st == 0 else "")
halted = halt(s)
print("halt after resume:", halted)
time.sleep(0.05)
st, det, pl = cmd(s, ord("G"), n=17 * 4)
regs = struct.unpack("<17I", pl) if st == 0 else None
print("pc after halt:", hex(regs[15]) if regs else None, "xpsr=%08x" % regs[16] if regs else "")

# clear bp
print("F bp clear:", cmd(s, ord("F"), bytes([0]) + u32(RADDR + 4))[:2])
print("FPB_COMP0 after clear: %08x" % (rd32(s, 0xE0002008) or 0))
print("resume+halt:", cmd(s, ord("C"))[:2], halt(s))
time.sleep(0.05)
st, det, pl = cmd(s, ord("G"), n=17 * 4)
regs = struct.unpack("<17I", pl) if st == 0 else None
print("pc:", hex(regs[15]) if regs else None)
s.close()
