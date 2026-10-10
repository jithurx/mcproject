import serial, struct, time, sys

PORT = "COM10"

def recvall(s, n, timeout=5):
    s.timeout = timeout
    data = b""
    while len(data) < n:
        chunk = s.read(n - len(data))
        if not chunk:
            break
        data += chunk
    return data

def cmd_payload(s, op, body=b"", n=0):
    s.write(bytes([op]) + body)
    hdr = recvall(s, 2)
    if len(hdr) != 2:
        return (-99, 0, b"")
    st, det = hdr[0], hdr[1]
    pl = recvall(s, n) if n and st == 0 else b""
    return (st, det, pl)

def u32(v):
    return struct.pack("<I", v)

def xread(s, addr, nw):
    st, det, pl = cmd_payload(s, ord("X"), u32(addr) + struct.pack("<H", nw), n=nw * 4)
    return (st, det, pl)

def mwrite(s, addr, data):
    return cmd_payload(s, ord("M"), u32(addr) + u32(len(data)) + data)

def wait_boot(s):
    s.timeout = 1.0
    t0 = time.time()
    last_data = time.time()
    while time.time() - t0 < 10:
        if s.in_waiting:
            s.read(s.in_waiting)
            last_data = time.time()
        elif time.time() - last_data > 1.0:
            break
        else:
            time.sleep(0.05)
    s.reset_input_buffer()

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else PORT
    s = serial.Serial(port, 115200, timeout=5)
    wait_boot(s)

    cmd_payload(s, ord("A"))
    cmd_payload(s, ord("I"), n=12)
    cmd_payload(s, ord("U"))
    time.sleep(0.3)
    cmd_payload(s, ord("T"), n=4)
    cmd_payload(s, ord("H"))

    base = 0x20004000
    print("pre-read 4w @%08x: %s" % (base, xread(s, base, 4)[2].hex()))

    print("word write 4B [11 22 33 44] @%08x:" % base, mwrite(s, base, bytes([0x11, 0x22, 0x33, 0x44])))
    print("  read 4w: %s" % xread(s, base, 4)[2].hex())

    print("hw write 2B [CD EF] @%08x:" % (base + 2), mwrite(s, base + 2, bytes([0xCD, 0xEF])))
    print("  read 4w: %s" % xread(s, base, 4)[2].hex())

    print("byte write 1B [A5] @%08x:" % (base + 1), mwrite(s, base + 1, bytes([0xA5])))
    print("  read 4w: %s" % xread(s, base, 4)[2].hex())

    print("hw write 2B [77 88] @%08x:" % (base + 6), mwrite(s, base + 6, bytes([0x77, 0x88])))
    print("  read 4w: %s" % xread(s, base, 4)[2].hex())

    s.close()

if __name__ == "__main__":
    main()
