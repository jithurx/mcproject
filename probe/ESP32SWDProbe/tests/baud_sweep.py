import serial, time, sys

BAUDS = [1200, 2400, 4800, 9600, 14400, 19200, 38400, 57600, 76800, 115200, 230400, 460800]

port = "COM10"
s = serial.Serial(port, 115200, timeout=1)
time.sleep(1.0)
s.reset_input_buffer()

for b in BAUDS:
    s.reset_input_buffer()
    s.write(b"B%d\n" % b)
    time.sleep(0.8)
    # read what the bridge echoed + whatever the STM32 sent at that baud
    data = s.read(3000)
    # check for the banner
    text = data.decode("latin-1", errors="replace")
    has_banner = "Blue Pill" in text or "=== Blue" in text or "MPU" in text or "imu:" in text
    # count printable chars
    printable = sum(1 for ch in data if 32 <= ch < 127)
    score = printable / max(1, len(data))
    print("BAUD %-7d len=%-4d printable=%-3d score=%.2f %s" % (b, len(data), printable, score, "  <-- MATCH?" if has_banner else ""))
    if has_banner:
        print("   text: %r" % text[:300])
        break

s.close()
