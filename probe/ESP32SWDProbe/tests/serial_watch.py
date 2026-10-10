import serial, time, sys
ser = serial.Serial('COM10', 115200, timeout=0.2)
t0 = time.time()
buf = b''
while time.time() - t0 < float(sys.argv[1]):
    d = ser.read(ser.in_waiting or 1)
    if d:
        buf += d
ser.close()
print(buf.decode('utf-8', 'replace')[-1500:])
