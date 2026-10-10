import socket, time

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
    return None if r == b'' else (1 if r == b'1' else 0)

def read_bits(s, n):
    v = 0
    for i in range(n):
        b = read_bit(s)
        if b is None:
            return None
        if b:
            v |= (1 << i)
    return v

def swd_switch(s):
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

def parity32(v):
    return bin(v).count('1') & 1

s = connect()
swd_switch(s)

# read DPIDR (verify target alive via bitbang)
cmd(s, b'g')
hdr = 0xA5
for i in range(8):
    write_bit(s, (hdr >> i) & 1)
cmd(s, b'o')
cmd(s, b'g')
rd = read_bits(s, 38)
cmd(s, b'O')
ack = (rd >> 1) & 7 if rd is not None else -1
print("DPIDR read: ack=%d data=0x%08x" % (ack, ((rd >> 4) & 0xffffffff) if rd is not None else 0))

# write CTRL/STAT = 0x50000000 (cmd = START|A(0x08)|PARITY|PARK = 0xA9)
wcmd = 0xA9
for i in range(8):
    write_bit(s, (wcmd >> i) & 1)
cmd(s, b'o')
cmd(s, b'g')
rd = read_bits(s, 5)
print("CTRL/STAT write ACK window (5 bits): %05s -> ack=%d" % (bin(rd if rd is not None else 0)[2:], (rd >> 1) & 7 if rd is not None else -1))
cmd(s, b'O')
val = 0x50000000
for i in range(32):
    write_bit(s, (val >> i) & 1)
write_bit(s, parity32(val))
cmd(s, b'O')
cmd(s, b'g')

# write ABORT = 0x1E (cmd = START|PARITY|PARK = 0x81)
wcmd = 0x81
for i in range(8):
    write_bit(s, (wcmd >> i) & 1)
cmd(s, b'o')
cmd(s, b'g')
rd = read_bits(s, 5)
print("ABORT write ACK window (5 bits): %05s -> ack=%d" % (bin(rd if rd is not None else 0)[2:], (rd >> 1) & 7 if rd is not None else -1))
cmd(s, b'O')
val = 0x1E
for i in range(32):
    write_bit(s, (val >> i) & 1)
write_bit(s, parity32(val))
cmd(s, b'O')
cmd(s, b'g')

# write SELECT = 0x0 (cmd = START|A3|PARITY|PARK = 0xB1)
wcmd = 0xB1
for i in range(8):
    write_bit(s, (wcmd >> i) & 1)
cmd(s, b'o')
cmd(s, b'g')
rd = read_bits(s, 5)
print("SELECT write ACK window (5 bits): %05s -> ack=%d" % (bin(rd if rd is not None else 0)[2:], (rd >> 1) & 7 if rd is not None else -1))
cmd(s, b'O')
val = 0
for i in range(32):
    write_bit(s, (val >> i) & 1)
write_bit(s, parity32(val))
cmd(s, b'O')
cmd(s, b'g')

# read CTRL/STAT back to confirm
hdr = 0xA5 ^ 0x04  # same as DPIDR read (A=0 for CTRL/STAT? no: A[3:2]=01 -> 0x08)
# cmd for DP read CTRL/STAT: START|RNW|A(0x08)|PARITY|PARK. parity over bits1..4 = 1^0^1^0=0? RNW=1,A3=0,A2=1 -> parity=0
hdr2 = 0x01 | (1 << 2) | 0x08 | 0x80  # RNW set, A=0x08, parity bit? compute: bits1..4 = 0,1,0,1 -> parity 0
for i in range(8):
    write_bit(s, (hdr2 >> i) & 1)
cmd(s, b'o')
cmd(s, b'g')
rd = read_bits(s, 38)
cmd(s, b'O')
ack2 = (rd >> 1) & 7 if rd is not None else -1
data2 = ((rd >> 4) & 0xffffffff) if rd is not None else 0
print("CTRL/STAT read: ack=%d data=0x%08x" % (ack2, data2))

cmd(s, b'Q')
s.close()
