# ESP32 SWD Probe — Improvement Plan

Based on deep study of:
- **blackmagic_codeberg** (canonical Black Magic Debug): `src/target/cortexm.c`, `stm32f1.c`, `adiv5.c`, `adi.c`
- **cmsis_dap_tcp_esp32** (Espressif CMSIS-DAP over TCP): `main/SW_DP.c`, `main/DAP_config.h`
- **openocd_remote_swd_esp32** (Brian Kuschak): `remote_swd.c` — bit-banged SWD for OpenOCD `remote_swd` driver
- Current FC firmware: `esp32_swd_probe.ino` (SERIAL_ONLY mode, lane fix flashed, serial channel verified)

---

## 1. Cortex-M Debug Core Improvements (from blackmagic)

| Area | Current FC | blackmagic | Action |
|------|-----------|------------|--------|
| **Stepping with C_MASKINTS** | `DHCSR=0xA05F0005` (no mask) | Sets `C_MASKINTS` (bit 8) when stepping to prevent interrupt interference | Update `cortex_step()` and `cortex_resume_checked()` to use `0xA05F0105` (DBGKEY\|C_DEBUGEN\|C_HALT\|C_STEP\|C_MASKINTS) |
| **Halt reason / DFSR** | Only polls `DHCSR.S_HALT` | Reads `DFSR`, writes back to clear; classifies halt (BKPT, WATCHPOINT, HWBREAK, EXTERNAL) | Add `DFSR=0xE000ED30` read+clear in `cortex_poll_halted()`; report halt reason |
| **Breakpoint resume PC adjust** | No special handling | On resume, if PC at BKPT (`0xBE00..0xBEFF`), advance PC by 2 or 4 | Add PC read+adjust in `cortex_resume_checked()` before stepping |
| **Watchpoint count** | Hardcoded 4 (`DWT_FUNC0..3`) | Reads `DWT_CTRL >> 28 & 0xF` (`NUMCOMP`), truncates | Query `DWT_CTRL` at attach; use actual count |
| **Stale watchpoint/breakpoint clear at attach** | `cortex_cleanup()` on connect | Clears all FPB comparators + DWT function registers on probe attach | Call `bp_clear_all()` + `wp_clear_all()` in native `A` (alive) or first connect |
| **Semihosting** | Not supported | Detects `0xBEAB` BKPT, services semihosting calls | Optional: add semihosting handler in halt path |
| **Power-down request (CDBGPWRUPREQ)** | Not checked | Polls `DHCSR.S_REGRDY`/`CDBGPWRUPREQ` before access | Add power-up request check in `ap_init()` |

---

## 2. Memory Access / SWD Transport Improvements

| Area | Current FC | Reference | Action |
|------|-----------|-----------|--------|
| **TAR auto-increment rollover** | No handling | blackmagic: re-writes TAR when `(addr & 0x3FF) == 0` (1 KB boundary) | In `swd_read_block` / `swd_write_block`, detect 1 KB boundary and re-issue TAR |
| **CSW setup for sized access** | Lane fix uses `0x80000010 | s` | blackmagic `adi_ap_mem_access_setup`: base CSW `0x23000040` \| `SIZE_8/16/32/64` \| `ADDRINC_SINGLE` | Unify CSW base to `0x23000040` (APB access + DeviceEn + AddrIncSingle + SizeWord), OR in size per `MIN_ALIGN` |
| **MIN_ALIGN logic** | Custom `s` selection (0=8, 1=16, 2=32) | `MIN_ALIGN(addr, len)` picks largest alignment supported by address | Replace manual `s` logic with `MIN_ALIGN` macro; reuse for read & write |
| **Packing/unpacking** | Manual bit shifts in `mem_write` | `adiv5_pack_data` / `adiv5_unpack_data` for 8/16/32/64 | Centralize pack/unpack functions; reuse in read & write |
| **Dummy RDBUFF after write burst** | Done in `swd_write_block` (line 298) | blackmagic does same; needed to flush AP write buffer | Keep; verify it's always executed |
| **AP delay clocks after AP access** | `ap_delay=true` passes extra 8 clocks | openocd `ap_delay_clks` (default 10) | Parameterize; tune for reliability at high SWCLK |
| **SWD clock speed** | Bit-banged `digitalWrite` (slow ~100 kHz) | cmsis_dap: `IO_PORT_WRITE_CYCLES` + `DELAY_SLOW_CYCLES` tuned for 1.2 MHz+; uses GPIO matrix | Replace `digitalWrite` with fast GPIO register writes (`GPIO.out_w1ts`/`out_w1tc`); add configurable delay for target clock |

