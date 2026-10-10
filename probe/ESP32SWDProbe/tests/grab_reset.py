import serial, time

s = serial.Serial("COM10", 115200, timeout=1)
time.sleep(1.0)
s.reset_input_buffer()
print(">>> Press Blue Pill RESET now (BOOT0=0). Capturing 12s...")
data = bytearray()
end = time.time() + 12.0
while time.time() < end:
    d = s.read(4096)
    if d:
        data += d
print("captured %d bytes" % len(data))
with open(r"C:\Users\cecilia\AppData\Local\Temp\opencode\reset_115200.bin", "wb") as f:
    f.write(data)
text = data.decode("latin-1", errors="replace")
print("printable=%d" % sum(1 for ch in data if 32 <= ch < 127))
print(repr(text[:800]))
s.close()
