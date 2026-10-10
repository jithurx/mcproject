import socket, struct, time, serial, threading

SER = "COM10"

def read_serial(ser, acc, stop):
    while not stop[0]:
        try:
            d = ser.read(256)
            if d:
                acc.append(d.decode("utf-8", "replace"))
        except Exception:
            break

def recv_exact(s, n):
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    return data

def fast_read(host, port, addr, nwords, timeout=15):
    s = socket.create_connection((host, port), timeout=6)
    s.settimeout(timeout)
    s.sendall(b"X" + struct.pack("<IH", addr, nwords))
    hdr = recv_exact(s, 2)
    if len(hdr) < 2:
        s.close()
        return None, b""
    status, detail = hdr[0], hdr[1]
    if status != 0:
        s.close()
        return (status, detail), b""
    data = recv_exact(s, nwords * 4)
    try:
        s.sendall(b"Q")
    except Exception:
        pass
    s.close()
    return (0, 0), data

ser = serial.Serial(SER, 115200, timeout=0.3)
acc = []
stop = [False]
t = threading.Thread(target=read_serial, args=(ser, acc, stop), daemon=True)
t.start()
time.sleep(1)

tests = [(0x1FFFF7E0, 2, "IDCODE/FLASH size regs"),
         (0x08000000, 32, "vector table"),
         (0x08000000, 256, "1KB flash"),
         (0x08004000, 32, "erased region")]
for addr, nw, label in tests:
    t0 = time.time()
    try:
        st, data = fast_read("192.168.0.192", 5555, addr, nw)
        dt = time.time() - t0
        if st[0] == 0:
            words = struct.unpack("<%dI" % nw, data)
            print("%-24s %4d words OK in %.0fms: %s" %
                  (label, nw, dt * 1000, " ".join("%08x" % w for w in words[:8])))
        else:
            print("%-24s FAIL st=%s (%.0fms)" % (label, st, dt * 1000))
    except Exception as e:
        print("%-24s EXC %s" % (label, e))

stop[0] = True
time.sleep(0.5)
ser.close()
print("---ESP32 serial---")
print("".join(acc)[-1500:])
