import serial, time

# For each baud: set bridge, ask user to reset Blue Pill, capture, check text
BAUDS = [4800, 9600, 14400, 19200, 38400, 57600, 115200]

s = serial.Serial("COM10", 115200, timeout=1)
time.sleep(1.0)

for b in BAUDS:
    s.reset_input_buffer()
    s.write(b"B%d\n" % b)
    time.sleep(1.0)
    # drain echo
    s.reset_input_buffer()
    print(">>> Set to %d. Press Blue Pill RESET now..." % b)
    time.sleep(4.0)
    data = bytearray()
    end = time.time() + 4.0
    while time.time() < end:
        d = s.read(4096)
        if d:
            data += d
    text = data.decode("latin-1", errors="replace")
    printable = sum(1 for ch in data if 32 <= ch < 127)
    score = 100.0 * printable / max(1, len(data))
    has = ("Blue Pill" in text) or ("MPU-9250" in text) or ("imu:" in text) or ("nRF24" in text)
    print("   %-7d %5d bytes  printable %3.0f%%  %s  %r" % (b, len(data), score, "MATCH!" if has else "", text[:200]))
    if has:
        break
s.close()
