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
            return -1
        if b:
            v |= (1 << i)
    return v

def parity32(v):
    return bin(v).count('1') & 1

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

def write_reg(s, cmd_byte, val, label):
    for i in range(8):
        write_bit(s, (cmd_byte >> i) & 1)
    cmd(s, b'o')
    cmd(s, b'g')
    rd = read_bits(s, 5)
    cmd(s, b'O')
    ack = (rd >> 1) & 7
    for i in range(32):
        write_bit(s, (val >> i) & 1)
    write_bit(s, parity32(val))
    cmd(s, b'O')
    cmd(s, b'g')
    print("%-28s ack=%d window=%s" % (label, ack, bin(rd)[2:].zfill(5)))

def read_reg(s, cmd_byte, label):
    for i in range(8):
        write_bit(s, (cmd_byte >> i) & 1)
    cmd(s, b'o')
    cmd(s, b'g')
    rd = read_bits(s, 38)
    cmd(s, b'O')
    ack = (rd >> 1) & 7
    data = (rd >> 4) & 0xffffffff
    print("%-28s ack=%d data=0x%08x" % (label, ack, data))
    return data

s = connect()
swd_switch(s)

# header bytes
H_DPIDR  = 0xA5  # DP read reg 0x00 (RNW, parity1)
H_ABORT  = 0x81  # DP write reg 0x00
H_CTRL   = 0xA9  # DP write reg 0x04 (parity1)
H_CTRL_R = 0xA1 ^ 0x08  # DP read reg 0x04: START|RNW|A(0x08)|?parity
# recompute CTRL read parity: bits1-4 = 0,1,0,1 -> parity 0 -> 0x01|0x04|0x08|0x80 = 0x8D
H_CTRL_R = 0x8D
H_SELECT = 0xB1  # DP write reg 0x08

read_reg(s, H_DPIDR, "DPIDR read")
write_reg(s, H_ABORT, 0x1E, "ABORT write 0x1E")
write_reg(s, H_SELECT, 0, "SELECT write 0x0")
write_reg(s, H_CTRL, 0x00000000, "CTRL/STAT write 0x0")
write_reg(s, H_CTRL, 0x1E, "CTRL/STAT write 0x1E")
write_reg(s, H_CTRL, 0x50000000, "CTRL/STAT write 0x50000000")
write_reg(s, H_CTRL, 0x50000000, "CTRL/STAT write 0x50000000 (2nd)")
read_reg(s, H_CTRL_R, "CTRL/STAT read")

cmd(s, b'Q')
s.close()
