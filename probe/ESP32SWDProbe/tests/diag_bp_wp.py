import socket, time, struct

HOST = "192.168.0.192"
PORT = 5555
ROUTINE = bytes([0x08, 0x46, 0x08, 0x60, 0x00, 0xBF, 0xFE, 0xE7])
ROUTINE_ADDR = 0x20002000
FLASH_LOOP = 0x08001520

def connect():
    return socket.create_connection((HOST, PORT), timeout=5)

def recvn(s, n, timeout=10):
    s.settimeout(timeout)
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise RuntimeError("connection closed, got %d/%d" % (len(data), n))
        data += chunk
    return data

def cmd(s, byte, params=b""):
    s.sendall(bytes([byte]) + params)
    st, det = recvn(s, 2)
    return st, det

def cmd_payload(s, byte, params=b"", n=0):
    s.sendall(bytes([byte]) + params)
    st, det = recvn(s, 2)
    payload = recvn(s, n) if n else b""
    return st, det, payload

def u32(x):
    return struct.pack("<I", x)

def read_pc(s):
    st, det, pl = cmd_payload(s, ord("G"), n=17 * 4)
    if st != 0:
        return None
    return struct.unpack("<17I", pl)[15]

def write_reg(s, sel, val):
    return cmd(s, ord("N"), bytes([sel]) + u32(val))[0] == 0

s = connect()
print("connected")

st, det = cmd(s, ord("U"))
print("U sysreset st=%d det=%d" % (st, det))
time.sleep(0.7)
st, det = cmd(s, ord("H"))
print("H halt st=%d det=%d" % (st, det))
time.sleep(0.05)
pc = read_pc(s)
print("pc after reset-halt = %08x" % (pc or 0))

# --- deterministic FPB test: force pc into the flash loop ---
ok = write_reg(s, 15, FLASH_LOOP)
print("N pc=FLASH_LOOP -> %s" % ok)
pc = read_pc(s)
print("pc now = %08x" % (pc or 0))
st, det, pl = cmd_payload(s, ord("F"), bytes([1]) + u32(FLASH_LOOP), n=1)
print("F bp set st=%d slot=%d" % (st, pl[0] if st == 0 else -1))
t0 = time.time()
st, det = cmd(s, ord("C"))
dt = time.time() - t0
print("C resume st=%d det=%d in %.3fs" % (st, det, dt))
time.sleep(0.2)
st, det, pl = cmd_payload(s, ord("T"), n=4)
dhcsr = struct.unpack("<I", pl)[0] if st == 0 else 0
print("T dhcsr=%08x halted=%s" % (dhcsr, bool(dhcsr & (1 << 17))))
pc = read_pc(s)
print("pc = %08x" % (pc or 0))
st, det = cmd(s, ord("F"), bytes([0]) + u32(FLASH_LOOP))
print("F bp clear st=%d" % st)

# --- V wp test: halt, set regs, set wp, resume, expect halt ---
st, det = cmd(s, ord("H"))
print("H (pre-wp) st=%d" % st)
time.sleep(0.05)
write_reg(s, 20, 1)
write_reg(s, 1, 0x20003000)
write_reg(s, 15, ROUTINE_ADDR)
st, det = cmd(s, ord("M"), u32(ROUTINE_ADDR) + u32(len(ROUTINE)) + ROUTINE)
print("M load routine st=%d" % st)
st, det, pl = cmd_payload(s, ord("V"), bytes([1]) + u32(0x20003000) + bytes([7]), n=1)
print("V wp set st=%d slot=%d" % (st, pl[0] if st == 0 else -1))
t0 = time.time()
st, det = cmd(s, ord("C"))
dt = time.time() - t0
print("C resume st=%d det=%d in %.3fs" % (st, det, dt))
time.sleep(0.2)
st, det, pl = cmd_payload(s, ord("T"), n=4)
dhcsr = struct.unpack("<I", pl)[0] if st == 0 else 0
print("T dhcsr=%08x halted=%s" % (dhcsr, bool(dhcsr & (1 << 17))))
pc = read_pc(s)
print("pc = %08x (expect %08x or %08x)" % (pc or 0, ROUTINE_ADDR + 2, ROUTINE_ADDR + 4))
st, det = cmd(s, ord("V"), bytes([0]) + u32(0x20003000) + bytes([7]))
print("V wp clear st=%d" % st)

s.close()
