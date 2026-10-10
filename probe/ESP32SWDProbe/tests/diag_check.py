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
def t(s):
    st, det, pl = cmd(s, ord("T"), n=4)
    return struct.unpack("<I", pl)[0] if st == 0 else 0

s = conn()
print("A:", cmd(s, ord("A"))[:2])
cmd(s, ord("H")); time.sleep(0.05)
print("T: %08x" % t(s))
# F command: check firmware arg format first
import re
ino = open(r"C:\Users\cecilia\Downloads\FC & telemetry\esp32_swd_probe\esp32_swd_probe.ino").read()
m = re.search(r"case 'F':(.*?)(?=case |\n    })", ino, re.S)
print(m.group(0) if m else "no F case")
s.close()
