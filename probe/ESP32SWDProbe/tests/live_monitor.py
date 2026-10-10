import socket, serial, time, threading

acc = []
stop = [False]
ser = serial.Serial('COM10', 115200, timeout=0.2)

def reader():
    while not stop[0]:
        d = ser.read(ser.in_waiting or 1)
        if d:
            acc.append(d.decode('utf-8', 'replace'))

t = threading.Thread(target=reader, daemon=True)
t.start()

t0 = time.time()
last_tcp = None
while time.time() - t0 < 90:
    try:
        s = socket.create_connection(('192.168.0.192', 5555), timeout=4)
        s.sendall(b'Z')
        s.close()
        now = "TCP OK"
    except Exception as e:
        now = "TCP FAIL"
    if now != last_tcp:
        print("[%6.1fs] %s" % (time.time() - t0, now))
        last_tcp = now
    time.sleep(2)

stop[0] = True
time.sleep(1)
ser.close()
print("---serial log---")
print("".join(acc))
