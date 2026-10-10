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
        print("%-28s ack=%d" % (label, ack))
    return ack

def read_reg_raw(s, cmd_byte, bits=38):
    for i in range(8):
        write_bit(s, (cmd_byte >> i) & 1)
    cmd(s, b'o')
    cmd(s, b'g')
    rd = read_bits(s, bits)
    cmd(s, b'O')
    ack = (rd >> 1) & 7
    data = (rd >> 4) & 0xffffffff
    return ack, data

def read_reg(s, cmd_byte, label, max_retries=200, pause=0.001, verbose=True):
    for attempt in range(max_retries + 1):
        ack, data = read_reg_raw(s, cmd_byte)
        if ack == 1:
            if verbose:
                print("%-28s ack=%d data=0x%08x (tries=%d)" % (label, ack, data, attempt))
            return ack, data, attempt
        if attempt < max_retries:
            time.sleep(pause)
    if verbose:
        print("%-28s FAIL ack=%d data=0x%08x (tries=%d)" % (label, ack, data, max_retries))
    return ack, data, max_retries

H_DPIDR  = 0xA5
H_ABORT  = 0x81
H_CTRL_W = 0xA9
H_CTRL_R = 0x8D
H_SELECT = 0xB1
H_AP_CSW_W = 0xA3
H_AP_TAR_W = 0x8B
H_AP_DRW_R = 0x9F

s = connect()
swd_switch(s)
read_reg(s, H_DPIDR, "DPIDR read", max_retries=10)
write_reg(s, H_ABORT, 0x1E, "ABORT clear sticky")
write_reg(s, H_SELECT, 0x0, "DP SELECT=0")
write_reg(s, H_CTRL_W, 0x50000000, "CTRL/STAT PWRUPREQ")
ack, cs, _ = read_reg(s, H_CTRL_R, "CTRL/STAT read", max_retries=10)
for i in range(20):
    if ((cs >> 28) & 0xF) == 0xF:
        break
    ack, cs, _ = read_reg(s, H_CTRL_R, "CTRL/STAT poll")
print("power bits: 0x%X" % ((cs >> 28) & 0xF))

write_reg(s, H_AP_CSW_W, 0x23000052, "AP CSW write")
write_reg(s, H_AP_TAR_W, 0x08000000, "AP TAR write 0x08000000")
# first access to AP after power-up: expect WAIT; keep retrying the same read
ack, d, tries = read_reg(s, H_AP_DRW_R, "AP DRW warm-up", max_retries=300, pause=0.005)
ack, d, tries = read_reg(s, H_AP_DRW_R, "AP DRW word[0]", max_retries=20)
print("  -> flash word[0] = 0x%08x" % d)
ack, d, tries = read_reg(s, H_AP_DRW_R, "AP DRW word[1]", max_retries=20)
print("  -> flash word[1] = 0x%08x" % d)
ack, d, tries = read_reg(s, H_AP_DRW_R, "AP DRW word[2]", max_retries=20)
print("  -> flash word[2] = 0x%08x" % d)

cmd(s, b'Q')
s.close()
