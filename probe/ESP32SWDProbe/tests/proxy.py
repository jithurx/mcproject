import socket, threading, time, sys

LISTEN_PORT = 5556
TARGET = ("192.168.0.192", 5555)
LOGFILE = r"C:\Users\cecilia\AppData\Local\Temp\opencode\proxy.log"

LOG = open(LOGFILE, "wb")
LOG_LOCK = threading.Lock()

def log_bytes(direction, data):
    with LOG_LOCK:
        for b in data:
            t = time.time()
            LOG.write(("%d C>%s %s %02x\n" % (t, "UP " if direction == "up" else "DOWN", chr(b) if 32 <= b < 127 else ".", b)).encode())
    LOG.flush()

def pipe(src, dst, direction, stop):
    try:
        while not stop[0]:
            try:
                data = src.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            log_bytes(direction, data)
            dst.sendall(data)
    finally:
        stop[0] = True

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", LISTEN_PORT))
    srv.listen(2)
    print("proxy on 127.0.0.1:%d -> %s:%d" % (LISTEN_PORT, TARGET[0], TARGET[1]), flush=True)
    while True:
        client, _ = srv.accept()
        print("client connected", flush=True)
        up = socket.create_connection(TARGET, timeout=6)
        up.settimeout(0.2)
        client.settimeout(0.2)
        stop = [False]
        t1 = threading.Thread(target=pipe, args=(client, up, "up", stop))
        t2 = threading.Thread(target=pipe, args=(up, client, "down", stop))
        t1.start(); t2.start()
        while t1.is_alive() and t2.is_alive():
            time.sleep(0.1)
        stop[0] = True
        try: up.close()
        except Exception: pass
        try: client.close()
        except Exception: pass
        print("connection closed", flush=True)

main()
