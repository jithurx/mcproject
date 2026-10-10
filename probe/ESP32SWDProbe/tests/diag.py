import socket, time, subprocess, serial, threading

def reset_esp():
    subprocess.run([r"C:\Users\cecilia\AppData\Local\Arduino15\packages\esp32\tools\esptool_py\5.3.1\esptool.exe",
                    "-p", "COM10", "read_mac"],
                   capture_output=True, timeout=30)

try:
    ser = serial.Serial("COM10", 115200, timeout=0.5, write_timeout=1)
except Exception as e:
    ser = None
    print("serial open failed:", e)

log = []
stop = False
def reader():
    while not stop:
        try:
            data = ser.read(256) if ser else b""
            if data:
                log.append(data.decode("utf-8", "replace"))
        except Exception:
            break
t = threading.Thread(target=reader, daemon=True)
t.start()

reset_esp()
print("reset done, waiting for wifi join...")
time.sleep(12)
log.append("\n--- connecting ---\n")
s = socket.socket()
s.settimeout(6)
try:
    t0 = time.time()
    s.connect(("192.168.0.192", 5555))
    print("TCP CONNECT OK in %.0fms" % ((time.time() - t0) * 1000))
    s.settimeout(6)
    t0 = time.time()
    s.sendall(b"c")
    resp = s.recv(4)
    print("RESP %r in %.0fms" % (resp, (time.time() - t0) * 1000))
    s.sendall(b"Q")
except Exception as e:
    print("TCP FAIL:", e)
finally:
    s.close()
stop = True
time.sleep(1)
if ser:
    ser.close()
print("---serial---")
print("".join(log)[-800:])
