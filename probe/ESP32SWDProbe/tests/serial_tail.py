import serial, time

ser = serial.Serial("COM10", 115200, timeout=1)
ser.dtr = False
ser.rts = False
time.sleep(0.2)
t0 = time.time()
buf = b""
while time.time() - t0 < 6:
    b = ser.read(256)
    if b:
        buf += b
        t0 = time.time()
print(repr(buf.decode("utf-8", "replace")))
ser.close()
