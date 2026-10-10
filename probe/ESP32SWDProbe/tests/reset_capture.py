import serial, time, sys

port = "COM10"
bauds = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else [115200]

s = serial.Serial(port, 115200, timeout=1)
time.sleep(1.0)

for b in bauds:
    s.reset_input_buffer()
    s.write(b"B%d\n" % b)
    time.sleep(1.5)
    s.reset_input_buffer()
    print(">>> Waiting at baud %d. Press Blue Pill RESET now within 5s..." % b)
    data = bytearray()
    end = time.time() + 5.0
    while time.time() < end:
        d = s.read(4096)
        if d:
            data += d
    text = data.decode("latin-1", errors="replace")
    printable = sum(1 for ch in data if 32 <= ch < 127)
    print("    captured %d bytes, %d printable (%.0f%%)" % (len(data), printable, 100.0 * printable / max(1, len(data))))
    print("    %r" % text[:400])
s.close()
