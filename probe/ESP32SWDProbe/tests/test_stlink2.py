import socket, serial, time, struct, sys

HOST = "192.168.0.192"
PORT = 5555
SERIAL_PORT = None

# SRAM test routine (Cortex-M3 Thumb):
#   0x20002000: mov r0, r1    08 46
#   0x20002002: str r0, [r1]  08 60
#   0x20002004: nop           00 BF
#   0x20002006: b .           FE E7
ROUTINE = bytes([0x08, 0x46, 0x08, 0x60, 0x00, 0xBF, 0xFE, 0xE7])
ROUTINE_ADDR = 0x20002000

def wait_boot(s):
    # Opening the port pulses DTR/RTS on most ESP32 boards, which resets the
    # chip; drain the ROM + sketch boot banner (settles ~0.3-0.5s) before
    # sending binary protocol bytes, or the first replies desync.
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

def connect():
    if SERIAL_PORT:
        s = serial.Serial(SERIAL_PORT, 115200, timeout=5)
        wait_boot(s)
        return s
    return socket.create_connection((HOST, PORT), timeout=5)

def recvn(s, n, timeout=10):
    if hasattr(s, 'settimeout'):
        s.settimeout(timeout)
    else:
        s.timeout = timeout
    data = b""
    while len(data) < n:
        if hasattr(s, 'recv'):
            chunk = s.recv(n - len(data))
        else:
            chunk = s.read(n - len(data))
        if not chunk:
            raise RuntimeError("connection closed, got %d/%d" % (len(data), n))
        data += chunk
    return data

def cmd(s, byte, params=b""):
    if hasattr(s, 'sendall'):
        s.sendall(bytes([byte]) + params)
    else:
        s.write(bytes([byte]) + params)
    st, det = recvn(s, 2)
    return st, det

def cmd_payload(s, byte, params=b"", n=0):
    if hasattr(s, 'sendall'):
        s.sendall(bytes([byte]) + params)
    else:
        s.write(bytes([byte]) + params)
    st, det = recvn(s, 2)
    payload = recvn(s, n) if n else b""
    return st, det, payload

def u32(x):
    return struct.pack("<I", x)

def read_pc(s):
    st, det, pl = cmd_payload(s, ord("G"), n=17 * 4)
    if st != 0:
        return None
    return struct.unpack("<17I", pl)[15]

def write_reg(s, sel, val):
    return cmd(s, ord("N"), bytes([sel]) + u32(val))[0] == 0

def test(name, ok, extra=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + extra if extra else ""))

