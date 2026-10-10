import socket, struct, serial, time, threading

HOST, PORT = "192.168.0.192", 5555
acc = []
stop = [False]

def read_serial():
    try:
        ser = serial.Serial('COM10', 115200, timeout=0.5)
    except Exception as e:
        acc.append("serial ERR %s\n" % e)
        return
    while not stop[0]:
        data = ser.read(ser.in_waiting or 1)
        if data:
            acc.append(data.decode('utf-8', 'replace'))
    ser.close()

t = threading.Thread(target=read_serial, daemon=True)
t.start()

def ping(timeout=6):
    try:
        s = socket.create_connection((HOST, PORT), timeout=timeout)
        s.sendall(b'Z')
        r = s.recv(1)
        s.close()
        return r == b''
    except Exception as e:
        return "FAIL %s" % e

def xread(addr, nw, timeout=6):
    try:
        s = socket.create_connection((HOST, PORT), timeout=timeout)
        s.settimeout(timeout)
        s.sendall(b'X' + struct.pack('<IH', addr, nw))
        hdr = s.recv(2)
        s.close()
        return hdr.hex() if hdr else 'empty'
    except Exception as e:
        return "FAIL %s" % e

time.sleep(2)
# Phase 1: 5 Z pings, no X
for i in range(5):
    print("Z ping %d -> %s" % (i, ping()))
    time.sleep(3)
# Phase 2: one X read
print("X 0x08000000 nw=1 ->", xread(0x08000000, 1))
time.sleep(5)
print("Z after X ->", ping())
time.sleep(3)
print("Z after X2 ->", ping())

stop[0] = True
time.sleep(1)
print("---serial tail---")
print("".join(acc)[-2000:])
