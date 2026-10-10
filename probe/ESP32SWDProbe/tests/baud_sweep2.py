import serial, time

BAUDS = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800]

port = "COM10"
s = serial.Serial(port, 115200, timeout=1)
time.sleep(1.0)

for b in BAUDS:
    s.reset_input_buffer()
    s.write(b"B%d\n" % b)
    time.sleep(1.5)
    data = bytearray()
    end = time.time() + 4.0
    while time.time() < end:
        d = s.read(1024)
        if d:
            data += d
    # find runs of printable chars
    text = data.decode("latin-1", errors="replace")
    best = ""
    cur = ""
    for ch in text:
        if 32 <= ord(ch) < 127 or ch in "\r\n":
            cur += ch
            if len(cur) > len(best):
                best = cur
        else:
            cur = ""
    print("==== BAUD %d: %d bytes, longest printable run = %d ====" % (b, len(data), len(best)))
    if best:
        print("  %r" % best[:300])
s.close()
