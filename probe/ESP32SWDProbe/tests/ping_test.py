import socket, time

s = socket.create_connection(("192.168.0.192", 5555), timeout=5)
s.settimeout(5)
slow = 0
t0 = time.time()
for i in range(60):
    a = time.time()
    s.sendall(b"A")
    data = b""
    while len(data) < 2:
        chunk = s.recv(2 - len(data))
        if not chunk:
            print("closed at iter", i)
            s.close()
            raise SystemExit(1)
        data += chunk
    dt = time.time() - a
    if dt > 0.5:
        slow += 1
        print("slow iter %02d  %.2fs" % (i, dt))
print("total %.2fs, slow(%d/60)" % (time.time() - t0, slow))
s.close()
