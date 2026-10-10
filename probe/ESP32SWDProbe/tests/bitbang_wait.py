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

def read_reg_retry(s, cmd_byte, label, delay=0.05, maxr=20):
    ack, data = read_reg(s, cmd_byte, label, verbose=False)
    tries = 0
    while ack != 1 and tries < maxr:
        time.sleep(delay)
        ack, data = read_reg(s, cmd_byte, label, verbose=False)
        tries += 1
    print("%-30s ack=%d data=0x%08x (retries=%d)" % (label, ack, data, tries))
    return ack, data

H_DPIDR  = 0xA5
H_ABORT  = 0x81
H_CTRL_W = 0xA9
H_CTRL_R = 0x8D
H_SELECT = 0xB1
H_AP_CSW = 0xA3
H_AP_TAR = 0x8B
H_AP_DRW = 0x9F

s = connect()
swd_switch(s)
read_reg(s, H_DPIDR, "DPIDR read")
write_reg(s, H_SELECT, 0x0, "DP SELECT=0")
write_reg(s, H_ABORT, 0x1E, "ABORT clear")
write_reg(s, H_CTRL_W, 0x50000000, "CTRL/STAT PWRUP")
read_reg(s, H_CTRL_R, "CTRL/STAT read")

# warm-up AP read
write_reg(s, H_SELECT, 0xF0, "SELECT bank F")
read_reg(s, H_AP_DRW, "AP warm-up read (discard)")
write_reg(s, H_SELECT, 0x0, "SELECT bank 0")
write_reg(s, H_AP_CSW, 0x23000052, "AP CSW write")
write_reg(s, H_AP_TAR, 0x08000000, "AP TAR write")

# DRW read with SLOW retry on WAIT
read_reg_retry(s, H_AP_DRW, "AP DRW word[0] (retry)")
read_reg_retry(s, H_AP_DRW, "AP DRW word[1] (retry)")
read_reg_retry(s, H_AP_DRW, "AP DRW word[2] (retry)")

cmd(s, b'Q')
s.close()
