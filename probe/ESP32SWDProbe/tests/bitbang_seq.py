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
        while len(r) < 1 and time.time() - t0 < 3:
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

def write_reg(s, cmd_byte, val, label, verbose=True):
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
    if verbose:
        print("%-34s ack=%d window=%s" % (label, ack, bin(rd)[2:].zfill(5)))
    return ack

def read_reg(s, cmd_byte, label, verbose=True):
    for i in range(8):
        write_bit(s, (cmd_byte >> i) & 1)
    cmd(s, b'o')
    cmd(s, b'g')
    rd = read_bits(s, 38)
    cmd(s, b'O')
    ack = (rd >> 1) & 7
    data = (rd >> 4) & 0xffffffff
    if verbose:
        print("%-34s ack=%d data=0x%08x" % (label, ack, data))
    return ack, data

H_DPIDR  = 0xA5
H_ABORT  = 0x81
H_CTRL_W = 0xA9
H_CTRL_R = 0x8D
H_SELECT = 0xB1

s = connect()
swd_switch(s)
read_reg(s, H_DPIDR, "1 DPIDR read")
read_reg(s, H_DPIDR, "2 DPIDR read")
write_reg(s, H_ABORT, 0x1E, "3 ABORT write")
read_reg(s, H_CTRL_R, "4 CTRL/STAT read")
write_reg(s, H_CTRL_W, 0x50000000, "5 CTRL/STAT write")
read_reg(s, H_CTRL_R, "6 CTRL/STAT read (poll)")
write_reg(s, H_SELECT, 0x0, "7 SELECT write")
write_reg(s, H_CTRL_W, 0x50000000, "8 CTRL/STAT write again")
read_reg(s, H_CTRL_R, "9 CTRL/STAT read")
write_reg(s, H_SELECT, 0x0, "10 SELECT write again")

cmd(s, b'Q')
s.close()
