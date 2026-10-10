import socket, time, struct

HOST = "192.168.0.192"; PORT = 5555

def conn():
    s = socket.create_connection((HOST, PORT), timeout=5); s.settimeout(5); return s

def recvn(s, n, timeout=8):
    s.settimeout(timeout); data = b""
    while len(data) < n:
        c = s.recv(n - len(data))
        if not c: raise RuntimeError("closed")
        data += c
    return data

def cmd(s, b, p=b"", n=0):
    s.sendall(bytes([b]) + p)
    st, det = recvn(s, 2)
    return (st, det, recvn(s, n) if n else b"")

def u32(x): return struct.pack("<I", x)

def rd32(s, addr):
    st, det, pl = cmd(s, ord("X"), u32(addr) + struct.pack("<H", 1), n=4)
    return struct.unpack("<I", pl)[0] if st == 0 else 0

def wr32(s, addr, val):
    return cmd(s, ord("M"), u32(addr) + u32(4) + u32(val))[:2]

def g_pc(s):
    st, det, pl = cmd(s, ord("G"), n=68)
    return struct.unpack("<17I", pl)[15] if st == 0 else 0

def t(s):
    st, det, pl = cmd(s, ord("T"), n=4)
    return struct.unpack("<I", pl)[0] if st == 0 else 0

# str r0,[r1]; b.        ldr r0,[r1]; b.     (r0=0, r1=addr)
STRW = bytes([0x08, 0x46, 0x08, 0x60, 0x00, 0xBF, 0xFE, 0xE7])
LDRW = bytes([0x00, 0x46, 0x08, 0x68, 0x00, 0xBF, 0xFE, 0xE7])

def wp_test(s, label, comp, func, code, r1, cmpno=0):
    cmd(s, ord("H")); time.sleep(0.05)
    cmd(s, ord("M"), u32(0x20002000) + u32(len(code)) + code)
    cmd(s, ord("N"), bytes([1]) + u32(r1))
    cmd(s, ord("N"), bytes([15]) + u32(0x20002000)); time.sleep(0.02)
    COMP = 0xE0001010 + 4 * cmpno
    FUNC = 0xE0001020 + 4 * cmpno
    wr32(s, COMP, comp)
    wr32(s, FUNC, func)
    cmd(s, ord("C")); time.sleep(0.25)
    dh = t(s)
    fn = rd32(s, FUNC)
    wr32(s, FUNC, 0); wr32(s, COMP, 0)
    cmd(s, ord("H")); time.sleep(0.05)
    pc = g_pc(s)
    print("  %-48s cmp%d fired=%s dh=%08x fn=%08x pc=%08x" % (
        label, cmpno, bool(dh & (1 << 17)), dh, fn, pc))

s = conn()
print("A:", cmd(s, ord("A"))[:2])
cmd(s, ord("H")); time.sleep(0.05)

print("== load access (LDR) ==")
wp_test(s, "ldr word comp=20003000 f=08 read", 0x20003000, 0x08, LDRW, 0x20003000)
wp_test(s, "ldr word comp=20003000 f=0A read wp", 0x20003000, 0x0A, LDRW, 0x20003000)

print("== all comparators ==")
for cmpno in range(4):
    wp_test(s, "str word comp=20003000 f=0E cmp%d" % cmpno, 0x20003000, 0x0E, STRW, 0x20003000, cmpno)

print("== other addrs ==")
wp_test(s, "str word to 0x20000000 f=0E", 0x20000000, 0x0E, STRW, 0x20000000)
wp_test(s, "str word to 0x20003004 f=0E", 0x20003004, 0x0E, STRW, 0x20003004)

print("== data VALUE match (f=7) ==")
wp_test(s, "str value 0x20003000 to [r1] f=7 comp=20003000", 0x20003000, 0x07, STRW, 0x20003000)
wp_test(s, "str value 0x20003000 f=7 (value match, any addr)", 0x20003000, 0x07, STRW, 0x20001000)

print("== watch stack (SP write) f=0E ==")
# routine: push {lr}; pop {pc}: E7FD 08B5... use sub sp; str r0,[sp]
PUSHW = bytes([0x08, 0x46, 0x01, 0x90, 0xFE, 0xE7])  # mov r0,r1; str r0,[sp,#4]; b .
wp_test(s, "str r0,[sp,#4] watch sp+4", 0x20002FE4, 0x0E, PUSHW, 0x20000000)
s.close()