def main():
    global SERIAL_PORT
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--serial" and i + 1 < len(sys.argv):
            SERIAL_PORT = sys.argv[i + 1]
    ok = True
    s = connect()
    if SERIAL_PORT:
        print("connected to serial %s" % SERIAL_PORT)
    else:
        print("connected to %s:%d" % (HOST, PORT))

    # alive
    st, det = cmd(s, ord("A"))
    test("A alive", st == 0 and det == 0, "st=%d det=%d" % (st, det))
    ok &= (st == 0)

    # info
    st, det, pl = cmd_payload(s, ord("I"), n=12)
    if st == 0:
        dpidr, fsize, idcode = struct.unpack("<III", pl)
        test("I info", dpidr == 0x1ba01477 and fsize == 0x80,
             "dpidr=%08x fsize=%02x idcode=%08x" % (dpidr, fsize, idcode))
        ok &= (dpidr == 0x1ba01477)
    else:
        test("I info", False, "st=%d det=%d" % (st, det)); ok = False

    # reset so the FC firmware boots and runs
    st, det = cmd(s, ord("U"))
    test("U sysreset", st == 0, "st=%d" % st)
    ok &= (st == 0)
    time.sleep(0.7)

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
        ok_r = 0x08000000 <= pc <= 0x0801FFFF
        ok_s = 0x20000000 <= sp <= 0x20010000
        test("G regs", ok_r and ok_s, "pc=%08x sp=%08x xpsr=%08x" % (pc, sp, regs[16]))
        ok &= (ok_r and ok_s)
    else:
        test("G regs", False, "st=%d" % st); ok = False

    # write r0 + readback
    st, det = cmd(s, ord("N"), bytes([0]) + u32(0xDEADBEEF))
    test("N write r0", st == 0, "st=%d" % st)
    ok &= (st == 0)
    st, det, pl = cmd_payload(s, ord("G"), n=17 * 4)
    r0 = struct.unpack("<17I", pl)[0] if st == 0 else 0
    test("N r0 readback", st == 0 and r0 == 0xDEADBEEF, "r0=%08x" % r0)
    ok &= (st == 0 and r0 == 0xDEADBEEF)

    # --- deterministic step test in SRAM ---
    # mask interrupts while running test code
    write_reg(s, 20, 1)  # PRIMASK (DCRSR REGSEL 20)
    st, det = cmd(s, ord("M"), u32(ROUTINE_ADDR) + u32(len(ROUTINE)) + ROUTINE)
    test("M load routine", st == 0, "st=%d" % st)
    ok &= (st == 0)
    write_reg(s, 1, 0x20003000)  # r1 = scratch addr
    write_reg(s, 15, ROUTINE_ADDR)  # pc
    st = True
    prev = read_pc(s)
    for i in range(3):
        stp, det = cmd(s, ord("S"))
        time.sleep(0.03)
        cur = read_pc(s)
        st &= (stp == 0 and cur is not None and cur != prev)
        prev = cur
    test("S step x3 (nop/str path)", st, "pc0=%08x pc3=%s" % (ROUTINE_ADDR, ("%08x" % prev) if prev is not None else "None"))
    ok &= st

    # --- breakpoint test (FPB V1: flash addresses only) ---
    # Deterministic: set the pc straight into the FC's flash loop so the FPB
    # trap fires without depending on boot timing.
    FLASH_LOOP = 0x08001520
    st, det = cmd(s, ord("U"))          # reboot so the FC firmware runs
    test("U reset for bp test", st == 0, "st=%d" % st)
    ok &= (st == 0)
    time.sleep(0.7)
    st, det = cmd(s, ord("H"))
    test("H halt after reset", st == 0, "st=%d" % st)
    ok &= (st == 0)
    time.sleep(0.05)
    write_reg(s, 15, FLASH_LOOP)
    st, det, pl = cmd_payload(s, ord("F"), bytes([1]) + u32(FLASH_LOOP), n=1)
    slot = pl[0] if st == 0 else -1
    test("F bp set", st == 0, "slot=%d" % slot)
    ok &= (st == 0)
    time.sleep(0.02)
    st, det = cmd(s, ord("C"))
    time.sleep(0.15)
    st, det, pl = cmd_payload(s, ord("T"), n=4)
    dhcsr = struct.unpack("<I", pl)[0] if st == 0 else 0
    halted = bool(dhcsr & (1 << 17))
    pc = read_pc(s)
    # Require the halted bit too -- pc==FLASH_LOOP alone can be a stale
    # DCRDR readback from the write_reg(15, FLASH_LOOP) above if the FPB
    # breakpoint never actually halted the core (see IMPROVEMENT_PLAN.md
    # "FPB resume timing FAIL").
    bp_hit = halted and (pc == FLASH_LOOP)
    test("F bp hit on resume", bp_hit, "dhcsr=%08x pc=%08x" % (dhcsr, pc or 0))
    ok &= bp_hit
    test("G pc at bp", pc == FLASH_LOOP, "pc=%08x" % (pc or 0))
    ok &= (pc == FLASH_LOOP)
    st, det, pl = cmd_payload(s, ord("F"), bytes([0]) + u32(FLASH_LOOP), n=1)
    test("F bp clear", st == 0, "")
    ok &= (st == 0)

    # --- watchpoint test (write to 0x20003000) ---
    st, det = cmd(s, ord("H"))
    test("H for wp test", st == 0, "st=%d" % st)
    ok &= (st == 0)
    time.sleep(0.05)
    write_reg(s, 20, 1)  # PRIMASK (DCRSR REGSEL 20); reset cleared it
    write_reg(s, 1, 0x20003000)
    write_reg(s, 15, ROUTINE_ADDR)
    # prime the watched word so the routine's store actually changes it
    st, det = cmd(s, ord("M"), u32(0x20003000) + u32(4) + struct.pack("<I", 0))
    test("M prime wp addr", st == 0, "st=%d" % st)
    ok &= (st == 0)
    st, det = cmd(s, ord("M"), u32(ROUTINE_ADDR) + u32(len(ROUTINE)) + ROUTINE)
    test("M load routine", st == 0, "st=%d" % st)
    ok &= (st == 0)
    time.sleep(0.02)
    st, det, pl = cmd_payload(s, ord("V"), bytes([1]) + u32(0x20003000) + bytes([7]), n=1)
    test("V wp set (write)", st == 0, "slot=%d" % (pl[0] if st == 0 else -1))
    ok &= (st == 0)
    time.sleep(0.02)
    st, det = cmd(s, ord("C"))
    time.sleep(0.15)
    st, det, pl = cmd_payload(s, ord("T"), n=4)
    dhcsr = struct.unpack("<I", pl)[0] if st == 0 else 0
    wp_hit = st == 0 and bool(dhcsr & (1 << 17))
    test("V wp hit on write", wp_hit, "dhcsr=%08x" % dhcsr)
    ok &= wp_hit
    pc = read_pc(s)
    test("G pc at wp", pc in (ROUTINE_ADDR + 2, ROUTINE_ADDR + 4), "pc=%08x" % (pc or 0))
    ok &= (pc in (ROUTINE_ADDR + 2, ROUTINE_ADDR + 4))
    st, det, pl = cmd_payload(s, ord("V"), bytes([0]) + u32(0x20003000) + bytes([7]), n=1)
    test("V wp clear", st == 0, "")
    ok &= (st == 0)
    write_reg(s, 20, 0)  # restore PRIMASK

    # --- memory roundtrip: aligned, halfword, byte paths ---
    ok_m = True
    for addr, ln in [(0x20004000, 256), (0x20004002, 6), (0x20004001, 3)]:
        pattern = bytes((i * 11 + addr) & 0xFF for i in range(ln))
        st, det = cmd(s, ord("M"), u32(addr) + u32(ln) + pattern)
        m_ok = (st == 0)
        nw = (ln + 3) // 4
        st, det, pl = cmd_payload(s, ord("X"), u32(addr) + struct.pack("<H", nw), n=nw * 4)
        x_ok = (st == 0 and pl[:ln] == pattern)
        ok_m &= (m_ok and x_ok)
        test("M/X sram @%08x len=%d" % (addr, ln), m_ok and x_ok,
             "m=%d x=%d match=%s" % (st, m_ok, pl[:ln] == pattern))
    ok &= ok_m

    # --- AP/DP register reads ---
    st, det, pl = cmd_payload(s, ord("D"), bytes([1, 0xF, 0xC]), n=4)
    idr = struct.unpack("<I", pl)[0] if st == 0 else 0
    test("D AP IDR", st == 0 and idr != 0 and idr != 0xFFFFFFFF, "idr=%08x" % idr)
    ok &= (st == 0 and idr != 0)
    st, det, pl = cmd_payload(s, ord("D"), bytes([0, 0, 0]), n=4)
    dpidr = struct.unpack("<I", pl)[0] if st == 0 else 0
    test("D DPIDR", st == 0 and dpidr == 0x1ba01477, "dpidr=%08x" % dpidr)
    ok &= (st == 0 and dpidr == 0x1ba01477)

    # --- flash: program + verify, readback, erase, verify FF ---
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

    # --- resume and confirm running, then reset to restore FC firmware ---
    st, det = cmd(s, ord("C"))
    time.sleep(0.1)
    st, det, pl = cmd_payload(s, ord("T"), n=4)
    dhcsr = struct.unpack("<I", pl)[0] if st == 0 else 0
    test("C resume running", st == 0 and not (dhcsr & (1 << 17)), "dhcsr=%08x" % dhcsr)
    ok &= (st == 0 and not (dhcsr & (1 << 17)))
    st, det = cmd(s, ord("U"))
    test("U sysreset (restore)", st == 0, "st=%d" % st)
    ok &= (st == 0)

    s.close()
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