---

## 3. STM32F1 Flash Programming Improvements (from blackmagic stm32f1.c)

| Area | Current FC | blackmagic | Action |
|------|-----------|------------|--------|
| **Write alignment handling** | `flash_program_hw` calls `mem_write(addr, &hw, 2)` per halfword | `stm32f1_flash_write` sets `CR.PG` once, then `cortexm_mem_write_aligned` with `psize=ALIGN_16BIT` (or 32-bit for GD/AT) | Batch writes: set PG once, use aligned multi-word write via `swd_write_block` with CSW SIZE_HALFWORD, then wait BSY once per page |
| **Error status check** | `flash_wait_bsy` checks SR bits 0, 2, 4 | Same, plus clears `SR` error bits on failure | Keep; ensure `PGERR`/`WRPRTERR` cleared before retry |
| **Option bytes** | Not supported | Full option byte erase/write with `OPTER`/`OPTPG`/`OPTWRE` | Optional: add option byte commands |
| **Dual-bank / mass erase** | `flash_mass_erase` does both banks | Detects dual-bank via `part_id`; erases each bank separately with proper unlock | Add `stm32f1_is_dual_bank(part_id)` detection from `DBGMCU_IDCODE` |
| **RAM stub for fast flash** | Not used (direct register) | blackmagic does NOT use RAM stub for STM32F1 (direct is fine) | Current approach OK; just batch writes |

---

## 4. ESP32-Specific Speed & Reliability (from cmsis_dap_tcp_esp32 & openocd_remote_swd)

| Area | Current FC | Reference | Action |
|------|-----------|-----------|--------|
| **SWD IO implementation** | Arduino `digitalWrite`/`digitalRead` (~100 kHz) | cmsis_dap: `PIN_SWCLK_SET/CLR` macros → direct GPIO reg writes; `PIN_DELAY_SLOW(cycles)` | Rewrite `swd_exchange` / `swd_clock_half` using `GPIO.out_w1ts = (1<<PIN_SWCLK)` etc.; add `DELAY_SLOW_CYCLES` configurable constant |
| **GPIO matrix vs direct** | Direct GPIO | cmsis_dap uses GPIO matrix (allows any pin) | Keep direct for speed; document pin constraints |
| **SWCLK/SWDIO pin drive strength** | Default | ESP32 `gpio_set_drive_capability(pin, GPIO_DRIVE_CAP_3)` for fast edges | Add in `setup()` |
| **TCP keepalive / non-blocking** | Not used (SERIAL_ONLY) | openocd_remote: `SO_KEEPALIVE`, `TCP_KEEPIDLE=1`, `TCP_KEEPINTVL=1`, `TCP_KEEPCNT=5`; non-blocking sockets | When WiFi re-enabled, apply same socket options |
| **Response batching** | Per-command reply | openocd_remote: queues responses, sends in one `write()` | For TCP mode, batch responses |
| **Line reset / dormant sequences** | Partial (JTAG-to-SWD only) | Both refs have full `LINE_RESET`, `DORMANT_TO_SWD`, `SWD_TO_DORMANT`, `JTAG_TO_SWD`, `SWD_TO_JTAG` | Add all sequences; expose via native command |

---

## 5. Protocol & Native Command Additions

