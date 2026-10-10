import socket, time, struct

HOST = "192.168.0.192"
PORT = 5555

def connect():
    s = socket.create_connection((HOST, PORT), timeout=5)
    return s

def recvn(s, n, timeout=10):
    s.settimeout(timeout)
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise RuntimeError("connection closed, got %d/%d" % (len(data), n))
        data += chunk
    return data

def cmd(s, byte, params=b""):
    s.sendall(bytes([byte]) + params)
    st, det = recvn(s, 2)
    return st, det

def cmd_payload(s, byte, params=b"", n=0):
    s.sendall(bytes([byte]) + params)
    st, det = recvn(s, 2)
    payload = recvn(s, n) if n else b""
    return st, det, payload

def u32(x):
    return struct.pack("<I", x)

def test(name, ok, extra=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + extra if extra else ""))

def main():
    ok = True
    s = connect()
    print("connected to %s:%d" % (HOST, PORT))

    # alive
    st, det = cmd(s, ord("A"))
    test("A alive", st == 0 and det == 0, "st=%d det=%d" % (st, det))
    ok &= (st == 0)

    # info
    st, det, pl = cmd_payload(s, ord("I"), n=12)
    if st == 0:
        dpidr, fsize, idcode = struct.unpack("<III", pl)
        test("I info", dpidr == 0x1ba01477 and fsize == 0x80, "dpidr=%08x fsize=%02x idcode=%04x" % (dpidr, fsize, idcode))
        ok &= (dpidr == 0x1ba01477)
    else:
        test("I info", False, "st=%d det=%d" % (st, det)); ok = False

    # status (running)
    st, det, pl = cmd_payload(s, ord("T"), n=4)
    dhcsr = struct.unpack("<I", pl)[0] if st == 0 else 0
    running = st == 0 and not (dhcsr & (1 << 17))
    test("T running", running, "dhcsr=%08x" % dhcsr)
    ok &= running

    # halt
    st, det = cmd(s, ord("H"))
    test("H halt", st == 0, "st=%d det=%d" % (st, det))
    ok &= (st == 0)
    time.sleep(0.05)

    # status (halted)
    st, det, pl = cmd_payload(s, ord("T"), n=4)
    dhcsr = struct.unpack("<I", pl)[0] if st == 0 else 0
    halted = st == 0 and bool(dhcsr & (1 << 17))
    test("T halted", halted, "dhcsr=%08x" % dhcsr)
    ok &= halted

    # read regs
    st, det, pl = cmd_payload(s, ord("G"), n=17 * 4)
    if st == 0:
        regs = struct.unpack("<17I", pl)
        pc, sp = regs[15], regs[13]
        ok_r = (0x08000000 <= pc <= 0x0801FFFF) or (0x20000000 <= pc <= 0x20010000)
        ok_s = 0x20000000 <= sp <= 0x20010000
        test("G regs", ok_r and ok_s, "pc=%08x sp=%08x xpsr=%08x" % (pc, sp, regs[16]))
        ok &= (ok_r and ok_s)
    else:
        test("G regs", False, "st=%d" % st); ok = False

    # write r0
    st, det = cmd(s, ord("N"), bytes([0]) + u32(0xDEADBEEF))
    test("N write r0", st == 0, "st=%d" % st)
    ok &= (st == 0)
    st, det, pl = cmd_payload(s, ord("G"), n=17 * 4)
    r0 = struct.unpack("<17I", pl)[0] if st == 0 else 0
    test("N r0 readback", st == 0 and r0 == 0xDEADBEEF, "r0=%08x" % r0)
    ok &= (st == 0 and r0 == 0xDEADBEEF)

    # step
    st, det, pl = cmd_payload(s, ord("G"), n=17 * 4)
    pc0 = struct.unpack("<17I", pl)[15] if st == 0 else 0
    st, det = cmd(s, ord("S"))
    time.sleep(0.05)
    st2, det2, pl = cmd_payload(s, ord("G"), n=17 * 4)
    pc1 = struct.unpack("<17I", pl)[15] if st2 == 0 else 0
    test("S step", st == 0 and st2 == 0 and pc1 != pc0, "pc %08x -> %08x" % (pc0, pc1))
    ok &= (st == 0 and st2 == 0 and pc1 != pc0)

    # memory write/read SRAM
    pattern = bytes(range(256))
    st, det = cmd(s, ord("M"), u32(0x20001000) + u32(len(pattern)) + pattern)
    test("M write sram", st == 0, "st=%d" % st)
    ok &= (st == 0)
    st, det, pl = cmd_payload(s, ord("X"), u32(0x20001000) + struct.pack("<H", len(pattern) // 4), n=len(pattern))
    test("X read sram", st == 0 and pl == pattern, "st=%d len=%d" % (st, len(pl)))
    ok &= (st == 0 and pl == pattern)

    # AP/DP register reads
    st, det, pl = cmd_payload(s, ord("D"), bytes([1, 0xF, 0xC]), n=4)
    idr = struct.unpack("<I", pl)[0] if st == 0 else 0
    test("D AP IDR", st == 0 and idr == 0x00030003, "idr=%08x" % idr)
    ok &= (st == 0 and idr == 0x00030003)
    st, det, pl = cmd_payload(s, ord("D"), bytes([0, 0, 0]), n=4)
    dpidr = struct.unpack("<I", pl)[0] if st == 0 else 0
    test("D DPIDR", st == 0 and dpidr == 0x1ba01477, "dpidr=%08x" % dpidr)
    ok &= (st == 0 and dpidr == 0x1ba01477)

    # breakpoint: set at current PC, resume, expect immediate halt
    st, det, pl = cmd_payload(s, ord("G"), n=17 * 4)
    pc = struct.unpack("<17I", pl)[15]
    st, det, pl = cmd_payload(s, ord("F"), bytes([1]) + u32(pc), n=1)
    slot = pl[0] if st == 0 else 0xFF
    test("F bp set", st == 0, "slot=%d" % slot)
    ok &= (st == 0)
    time.sleep(0.02)
    st, det = cmd(s, ord("C"))
    time.sleep(0.15)
    st, det, pl = cmd_payload(s, ord("T"), n=4)
    dhcsr = struct.unpack("<I", pl)[0] if st == 0 else 0
    bp_hit = st == 0 and bool(dhcsr & (1 << 17))
    test("F bp hit on resume", bp_hit, "dhcsr=%08x" % dhcsr)
    ok &= bp_hit
    st, det, pl = cmd_payload(s, ord("F"), bytes([0]) + u32(pc), n=1)
    test("F bp clear", st == 0, "")
    ok &= (st == 0)
    st, det, pl = cmd_payload(s, ord("G"), n=17 * 4)
    pc2 = struct.unpack("<17I", pl)[15] if st == 0 else 0
    test("G pc at bp", st == 0 and pc2 == pc, "pc=%08x" % pc2)
    ok &= (st == 0 and pc2 == pc)

    # watchpoint set/clear (behavior verified only if target writes addr)
    st, det, pl = cmd_payload(s, ord("V"), bytes([1]) + u32(0x20002000) + bytes([7]), n=1)
    test("V wp set", st == 0, "slot=%d" % (pl[0] if st == 0 else -1))
    ok &= (st == 0)
    st, det, pl = cmd_payload(s, ord("V"), bytes([0]) + u32(0x20002000) + bytes([7]), n=1)
    test("V wp clear", st == 0, "")
    ok &= (st == 0)

    # flash: program + verify on last page, then erase + verify FF
    faddr = 0x0801FC00
    fdata = bytes((i * 7) & 0xFF for i in range(128))
    st, det = cmd(s, ord("P"), u32(faddr) + u32(len(fdata)) + fdata)
    test("P flash program+verify", st == 0, "st=%d det=%d" % (st, det))
    ok &= (st == 0)
    st, det, pl = cmd_payload(s, ord("X"), u32(faddr) + struct.pack("<H", len(fdata) // 4), n=len(fdata))
    test("X flash readback", st == 0 and pl == fdata, "match=%s" % (pl == fdata))
    ok &= (st == 0 and pl == fdata)
    st, det = cmd(s, ord("Y"), u32(faddr) + u32(1024))
    test("Y flash erase", st == 0, "st=%d" % st)
    ok &= (st == 0)
    st, det, pl = cmd_payload(s, ord("X"), u32(faddr) + struct.pack("<H", 256), n=1024)
    erased = st == 0 and all(b == 0xFF for b in pl)
    test("X flash erased FF", erased, "")
    ok &= erased

    # resume
    st, det = cmd(s, ord("C"))
    time.sleep(0.1)
    st, det, pl = cmd_payload(s, ord("T"), n=4)
    dhcsr = struct.unpack("<I", pl)[0] if st == 0 else 0
    test("C resume running", st == 0 and not (dhcsr & (1 << 17)), "dhcsr=%08x" % dhcsr)
    ok &= (st == 0 and not (dhcsr & (1 << 17)))

    s.close()
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
