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
    return struct.unpack("<I", pl)[0] if st == 0 else None

s = conn()
print("A:", cmd(s, ord("A"))[:2])
print("H:", cmd(s, ord("H"))[:2]); time.sleep(0.05)

print("-- D AP IDR (fresh) --")
st, det, pl = cmd(s, ord("D"), bytes([1, 0xF, 0xC]), n=4)
print("  st=%d det=%d raw=%s val=%08x" % (st, det, pl.hex(), struct.unpack("<I", pl)[0] if st == 0 else 0))

print("-- warm up AP with a mem read, then D AP IDR --")
print("  mem_rd32(0x20000000) = %08x" % (rd32(s, 0x20000000) or 0))
st, det, pl = cmd(s, ord("D"), bytes([1, 0xF, 0xC]), n=4)
print("  st=%d det=%d raw=%s val=%08x" % (st, det, pl.hex(), struct.unpack("<I", pl)[0] if st == 0 else 0))

print("-- D AP IDR twice in a row --")
st, det, pl = cmd(s, ord("D"), bytes([1, 0xF, 0xC]), n=4)
print("  1st: st=%d det=%d val=%08x" % (st, det, struct.unpack("<I", pl)[0] if st == 0 else 0))
st, det, pl = cmd(s, ord("D"), bytes([1, 0xF, 0xC]), n=4)
print("  2nd: st=%d det=%d val=%08x" % (st, det, struct.unpack("<I", pl)[0] if st == 0 else 0))

print("-- D DPIDR --")
st, det, pl = cmd(s, ord("D"), bytes([0, 0, 0]), n=4)
print("  st=%d det=%d raw=%s val=%08x" % (st, det, pl.hex(), struct.unpack("<I", pl)[0] if st == 0 else 0))

print("-- D DP CTRL/STAT via bank 0 reg 0x4 --")
st, det, pl = cmd(s, ord("D"), bytes([0, 0, 4]), n=4)
print("  st=%d det=%d val=%08x" % (st, det, struct.unpack("<I", pl)[0] if st == 0 else 0))
s.close()
