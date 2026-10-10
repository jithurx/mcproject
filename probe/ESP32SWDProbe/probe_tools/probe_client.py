"""Shared client library for the ESP32 SWD probe's native binary protocol.

Talks to esp32_swd_probe.ino over a plain USB-serial connection
(SERIAL_ONLY mode, the firmware's default). Every command here mirrors
handle_native_cmd() in esp32_swd_probe/esp32_swd_probe.ino 1:1 -- that
function is the authoritative protocol definition; this module just gives
Python callers a typed method instead of hand-building command bytes the
way the one-off scripts in tests/ do.

Not a standalone tool. flash_cli.py, dump_restore.py, and introspect.py all
import this.
"""
import struct
import time

import serial

# Native protocol error codes, as returned in a failed reply's second byte
# (see reply_st() in the firmware). Codes are the negation of what each
# probe-side function returns; -1 isn't used, -99 is a client-side
# sentinel for "no reply at all" that never comes from the probe itself.
ERROR_NAMES = {
    -2: "parity error",
    -3: "SWD FAULT ack",
    -4: "WAIT retries exhausted",
    -5: "DPIDR mismatch (target not responding / not connected)",
    -6: "debug power-up timeout",
    -7: "core did not halt/step in time",
    -8: "no free FPB comparator",
    -9: "no free DWT/software watchpoint slot",
    -10: "flash PGERR/WRPRTERR",
    -11: "flash busy-wait timeout",
    -12: "probe out of memory",
    -13: "verify mismatch",
    -14: "bad command / bad parameters",
    -15: "FPB V1: address outside code region",
    -16: "no MEM-AP found",
    -99: "no reply from probe (not connected / still booting?)",
}


class ProbeError(RuntimeError):
    """Raised for both protocol-level errors (status != 0) and a missing
    reply. `code` is the negative int from ERROR_NAMES, or -99 if the probe
    never replied at all."""

    def __init__(self, code):
        self.code = code
        super().__init__("probe error %d (%s)" % (code, ERROR_NAMES.get(code, "unknown")))


