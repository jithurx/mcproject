import serial, time, threading, sys

port = "COM10"
out = r"C:\Users\cecilia\AppData\Local\Temp\opencode\serial_log.txt"
with open(out, "wb") as f:
    try:
        ser = serial.Serial(port, 115200, timeout=0.1)
    except Exception as e:
        print("open failed:", e)
        sys.exit(1)
    end = time.time() + int(sys.argv[1]) if len(sys.argv) > 1 else time.time() + 120
    while time.time() < end:
        n = ser.in_waiting
        if n:
            f.write(ser.read(n))
            f.flush()
        else:
            time.sleep(0.02)
    ser.close()
print("capture done")
