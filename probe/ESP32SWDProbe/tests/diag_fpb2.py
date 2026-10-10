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

s = conn()
cmd(s, ord("A"))
cmd(s, ord("H")); time.sleep(0.05)

print("-- FPB_CTRL write 0x261 x5 --")
for i in range(5):
    wr(s, 0xE0002000, 0x261)
    print("  rd=%08x" % rd(s, 0xE0002000))

print("-- FPB_COMP0 write 0x20002005 x5 --")
for i in range(5):
    wr(s, 0xE0002008, 0x20002005)
    print("  rd=%08x" % rd(s, 0xE0002008))

print("-- write COMPs then ENABLE last --")
wr(s, 0xE0002008, 0x20002005)
wr(s, 0xE0002000, 0x261)
print("  ctrl=%08x comp0=%08x" % (rd(s, 0xE0002000), rd(s, 0xE0002008)))

print("-- write ENABLE via raw byte M (len 1) at ctrl+3 --")
wr(s, 0xE0002000, 0x260)  # reset
wr(s, 0xE0002003, 1)      # byte write, bit0 of ctrl is byte0 not byte3 - this is a test
print("  ctrl=%08x" % rd(s, 0xE0002000))

print("-- write all bytes individually --")
wr(s, 0xE0002000, 0)
for i in range(4):
    cmd(s, ord("M"), u32(0xE0002000 + i) + u32(1) + bytes([0xFF]))
print("  ctrl=%08x" % rd(s, 0xE0002000))
s.close()
