import subprocess, re, time, os, sys

OPENOCD = r"C:\Users\cecilia\AppData\Local\Arduino15\packages\esp32\tools\openocd-esp32\v0.12.0-esp32-20260424\bin\openocd.exe"
SCRIPTS = r"C:\Users\cecilia\AppData\Local\Arduino15\packages\esp32\tools\openocd-esp32\v0.12.0-esp32-20260424\share\openocd\scripts"
TMP = r"C:\Users\cecilia\AppData\Local\Temp\opencode"

START = 0x08000000
END   = 0x08004000
OUT   = os.path.join(TMP, "stm32_flash.bin")

data = bytearray(b'\xff' * (END - START))

def run_session(reads):
    cfg = os.path.join(TMP, "chunk.cfg")
    lines = [
        "adapter driver remote_bitbang",
        "remote_bitbang host 192.168.0.192",
        "remote_bitbang port 5555",
        "transport select swd",
        "adapter speed 1000",
        "source [find target/stm32f1x.cfg]",
        "reset_config none",
        "cortex_m reset_config sysresetreq",
        "init",
    ]
    for addr, nwords in reads:
        lines.append("mdw 0x%08x %d" % (addr, nwords))
    lines.append("shutdown")
    with open(cfg, "w") as f:
        f.write("\n".join(lines) + "\n")
    p = subprocess.run([OPENOCD, "-s", SCRIPTS, "-f", cfg],
                       capture_output=True, text=True, timeout=240)
    return p.stdout + p.stderr

WORD_RE = re.compile(r'0x([0-9a-fA-F]{8}):((?:\s+[0-9a-fA-F]{8})+)')

def parse(out):
    words = {}
    for m in WORD_RE.finditer(out):
        base = int(m.group(1), 16)
        for i, w in enumerate(m.group(2).split()):
            words[base + 4 * i] = int(w, 16)
    return words

read_chunks = set()

def read_chunk(addr, nwords):
    for attempt in range(1, 7):
        try:
            out = run_session([(addr, nwords)])
        except subprocess.TimeoutExpired:
            print("  attempt %d: TIMEOUT" % attempt)
            time.sleep(2)
            continue
        got = parse(out)
        if len(got) == nwords:
            for off, w in got.items():
                data[off - START: off - START + 4] = w.to_bytes(4, 'little')
            read_chunks.add(addr)
            return True
        got_addr = sorted(got.keys())
        got_ok = sum(1 for a in got_addr if a >= addr)
        print("  attempt %d: got %d/%d words (last %s) err=%s" %
              (attempt, len(got), nwords, hex(got_addr[-1]) if got_addr else "-",
               "yes" if "Error" in out else "no"))
        time.sleep(1)
    return False

def main():
    chunk = 0x100
    todo = []
    addr = START
    while addr < END:
        todo.append(addr)
        addr += chunk
    t0 = time.time()
    for i, addr in enumerate(todo):
        nwords = chunk // 4
        if addr in read_chunks:
            print("[%2d/%d] 0x%08x skip (read)" % (i + 1, len(todo), addr))
            continue
        print("[%2d/%d] 0x%08x read %d words" % (i + 1, len(todo), addr, nwords))
        ok = read_chunk(addr, nwords)
        if not ok:
            print("  !! FAILED after retries at 0x%08x" % addr)
    with open(OUT, "wb") as f:
        f.write(bytes(data))
    print("DONE in %.1fs, wrote %s (%d bytes)" % (time.time() - t0, OUT, len(data)))

if __name__ == "__main__":
    main()