| Feature | Status | Reference | Action |
|---------|--------|-----------|--------|
| **DAP/AP scan (auto-discover)** | Hardcoded APSEL=0 | blackmagic `cortexm_probe` reads `AP_IDR`, walks ROM table | Add `cortexm_probe()` equivalent; auto-select MEM-AP |
| **Target ID / part detection** | None | blackmagic reads `CPUID`, `DBGMCU_IDCODE`, `TARGETID` (ADIv6) | Add `target_identify()`: read CPUID, DBGMCU_IDCODE, report part |
| **Halt reason in status (`T`)** | Returns raw DHCSR | blackmagic classifies: BKPT, WATCHPOINT, VECTRESET, etc. | Decode DFSR + DHCSR; return structured status |
| **Register cache / DCRDR optimization** | Reads DCRSR/DCRDR per reg | blackmagic caches `demcr`, uses `gdb_to_dcrsr` map | Minor; keep current for simplicity |
| **GDB `qAttached` / `D` detach** | Not implemented | blackmagic supports | Add detach packet handling |
| **SWO / RTT** | Not supported | blackmagic has `swo.c`, `uart_bridge.c` | Optional: add SWO UART bridge on spare GPIO |

---

## 6. Testing & Validation (from project test suite)

| Test | Current State | Target |
|------|---------------|--------|
| `diag_serial.py` lane test | **PASS** on hardware (COM10): `1122CDEF` confirmed, plus byte/halfword lane writes all landed correctly | Pass with STM32 connected |
| `test_stlink2.py --serial COM10` | **ALL PASS (32/32)**, including a *genuine* `F bp hit on resume` (`S_HALT` actually set, not a stale readback -- see resolved finding below) | All pass |
| `tests/tar_rollover_check.py` (new) | **PASS** -- 300-word read across the 1KB TAR boundary matches non-spanning reads | n/a |
| `tests/dfsr_check.py` (new) | **PASS** -- reports `BKPT` after a real breakpoint halt | Should show `BKPT` once FPB is fixed |
| `test_stability.py` | Not run | Pass 1000+ cycles |
| OpenOCD `remote_bitbang` | Not tested | Works with `target remote :5555` |
| OpenOCD `cmsis-dap-tcp` | Not tested | Works with cmsis_dap_tcp_esp32 firmware |
| GDB `target extended-remote :3333` | Basic reg read works | Full debug (break, step, watch, flash) |

### RESOLVED: FPB breakpoints didn't actually halt the core

Root cause, confirmed against blackmagic's `cortexm.h`
(`CORTEXM_FPB_CTRL_KEY = 1U << 1U`): `bp_set()` was writing `FP_CTRL` with
`0x80000000u` as the "KEY" bit. Bit 31 is FP_CTRL's read-only `REV` field --
that write was a no-op, so `ENABLE` likely never actually latched even
though every SPI-level write reported success. Fixed to `0x00000002u`
(bit 1). Also replaced `fpb_is_v1()`'s write-all-ones-and-guess heuristic
with a direct read of `FP_CTRL.REV` (bits 31:28), matching how blackmagic
does the same detection -- more reliable, and it's what exposed how far off
the KEY bit was in the first place. `fpb_comp_val()`'s V1 mask was also
tightened from `0xFFFFFFFCu` to `0x1FFFFFFCu` per spec (bit 29 is reserved,
SBZ) -- didn't affect the test address but was wrong per reference.

Separately, while confirming the fix, found and fixed a gap in the P0 DFSR
work: `cortex_resume()`'s "fire and forget" path never re-reads DFSR when
the core halts *asynchronously* later (a breakpoint firing during
free-run), so native command `'J'` was returning a stale cache from the
last explicit `'H'`/step. `'J'` now does a live `cortex_read_clear_dfsr()`
at query time.

Verified end-to-end on hardware (COM10): breakpoint halt (`S_HALT` set),
correct `DFSR.BKPT` reason, and the full `test_stlink2.py` suite, all
passing for real rather than by coincidence.

---

## 11. Second improvement pass: AP auto-probe, dual-bank flash, part ID

