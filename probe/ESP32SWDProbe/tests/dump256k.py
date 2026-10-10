import socket, struct, time, sys

def recv_exact(s, n, timeout=15):
    s.settimeout(timeout)
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    return data

def fast_read(host, port, addr, nwords, timeout=25):
    s = socket.create_connection((host, port), timeout=6)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        s.sendall(b"X" + struct.pack("<IH", addr, nwords))
        hdr = recv_exact(s, 2, timeout)
        if len(hdr) < 2:
            return (-1, 0), b""
        status, detail = hdr[0], hdr[1]
        if status != 0:
            return (status, detail), b""
        data = recv_exact(s, nwords * 4, timeout)
        return (0, 0), data
    finally:
        try:
            s.sendall(b"Q")
        except Exception:
            pass
        s.close()

HOST, PORT = "192.168.0.192", 5555
START = 0x08000000
LEN = 0x40000      # 256KB
CHUNK_WORDS = 1024 # 4KB per X call

deadline = time.time() + 45
while time.time() < deadline:
    try:
        s = socket.create_connection((HOST, PORT), timeout=3)
        s.close()
        break
    except Exception:
        time.sleep(1)

out = bytearray()
failures = 0
t0 = time.time()
for off in range(0, LEN, CHUNK_WORDS * 4):
    addr = START + off
    st, data = fast_read(HOST, PORT, addr, CHUNK_WORDS)
    if st[0] != 0:
        print("FAIL at 0x%06x status=%d detail=%d" % (addr, st[0], st[1]))
        failures += 1
        out += b"\x00" * (CHUNK_WORDS * 4)
    else:
        out += data
    if (off // (CHUNK_WORDS * 4)) % 8 == 0:
        print("0x%06x ..." % addr, flush=True)

with open(r"C:\Users\cecilia\AppData\Local\Temp\opencode\stm32_dump_256k.bin", "wb") as f:
    f.write(bytes(out))
print("done: %d bytes, %d failures, %.1fs" % (len(out), failures, time.time() - t0))
