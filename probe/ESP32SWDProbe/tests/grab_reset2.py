import serial, time

s = serial.Serial("COM10", 115200, timeout=1)
time.sleep(1.0)
s.reset_input_buffer()
print(">>> Press Blue Pill RESET now. Capturing 15s...")
data = bytearray()
end = time.time() + 15.0
while time.time() < end:
    d = s.read(4096)
    if d:
        data += d
print("captured %d bytes" % len(data))
with open(r"C:\Users\cecilia\AppData\Local\Temp\opencode\reset2.bin", "wb") as f:
    f.write(data)
print(repr(data[:1200]))
s.close()
