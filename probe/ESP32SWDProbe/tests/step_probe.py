import socket, time, struct

HOST = "192.168.0.192"
PORT = 5555

def one(cmdbytes, n=0, label=""):
    s = socket.create_connection((HOST, PORT), timeout=5)
    s.settimeout(3)
    try:
        s.sendall(cmdbytes)
        data = b""
        while len(data) < 2 + n:
            chunk = s.recv(2 + n - len(data))
            if not chunk:
                print("CLOSE", label, "got", len(data), "bytes")
                s.close()
                return None
            data += chunk
        print("OK", label, "reply", data[:2].hex(), "plen", len(data) - 2)
        s.close()
        return data
    except Exception as e:
        print("FAIL", label, type(e).__name__, e, "got", len(data) if 'data' in dir() else 0)
        try:
            s.close()
        except Exception:
            pass
        return None

time.sleep(1)
one(b"A", 0, "A alive")
one(b"\x49", 12, "I info")
one(b"U", 0, "U reset")
time.sleep(0.7)
one(b"T", 4, "T status")
one(b"H", 0, "H halt")
time.sleep(0.1)
one(b"T", 4, "T halted")
one(b"G", 17*4, "G regs")
one(b"N" + bytes([0]) + struct.pack("<I", 0xDEADBEEF), 0, "N r0")
one(b"G", 17*4, "G r0 check")
