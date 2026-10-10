import socket, time, subprocess, serial, threading

def attempt(label, reset=False):
    if reset:
        subprocess.run([r"C:\Users\cecilia\AppData\Local\Arduino15\packages\esp32\tools\esptool_py\5.3.1\esptool.exe",
                        "-p", "COM10", "read_mac"], capture_output=True, timeout=30)
        time.sleep(12)
    s = socket.socket()
    s.settimeout(5)
    try:
        t0 = time.time()
        s.connect(("192.168.0.192", 5555))
        print("%s: CONNECT OK %.0fms" % (label, (time.time()-t0)*1000))
        s.settimeout(5)
        s.sendall(b"c")
        print("%s: RESP %r" % (label, s.recv(4)))
        s.sendall(b"Q")
        time.sleep(0.3)
    except Exception as e:
        print("%s: FAIL %s" % (label, e))
    finally:
        s.close()
    time.sleep(1)

attempt("1st (fresh boot)", reset=True)
attempt("2nd (no reset)")
attempt("3rd (no reset)")
attempt("4th (no reset)")
attempt("5th (no reset)")