class Probe:
    """One open connection to the probe's native protocol.

    Usage:
        with Probe("COM10") as p:
            p.halt()
            regs = p.read_regs()
    """

    def __init__(self, port, baud=115200, boot_timeout=10.0):
        self.ser = serial.Serial(port, baud, timeout=5)
        self._wait_boot(boot_timeout)

    def close(self):
        self.ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- transport -----------------------------------------------------

    def _wait_boot(self, timeout):
        """Drain the ESP32's reset/boot banner before sending anything.

        Opening the serial port pulses DTR/RTS on most ESP32 boards, which
        resets the chip -- without this, the first real command can be sent
        while ROM/sketch boot text is still arriving, and the 2-byte reply
        header gets desynced against banner bytes (this exact failure mode
        cost a debugging session earlier: every reply looked like garbage
        until this drain was added)."""
        s = self.ser
        s.timeout = 1.0
        t0 = time.time()
        last_data = time.time()
        while time.time() - t0 < timeout:
            if s.in_waiting:
                s.read(s.in_waiting)
                last_data = time.time()
            elif time.time() - last_data > 1.0:
                break
            else:
                time.sleep(0.05)
        s.reset_input_buffer()

    def _recvall(self, n, timeout=5):
        self.ser.timeout = timeout
        data = b""
        while len(data) < n:
            chunk = self.ser.read(n - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def _cmd(self, op, body=b"", reply_len=0, timeout=5):
        self.ser.write(bytes([op]) + body)
        hdr = self._recvall(2, timeout=timeout)
        if len(hdr) != 2:
            raise ProbeError(-99)
        status, detail = hdr[0], hdr[1]
        if status != 0:
            code = detail - 256 if detail >= 128 else detail
            raise ProbeError(code)
        return self._recvall(reply_len, timeout=timeout) if reply_len else b""

    # ---- low-level primitives (1:1 with handle_native_cmd) --------------

    def alive(self):
        """'A': raises ProbeError if the probe doesn't answer at all."""
        self._cmd(ord("A"))

    def read_block(self, addr, nwords):
        """'X': raw word-aligned block read, capped at 1024 words (4KB) --
        the firmware clamps to this itself; check here so the error is
        immediate and points at the actual limit."""
        if nwords > 1024:
            raise ValueError("nwords=%d exceeds the probe's 1024-word ('X') cap per call" % nwords)
        body = struct.pack("<IH", addr, nwords)
        return self._cmd(ord("X"), body, reply_len=nwords * 4, timeout=10)

    def write_mem(self, addr, data):
        """'M': raw memory write (any alignment), capped at 4096 bytes."""
        if len(data) > 4096:
            raise ValueError("len(data)=%d exceeds the probe's 4096-byte ('M') cap per call" % len(data))
        body = struct.pack("<II", addr, len(data)) + data
        self._cmd(ord("M"), body, timeout=10)

    def halt(self):
        self._cmd(ord("H"))

    def resume(self):
        self._cmd(ord("C"))

    def step(self):
        self._cmd(ord("S"))

    def reset(self):
        """'U': AIRCR.SYSRESETREQ. Resets the target core; does not affect
        the ESP32 probe or the serial link."""
        self._cmd(ord("U"))

    def status(self):
        """'T': raw DHCSR value."""
        pl = self._cmd(ord("T"), reply_len=4)
        return struct.unpack("<I", pl)[0]

    def halted(self):
        return bool(self.status() & (1 << 17))  # DHCSR.S_HALT

    def halt_reason(self):
        """'J': live DFSR read+clear (not a cache -- see the firmware
        comment on this command for why that distinction matters)."""
        pl = self._cmd(ord("J"), reply_len=4)
        return struct.unpack("<I", pl)[0]

    def read_regs(self):
        """'G': r0-r12, sp, lr, (debug return addr / pc), xpsr -- 17 words.
        Only meaningful while halted."""
        pl = self._cmd(ord("G"), reply_len=17 * 4)
        return struct.unpack("<17I", pl)

    def write_reg(self, sel, val):
        """sel is a DCRSR REGSEL value (0-12=r0-r12, 13=sp, 14=lr, 15=pc,
        16=xpsr, 20=PRIMASK, ...) -- see the ARMv7-M debug reg map."""
        self._cmd(ord("N"), bytes([sel]) + struct.pack("<I", val))

    # 'F' and 'V' both always append a trailing slot byte on success --
    # even for clear/clear-all, where the firmware's `slot` local stays -1
    # (0xFF) because only the *_set() branch ever assigns it. Every call
    # below passes reply_len=1 for that reason; dropping it on a clear call
    # would leave that byte sitting in the stream to desync whatever the
    # next command's reply header parses as.

    def bp_set(self, addr):
        """'F' op=1: returns the FPB comparator slot used."""
        pl = self._cmd(ord("F"), bytes([1]) + struct.pack("<I", addr), reply_len=1)
        return pl[0]

    def bp_clear(self, addr):
        self._cmd(ord("F"), bytes([0]) + struct.pack("<I", addr), reply_len=1)

    def bp_clear_all(self):
        self._cmd(ord("F"), bytes([2]) + struct.pack("<I", 0), reply_len=1)

    def wp_set(self, addr, kind=7):
        """'V' op=1. kind is a DWT_FUNCTION encoding: 4=any access,
        6=read, 7=write (the default -- the common case)."""
        pl = self._cmd(ord("V"), bytes([1]) + struct.pack("<I", addr) + bytes([kind]), reply_len=1)
        return pl[0]

    def wp_clear(self, addr):
        self._cmd(ord("V"), bytes([0]) + struct.pack("<I", addr) + bytes([0]), reply_len=1)

    def wp_clear_all(self):
        self._cmd(ord("V"), bytes([2]) + struct.pack("<I", 0) + bytes([0]), reply_len=1)

    def flash_erase(self, addr, length):
        """'Y'. addr=0, length=0 means mass erase (both banks, if the
        target is dual-bank). Note flash_program() below already erases
        the pages it touches on its own -- an explicit erase is only
        useful to wipe flash *beyond* what you're about to (re)program,
        e.g. after flashing a smaller image over a larger one."""
        self._cmd(ord("Y"), struct.pack("<II", addr, length), timeout=15)

    def flash_program(self, addr, data):
        """'P': halts, erases the covering page(s), programs, and
        verifies -- one round trip, up to 4096 bytes. addr and len(data)
        must both be even (STM32F1 flash is written a halfword at a time)."""
        if len(data) > 4096 or len(data) % 2 or addr % 2:
            raise ValueError("flash_program: addr and len(data) must be even, len(data) <= 4096")
        self._cmd(ord("P"), struct.pack("<II", addr, len(data)) + data, timeout=15)

    def dp_read(self, reg):
        pl = self._cmd(ord("D"), bytes([0, 0, reg]), reply_len=4)
        return struct.unpack("<I", pl)[0]

    def ap_read(self, bank, reg):
        pl = self._cmd(ord("D"), bytes([1, bank, reg]), reply_len=4)
        return struct.unpack("<I", pl)[0]

    def dp_write(self, reg, val):
        self._cmd(ord("W"), bytes([0, 0, reg]) + struct.pack("<I", val))

    def ap_write(self, bank, reg, val):
        self._cmd(ord("W"), bytes([1, bank, reg]) + struct.pack("<I", val))

    def chip_info(self):
        """'I': (dpidr, flash_size_register, dbgmcu_idcode). The flash
        size register (0x1FFFF7E0) reports size in KiB in its low 16
        bits -- see flash_bytes_total() for the usable form."""
        pl = self._cmd(ord("I"), reply_len=12)
        return struct.unpack("<III", pl)

    def part_info(self):
        """'K': (cpuid, flash_page_size, dual_bank, ap_sel)."""
        pl = self._cmd(ord("K"), reply_len=10)
        cpuid, page_size = struct.unpack("<II", pl[:8])
        return cpuid, page_size, bool(pl[8]), pl[9]

    # ---- higher-level helpers (chunking, not new protocol) --------------

    def read_mem(self, addr, length):
        """Arbitrary-length read, chunked to the probe's 4KB/request cap."""
        out = bytearray()
        a, remaining = addr, length
        while remaining:
            n = min(remaining, 4096)
            out += self.read_block(a, (n + 3) // 4)[:n]
            a += n
            remaining -= n
        return bytes(out)

    def write_mem_bulk(self, addr, data):
        """Arbitrary-length RAM write, chunked to the probe's 4KB/request cap."""
        a, off = addr, 0
        while off < len(data):
            n = min(len(data) - off, 4096)
            self.write_mem(a, data[off:off + n])
            a += n
            off += n

    def flash_program_bulk(self, addr, data, on_progress=None):
        """Program arbitrary-length data, chunked to the probe's 4KB
        ('P') cap. Each chunk erases+programs+verifies independently
        (flash_program_flash() in firmware); chunk size (4096) is a
        multiple of both possible STM32F1 page sizes (1024, 2048), so as
        long as `addr` itself is page-aligned -- true for any real flash
        image, which starts at 0x08000000 -- chunk boundaries always land
        on page boundaries too. That matters: if a chunk's internal erase
        ever straddled into a page a previous chunk had already written,
        it would silently erase that earlier write."""
        a, off = addr, 0
        while off < len(data):
            n = min(len(data) - off, 4096)
            self.flash_program(a, data[off:off + n])
            a += n
            off += n
            if on_progress:
                on_progress(off, len(data))

    def flash_bytes_total(self):
        """Flash size in bytes, from the 'I' command's hardware flash-size
        register."""
        _, fsize_reg, _ = self.chip_info()
        return (fsize_reg & 0xFFFF) * 1024
