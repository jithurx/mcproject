import serial, socket, struct, time

ser = serial.Serial()
ser.port = 'COM10'
ser.baudrate = 115200
ser.timeout = 0.3
ser.dtr = False
ser.rts = False
ser.open()

# wait for probe to boot + join wifi + open listener
s = None
t0 = time.time()
while time.time() - t0 < 45:
    try:
        s = socket.create_connection(('192.168.0.192', 5555), timeout=3)
        s.settimeout(20)
        break
    except Exception:
        time.sleep(2)
if not s:
    print("probe never came up")
    ser.close()
    raise SystemExit

print("connected after %.0fs" % (time.time() - t0))

def recv_exact(s, n):
    d = b''
    while len(d) < n:
        c = s.recv(n - len(d))
        if not c:
            break
        d += c
    return d

s.sendall(b'X' + struct.pack('<IH', 0x08000000, 1))
h = recv_exact(s, 2)
print("X status:", h.hex())
s.sendall(b'Q')
s.close()

time.sleep(2)
buf = b''
while ser.in_waiting:
    buf += ser.read(ser.in_waiting)
print("--- serial tail ---")
print(buf.decode('utf-8', 'replace')[-2000:])
ser.close()
