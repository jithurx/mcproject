import socket, time, struct

HOST, PORT = "192.168.0.192", 5555

def connect():
    s = socket.create_connection((HOST, PORT), timeout=6)
    s.settimeout(6)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return s

def cmd(s, c, want_resp=False):
    s.sendall(c)
    if want_resp:
        r = b''
        s.settimeout(3)
        while len(r) < 1:
            chunk = s.recv(1 - len(r))
            if not chunk:
                break
            r += chunk
        return r
    return b''

s = connect()
# SWDIO idle high (output)
print("O:", cmd(s, b'O'))
# drive SWCLK low/high several times, drive SWDIO values, read back
cmd(s, b'g')   # clk high, swdio high
r = cmd(s, b'c', want_resp=True); print("SWDIO (driven high) reads:", r)
cmd(s, b'd')   # clk low, swdio low
r = cmd(s, b'c', want_resp=True); print("SWDIO (driven low) reads:", r)
cmd(s, b'o')   # SWDIO -> input pullup
r = cmd(s, b'c', want_resp=True); print("SWDIO (input pullup) reads:", r)
cmd(s, b'g')   # clock high
# toggle SWCLK 4 times
for i in range(4):
    cmd(s, b'd'); cmd(s, b'g')
    r = cmd(s, b'c', want_resp=True); print("SWCLK toggled, SWDIO reads:", r)
cmd(s, b'Q')
s.close()
print("done")