Three P1/P2 items implemented and verified on hardware (COM10), all
reference-checked against blackmagic (`adiv5.h`/`adiv5.c` for AP IDR
decoding, `stm32f1.c` for dual-bank layout) before touching working code,
same discipline as the FPB fix above.

- **DAP/AP auto-probe** (`ap_probe()`): scans APSEL 0-7, reads each AP's IDR
  (properly this time -- see the bug below), classifies via
  `IDR.CLASS == MEM-AP` (bits[16:13], value 8 -- verified against
  `adiv5.h`'s `ADIV5_AP_IDR_CLASS_MEM`), caches the first match for the rest
  of the boot session. `ap_init()`, and the manual `'D'`/`'W'` AP/DP register
  commands (which had their own separate, still-hardcoded-to-AP0 select
  logic -- a gap auto-probing would have left half-fixed otherwise), now all
  route through the discovered AP instead of assuming APSEL 0. Zero behavior
  change on this board, since it resolves to AP 0 anyway -- the value is on
  chips where the debug AP isn't at index 0.

  **Bug caught before it shipped**: the first version read IDR with a single
  `swd_read_reg()` call, copying the pattern from `ap_init()`'s existing
  warm-up read -- but that warm-up read never looks at its result, while
  probing obviously needs to. AP reads are posted (the first read primes it,
  a second DP RDBUFF read gets the actual value, same two-step the `'D'`
  command already does correctly). Missing that made every probed AP read
  back as 0, which the class check then correctly rejected as "not a MEM-AP"
  for every slot -- breaking `ap_init()` for everything (halt, step, memory
  access, flash all failed against real hardware). Caught immediately by
  running the full test suite before considering this done, fixed, then
  re-verified all 32/32 passing again.

- **Dual-bank flash + batched writes**: `flash_identify()` reads
  `DBGMCU_IDCODE`'s low 12 bits to pick page size (0x400/0x800) and detect
  XL-density (`0x430`) dual-bank parts, matching blackmagic's density table
  exactly. Bank1/bank2 FPEC registers (`FLASH_KEYR/SR/CR/AR`, bank2 at
  `+0x40`, split at `0x08080000`) are parameterized by a `bank_off` computed
  per-address, with erase/program/mass-erase all unlocking and touching only
  the bank(s) a given address range actually spans -- matching
  `stm32f1_mass_erase()`'s "erase bank1, then bank2 separately if dual-bank"
  behavior (there's no single register that erases both banks at once).
  `FLASH_CR.PG` is now set once per contiguous bank run instead of once per
  halfword (it stays latched until cleared; the repeated write was a wasted
  SWD transaction per halfword). This board is single-bank medium-density,
  so it exercises the "default" path of all of this -- the dual-bank routing
  itself is implemented and reference-checked, not hardware-verified, since
  no XL-density chip is available to test against.

- **Part identification** (new native command `'K'`, additive -- `'I'`'s
  reply is untouched): reads CPUID (`0xE000ED00`) plus the flash
  geometry/AP-index state above. Verified: `cpuid=0x412fc230` decodes to
  Implementer=ARM (`0x41`), PartNo=`0xC23` (Cortex-M3), matching OpenOCD's
  original "Cortex-M3 r2p0" identification of this exact chip.

New regression coverage: `tests/part_id_check.py`. Full `test_stlink2.py`
(32/32), `tar_rollover_check.py`, and `dfsr_check.py` all still pass.

---

## 7. Implementation Priority Order

| Priority | Item | Effort | Impact |
|----------|------|--------|--------|
| **P0** | C_MASKINTS in step/resume | 1h | **DONE** -- `cortex_step_write()`, used by `cortex_step()` and the sw-watchpoint loop in `cortex_resume_checked()`. Verified: `S step x3` and `V wp hit on write` pass on hardware (COM10). |
| **P0** | DFSR read+clear + halt reason | 2h | **DONE** -- `cortex_read_clear_dfsr()`, called from `cortex_halt()`/`cortex_step()`; exposed via new additive native cmd `'J'` (does not change `'T'`'s reply, so `diag_serial.py`/existing scripts are unaffected). Verified via `tests/dfsr_check.py` -- see finding below. |
| **P0** | TAR 1 KB rollover handling | 1h | **DONE** -- `swd_read_block()`/`swd_write_block()` re-issue TAR at each 0x400 boundary. Verified via `tests/tar_rollover_check.py`: a 300-word read spanning the boundary matches two non-spanning reads byte-for-byte. |
| **P0** | GPIO register SWD IO (speed) | 3h | **DONE** -- `gpio_fast_set/get()` (GPIO.out_w1ts/w1tc/in) replace `digitalWrite/digitalRead` in `swd_clock_half()`/`swd_exchange()`; added `GPIO_DRIVE_CAP_3` on SWCLK/SWDIO. Shipped with a conservative `swd_delay_cycles = 40` (tune down after confirming reliability -- see file comment). Verified: full `test_stlink2.py` suite runs correctly at this setting. |
| **P1** | Dynamic DWT NUMCOMP query | 1h | Correct watchpoint count |
| **P1** | Stale BP/WP clear on connect | 1h | Clean attach |
| **P1** | Batched flash write (set PG once) | 2h | 2× flash speed |
| **P1** | Dual-bank detect + mass erase | 1h | Large device support |
| **P2** | Unified CSW base (0x23000040) + MIN_ALIGN | 2h | Consistent sized access |
| **P2** | Pack/unpack helpers | 1h | Code reuse, fewer bugs |
| **P2** | DAP/AP auto-probe + part ID | 3h | Multi-target support |
| **P3** | Semihosting | 3h | Host I/O from target |
| **P3** | SWO / RTT bridge | 4h | printf debugging |
| **P3** | Full OpenOCD `remote_swd` / `cmsis-dap-tcp` compat | 4h | Standard tool support |

---

## 8. File Modification Map

| File | Functions to Modify |
|------|---------------------|
| `esp32_swd_probe.ino` | `cortex_step`, `cortex_resume_checked`, `cortex_poll_halted`, `cortex_halt`, `cortex_cleanup`, `ap_init`, `swd_read_block`, `swd_write_block`, `mem_write`, `mem_read`, `flash_program_hw`, `flash_program_region`, `flash_mass_erase`, `swd_exchange`, `swd_clock_half`, `setup` (GPIO drive), native cmd `T`, `G`, `C`, `S` |
| `diag_serial.py` | No change (test) |
| `test_stlink2.py` | No change (test) |
| New: `swd_gpio.h/c` | Fast GPIO SWD IO implementation |
| New: `cortexm_debug.h/c` | Halt reason, DFSR, DWT query, BP/WP management |
| New: `stm32f1_flash.h/c` | Batched flash, dual-bank, part detect |

---

## 9. Next Steps

1. **Immediate**: Connect STM32, run `diag_serial.py` → confirm lane fix (`1122CDEF`).
2. **P0 batch** (1–2 days): C_MASKINTS, DFSR, TAR rollover, fast GPIO SWD.
3. **Run full test suite** after P0; iterate on failures.
4. **P1 batch**: DWT NUMCOMP, stale clear, batched flash, dual-bank.
5. **Integration**: Test with OpenOCD `remote_bitbang` and `cmsis-dap-tcp`.
6. **Document** new native commands and GDB features.

---

## 10. Reference Code Locations

| Reference | Key Files |
|-----------|-----------|
| blackmagic | `src/target/cortexm.c` (halt/resume/step, breakwatch), `src/target/stm32f1.c` (flash), `src/target/adiv5.c` (mem access), `src/target/adi.c` (CSW setup) |
| cmsis_dap_tcp_esp32 | `main/SW_DP.c` (bit macros), `main/DAP_config.h` (timing), `main/DAP.c` (protocol) |
| openocd_remote_swd_esp32 | `remote_swd.c` (protocol, sequences, socket) |
| Current FC | `esp32_swd_probe.ino` (all functions listed above) |

---

*Plan generated from deep research of three reference projects + current firmware analysis. Ready for implementation.*