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

def t(s):
    st, det, pl = cmd(s, ord("T"), n=4)
    return struct.unpack("<I", pl)[0] if st == 0 else 0

# mov r0,r1; loop: str r0,[r1]; b loop   => store forever
LOOPSTR = bytes([0x08, 0x46, 0x08, 0x60, 0xFE, 0xE7])
s = conn()
print("A:", cmd(s, ord("A"))[:2])

cmd(s, ord("H")); time.sleep(0.05)
cmd(s, ord("M"), u32(0x20002000) + u32(len(LOOPSTR)) + LOOPSTR)
cmd(s, ord("N"), bytes([1]) + u32(0x20003000))
cmd(s, ord("N"), bytes([15]) + u32(0x20002000)); time.sleep(0.02)
wr32(s, 0xE0001020, 0x20003000)
wr32(s, 0xE0001024, 0x0E)

cmd(s, ord("C"))
hit = 0
for i in range(50):
    dh = t(s)
    if dh & (1 << 17):
        hit = 1
        print("  HALT observed at poll %d dhcsr=%08x" % (i, dh))
        break
    time.sleep(0.02)
if not hit:
    print("  never halted over 1s of continuous stores; dhcsr=%08x" % t(s))
cmd(s, ord("H")); time.sleep(0.05)
print("  final T: %08x" % t(s))
wr32(s, 0xE0001024, 0); wr32(s, 0xE0001020, 0)

print("== sanity: FPB flash bp still fires ==")
cmd(s, ord("F"), bytes([1, 0, 0, 0]) + u32(0x08001520))
cmd(s, ord("C")); time.sleep(0.25)
dh = t(s)
print("  after C: halted=%s dh=%08x" % (bool(dh & (1 << 17)), dh))
cmd(s, ord("H")); time.sleep(0.05)
cmd(s, ord("F"), bytes([0, 0, 0, 0]) + u32(0x08001520))
s.close()
