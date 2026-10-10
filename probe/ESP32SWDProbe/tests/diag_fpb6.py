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

def u32(x): return struct.pack("<I", x)

s = conn()
s.sendall(b"A"); print("A:", recvn(s, 2).hex())
s.sendall(b"H"); print("H:", recvn(s, 2).hex()); time.sleep(0.05)
# F set SRAM bp (error path replies only 2 bytes)
s.sendall(bytes([ord("F")]) + bytes([1]) + u32(0x20002004))
r2 = recvn(s, 2)
print("F set SRAM head:", r2.hex(), "(st=%d det=%d)" % (r2[0], r2[1]))
if r2[0] == 0:
    slot = recvn(s, 1)
    print("  slot=%d" % slot[0])
# read back COMP0
s.sendall(b"X" + u32(0xE0002008) + struct.pack("<H", 1))
r = recvn(s, 6)
print("comp0 raw:", r.hex(), "->", struct.unpack("<I", r[2:6])[0] if r[0] == 0 else "err")
# F clear SRAM
s.sendall(bytes([ord("F")]) + bytes([0]) + u32(0x20002004))
print("F clear SRAM:", recvn(s, 2).hex())
# F clear all
s.sendall(bytes([ord("F")]) + bytes([2]))
print("F clear all:", recvn(s, 2).hex())
s.close()
