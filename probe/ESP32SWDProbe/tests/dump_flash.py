import subprocess, sys, re, os, time

OC = r"C:\Users\cecilia\AppData\Local\Arduino15\packages\esp32\tools\openocd-esp32\v0.12.0-esp32-20260424"
SCRIPTS = OC + r"\share\openocd\scripts"
OUTBIN = r"C:\Users\cecilia\AppData\Local\Temp\opencode\stm32_flash.bin"
LOGF = r"C:\Users\cecilia\AppData\Local\Temp\opencode\flash_dump.txt"

base = 0x08000000
length = 0x10000
chunk = 0x100
words_per = chunk // 4
nchunks = length // chunk

buf = bytearray([0xFF]) * length
have = set()

def parse_log():
    with open(LOGF, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"0x([0-9a-fA-F]{8}):((?: [0-9a-fA-F]{8})+)", line)
            if not m:
                continue
            addr = int(m.group(1), 16)
            off = addr - base
            words = m.group(2).split()
            for i, w in enumerate(words):
                o = off + i * 4
                if 0 <= o < length and w not in ("????????", "0x????????"):
                    buf[o:o+4] = bytes.fromhex(w)[::-1]
                    have.add(o)

batch = 8

def run(chunk_idx):
    args = [OC + r"\bin\openocd.exe", "-s", SCRIPTS,
            "-c", "adapter driver remote_bitbang",
            "-c", "remote_bitbang host 192.168.0.192",
            "-c", "remote_bitbang port 5555",
            "-c", "transport select swd",
            "-c", "adapter speed 1000",
            "-f", "target/stm32f1x.cfg",
            "-c", "reset_config none",
            "-c", "cortex_m reset_config sysresetreq",
            "-c", "init",
            "-c", "reset halt"]
    for c in chunk_idx:
        args += ["-c", "mdw 0x%08X %d" % (base + c * chunk, words_per)]
    args += ["-c", "shutdown"]
    with open(LOGF, "w") as f:
        r = subprocess.run(args, stdout=f, stderr=subprocess.STDOUT, timeout=90)
    return r.returncode

missing = [c for c in range(nchunks)]
for attempt in range(200):
    missing = [c for c in range(nchunks) if not all(o in have for o in range(c*chunk, (c+1)*chunk))]
    if not missing:
        break
    todo = missing[:batch]
    print("attempt", attempt, "trying chunks", ["%02X" % c for c in todo], end=" ")
    rc = run(todo)
    parse_log()
    missing2 = [c for c in todo if not all(o in have for o in range(c*chunk, (c+1)*chunk))]
    print("-> ok:", len(todo) - len(missing2), "of", len(todo), "| total:", len(have), "/", length)

with open(OUTBIN, "wb") as f:
    f.write(bytes(buf))
print("captured:", len(have), "/", length, "->", OUTBIN)

def strings(data):
    out, cur = [], []
    for b in data:
        if 32 <= b < 127:
            cur.append(chr(b))
        else:
            if len(cur) >= 4:
                out.append("".join(cur))
            cur = []
    if len(cur) >= 4:
        out.append("".join(cur))
    return out

data = bytes(buf)
allstr = strings(data)
sig = ["INAV", "Cleanflight", "Betaflight", "Baseflight", "ArduPilot", "dRonin",
       "MultiWii", "KISS", "BLHeli", "LibrePilot", "PX4", "bootloader", "Bootloader",
       "version", "Version", "F1", "NAZE", "CLI", "SERIAL", "QUAD", "MSP", "uav",
       "stm32", "STM32", "USB", "GPS", "BARO", "MAG"]
seen = [s for s in allstr if any(k in s for k in sig)]
print("---signature strings---")
for s in seen[:100]:
    print("  " + s[:100])
