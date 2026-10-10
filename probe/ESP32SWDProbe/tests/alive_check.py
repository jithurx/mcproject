import socket, time

def try_conn(tag):
    try:
        s = socket.create_connection(("192.168.0.192", 5555), timeout=4)
        s.settimeout(4)
        s.sendall(b"A")
        st, det = s.recv(2)
        print(tag, "connected, A reply:", st.hex(), det.hex())
        s.close()
        return True
    except Exception as e:
        print(tag, "FAIL:", type(e).__name__, e)
        return False

for i in range(3):
    try_conn("probe check %d" % i)
    time.sleep(1)
