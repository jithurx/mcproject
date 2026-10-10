import socket, time, sys

HOST, PORT = "192.168.0.192", 5555

def connect():
    s = socket.create_connection((HOST, PORT), timeout=6)
    s.settimeout(6)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return s

def cmd(s, c, resp=False):
    s.sendall(c)
    if resp:
        r = b''
        t0 = time.time()
        while len(r) < 1 and time.time() - t0 < 4:
            try:
                chunk = s.recv(1)
            except socket.timeout:
                continue
            if chunk:
                r += chunk
        return r
    return b''

def write_bit(s, bit):
    cmd(s, b'e' if bit else b'd')
    cmd(s, b'g' if bit else b'f')

def read_bit(s):
    cmd(s, b'e')
    r = cmd(s, b'c', resp=True)
    cmd(s, b'g')
    if r == b'1':
        return 1
    if r == b'0':
        return 0
    return None

def read_bits(s, n):
    v = 0
    for i in range(n):
        b = read_bit(s)
        if b is None:
            print("READ FAIL at bit", i)
            return None
        if b:
            v |= (1 << i)
    return v

s = connect()
# sync check: get a few 'c' responses flowing
cmd(s, b'o')
r = cmd(s, b'c', resp=True)
print("pre-sync read:", r)

cmd(s, b'O')
for _ in range(7):
    for _ in range(8):
        write_bit(s, 1)
for byte in (0x9e, 0xe7):
    for i in range(8):
        write_bit(s, (byte >> i) & 1)
for _ in range(7):
    for _ in range(8):
        write_bit(s, 1)
for _ in range(8):
    write_bit(s, 0)
cmd(s, b'O')
cmd(s, b'g')

hdr = 0xA5
for i in range(8):
    write_bit(s, (hdr >> i) & 1)

cmd(s, b'o')
cmd(s, b'g')
rd = read_bits(s, 38)
cmd(s, b'O')
cmd(s, b'Q')
s.close()

if rd is None:
    print("DPIDR read INCOMPLETE")
    sys.exit(1)
ack = (rd >> 1) & 7
data = (rd >> 4) & 0xffffffff
par = (rd >> 36) & 1
print("ack=%d (1=OK 2=WAIT 4=FAULT 7=noresp)" % ack)
print("data=0x%08x parity=%d" % (data, par))
