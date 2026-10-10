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
        print("%-30s ack=%d window=%s" % (label, ack, bin(rd)[2:].zfill(5)))
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
        print("%-30s ack=%d data=0x%08x" % (label, ack, data))
    return ack, data

H_DPIDR   = 0xA5
H_ABORT   = 0x81
H_CTRL_W  = 0xA9
H_CTRL_R  = 0x8D
H_SELECT  = 0xB1       # DP write reg 0x08
H_AP_CSW  = 0xA3       # AP write reg 0x00 (bank0)
H_AP_TAR  = 0x8B       # AP write reg 0x04 (bank0)
H_AP_DRW  = 0x9F       # AP read reg 0x0C (bank0)
H_SEL_F   = 0xB1       # SELECT write with value 0xF0 for bank F

s = connect()
swd_switch(s)
read_reg(s, H_DPIDR, "DPIDR read")
write_reg(s, H_SELECT, 0x0, "DP SELECT=0")
write_reg(s, H_ABORT, 0x1E, "ABORT clear")
write_reg(s, H_CTRL_W, 0x50000000, "CTRL/STAT PWRUP")
ack, cs = read_reg(s, H_CTRL_R, "CTRL/STAT read")
print("  power bits: 0x%X" % ((cs >> 28) & 0xF))

# OpenOCD-style warm-up: read AP IDR first (SELECT bank F, then AP read reg C)
write_reg(s, H_SELECT, 0xF0, "SELECT APBANKSEL=F")
ack, idr = read_reg(s, H_AP_DRW, "AP IDR read (bank F)")
print("  AP IDR = 0x%08x (want 0x24770011)" % idr)
write_reg(s, H_SELECT, 0x0, "SELECT bank 0")
write_reg(s, H_AP_CSW, 0x23000052, "AP CSW write")
write_reg(s, H_AP_TAR, 0x08000000, "AP TAR write")
ack, d = read_reg(s, H_AP_DRW, "AP DRW warm-up read")
ack, d = read_reg(s, H_AP_DRW, "AP DRW word[0]")
print("  flash word[0] = 0x%08x" % d)

cmd(s, b'Q')
s.close()
