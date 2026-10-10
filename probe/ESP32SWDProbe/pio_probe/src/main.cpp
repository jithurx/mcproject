/*
 * ESP32-WROOM-32 as a full SWD/JTAG debug probe for STM32 / Cortex-M targets.
 *
 * - OpenOCD "remote_bitbang" compatible command set (port 5555)
 * - Native binary command set (port 5555): fast block read/write, CPU
 *   halt/resume/step, register access, FPB breakpoints, DWT watchpoints,
 *   STM32F1 flash erase/program/verify, reset, chip info.
 * - GDB Remote Serial Protocol server (port 3333) for on-chip debugging.
 *
 * Wiring to target (STM32 Blue Pill):
 *   SWDIO (GPIO21) -> PA13 (SWDIO/JTMS)
 *   SWCLK (GPIO22) -> PA14 (SWCLK/JTCK)
 *   NRST  (GPIO19) -> NRST   (optional, active-low reset)
 *   GND             -> GND
 *
 * Optional JTAG wires (if using "transport select jtag"):
 *   TMS  (GPIO23) -> PA13, SWCLK -> PA14, TDI (GPIO18) -> PA15,
 *   TDO  (GPIO32) -> PB3,  NRST (GPIO19) -> PB4
 */

#include <Arduino.h>
#include <WiFi.h>
#include <string.h>
#include "soc/gpio_struct.h"
#include "driver/gpio.h"

// Forward declarations (Arduino .ino auto-generates these; plain .cpp needs them)
static void handle_bitbang(char c, WiFiClient &client);
static void handle_gdb(WiFiClient &c);
static void handle_native(WiFiClient &c);

/* Serial-only mode: skip WiFi entirely so the probe runs standalone over
 * the USB/serial command channel (no WiFi AP needed, no esp-idf log noise
 * on the shared UART).  Comment out to re-enable WiFi TCP servers. */
#define SERIAL_ONLY 1

/* WiFi credentials are NOT committed to this repo. Fill these in locally
 * if you ever disable SERIAL_ONLY to use the TCP/GDB transports. */
static const char *WIFI_SSID     = "";
static const char *WIFI_PASSWORD = "";
static const uint16_t TCP_PORT = 5555;
static const uint16_t GDB_PORT = 3333;

static const uint8_t PIN_SWCLK = 22;
static const uint8_t PIN_SWDIO = 21;
static const uint8_t PIN_NRST  = 19;
static const uint8_t PIN_TDI   = 18;
static const uint8_t PIN_TMS   = 23;
static const uint8_t PIN_TDO   = 32;
static const uint8_t PIN_LED   = 2;

static bool swdio_output = false;
static bool blink_active = false;

/* When the serial command channel is used, debug prints would corrupt the
 * binary protocol on the shared UART, so they are suppressed. */
static bool serial_cmd_active = false;
#define DBG_PRINT(...)   do { if (!serial_cmd_active) Serial.print(__VA_ARGS__); } while (0)
#define DBG_PRINTLN(...) do { if (!serial_cmd_active) Serial.println(__VA_ARGS__); } while (0)

static uint32_t cur_select = 0xFFFFFFFF;
static uint32_t g_dpidr = 0;   /* last DPIDR seen (CKS32 clones differ from 0x1BA01477) */

/* Direct GPIO register access (bypasses digitalWrite/digitalRead's ~1us
 * function-call + lookup-table overhead). Both SWCLK(22) and SWDIO(21) are
 * pins 0-31, so the low GPIO bank (GPIO.out_w1ts/w1tc/in) is enough; the
 * >=32 path is kept only so this stays correct if pins are ever remapped. */
static inline void gpio_fast_set(uint8_t pin, bool high)
{
    if (pin < 32) {
        if (high) GPIO.out_w1ts = (1UL << pin);
        else      GPIO.out_w1tc = (1UL << pin);
    } else {
        if (high) GPIO.out1_w1ts.val = (1UL << (pin - 32));
        else      GPIO.out1_w1tc.val = (1UL << (pin - 32));
    }
}

static inline bool gpio_fast_get(uint8_t pin)
{
    if (pin < 32) return (GPIO.in >> pin) & 1;
    return (GPIO.in1.val >> (pin - 32)) & 1;
}

/* Tunable settle time between GPIO edges. digitalWrite's own overhead used
 * to act as an accidental rate limiter (~1us/call); direct register access
 * removes that, so an explicit delay keeps SWCLK edges clean over jumper
 * wires. Start conservative and reduce once diag_serial.py confirms a
 * reliable link at the current setting -- 0 means "as fast as the register
 * writes allow" (~1-2 MHz), which may be too fast for breadboard wiring. */
static uint32_t swd_delay_cycles = 250;

static inline void swd_delay(void)
{
    for (volatile uint32_t i = 0; i < swd_delay_cycles; i++) {
    }
}

static void swd_write(bool swclk, bool swdio)
{
    gpio_fast_set(PIN_SWCLK, swclk);
    if (swdio_output) {
        gpio_fast_set(PIN_SWDIO, swdio);
    }
}

/* ------------------------------------------------------------------ *
 * SWD transport (mirrors OpenOCD bitbang.c).  Error codes:
 *   1 OK, -2 parity, -3 FAULT, -4 WAIT retries exhausted
 * ------------------------------------------------------------------ */

#define SWD_START   (1 << 0)
#define SWD_APNDP   (1 << 1)
#define SWD_RNW     (1 << 2)
#define SWD_A32     (3 << 3)
#define SWD_PARITY  (1 << 5)
#define SWD_STOP    (0 << 6)
#define SWD_PARK    (1 << 7)

static void swd_clock_half(bool high)
{
    gpio_fast_set(PIN_SWCLK, high);
    swd_delay();
}

/* Clock bit_cnt bits.  rnw=false: drive SWDIO from 'bits' LSB-first.
 * rnw=true: sample SWDIO into *rdata LSB-first (pin must be input). */
static void swd_exchange(bool rnw, uint64_t bits, int bit_cnt, uint64_t *rdata)
{
    uint64_t v = rdata ? *rdata : 0;
    for (int i = 0; i < bit_cnt; i++) {
        if (!rnw)
            gpio_fast_set(PIN_SWDIO, (bits >> i) & 1);
        swd_clock_half(false);
        if (rnw && gpio_fast_get(PIN_SWDIO))
            v |= (1ULL << i);
        swd_clock_half(true);
    }
    if (rdata) *rdata = v;
}

/* Raw DP ABORT write (STKERRCLR|STKCMPCLR|WDERRCLR|ORUNERRCLR). */
static int swd_clear_sticky(void)
{
    for (int retry = 0; retry < 8; retry++) {
        uint8_t cmd = SWD_START | SWD_PARK;             /* DP write, reg 0x0 */
        pinMode(PIN_SWDIO, OUTPUT);
        swd_exchange(false, cmd, 8, NULL);
        pinMode(PIN_SWDIO, INPUT_PULLUP);
        uint64_t rd = 0;
        swd_exchange(true, 0, 1 + 3 + 1, &rd);
        int ack = (int)((rd >> 1) & 7);
        pinMode(PIN_SWDIO, OUTPUT);
        if (ack == 2 || ack == 7) {
            delayMicroseconds(20);
            continue;
        }
        if (ack != 1) return -3;
        swd_exchange(false, 0x1e, 33, NULL);            /* ABORT + parity(0) */
        digitalWrite(PIN_SWDIO, HIGH);
        return 1;
    }
    return -4;
}

static int swd_read_reg(bool ap, uint8_t reg, uint32_t *value, bool ap_delay)
{
    for (int retry = 0; retry < 1000; retry++) {
        uint8_t cmd = SWD_RNW | (ap ? SWD_APNDP : 0) | ((reg & 0xc) << 1);
        uint8_t p = 0;
        for (int i = 1; i <= 4; i++) p ^= (cmd >> i) & 1;
        if (p) cmd |= SWD_PARITY;
        cmd |= SWD_START | SWD_PARK;

        pinMode(PIN_SWDIO, OUTPUT);
        swd_exchange(false, cmd, 8, NULL);

        pinMode(PIN_SWDIO, INPUT_PULLUP);
        uint64_t rd = 0;
        swd_exchange(true, 0, 1 + 3 + 32 + 1 + 1, &rd);
        pinMode(PIN_SWDIO, OUTPUT);
        digitalWrite(PIN_SWDIO, HIGH);

        int ack = (int)((rd >> 1) & 7);
        if (ack == 2 || ack == 7) {
            if (ack == 2) swd_clear_sticky();
            swd_exchange(true, 0, 8, NULL);
            delayMicroseconds(20);
            continue;
        }
        if (ack != 1) return -3;
        uint32_t data = (uint32_t)((rd >> 4) & 0xffffffffu);
        int parity = (int)((rd >> 36) & 1);
        uint8_t dp = 0;
        for (int i = 0; i < 32; i++) dp ^= (uint8_t)((data >> i) & 1);
        if (dp != parity) return -2;
        if (value) *value = data;
        if (ap_delay) swd_exchange(false, 0, 8, NULL);
        return 1;
    }
    return -4;
}

static int swd_write_reg(bool ap, uint8_t reg, uint32_t wdata, bool ap_delay)
{
    for (int retry = 0; retry < 500; retry++) {
        uint8_t cmd = (ap ? SWD_APNDP : 0) | ((reg & 0xc) << 1);
        uint8_t p = 0;
        for (int i = 1; i <= 4; i++) p ^= (cmd >> i) & 1;
        if (p) cmd |= SWD_PARITY;
        cmd |= SWD_START | SWD_PARK;

        pinMode(PIN_SWDIO, OUTPUT);
        swd_exchange(false, cmd, 8, NULL);

        pinMode(PIN_SWDIO, INPUT_PULLUP);
        uint64_t rd = 0;
        swd_exchange(true, 0, 1 + 3 + 1, &rd);
        int ack = (int)((rd >> 1) & 7);
        pinMode(PIN_SWDIO, OUTPUT);
        if (ack == 2 || ack == 7) {
            if (ack == 2) swd_clear_sticky();
            delayMicroseconds(20);
            continue;
        }
        if (ack != 1) return -3;

        uint8_t dp = 0;
        for (int i = 0; i < 32; i++) dp ^= (uint8_t)((wdata >> i) & 1);
        uint64_t out = (uint64_t)wdata | ((uint64_t)dp << 32);
        swd_exchange(false, out, 33, NULL);
        digitalWrite(PIN_SWDIO, HIGH);
        if (ap_delay) swd_exchange(false, 0, 8, NULL);
        return 1;
    }
    return -4;
}

static int swd_init(void)
{
    cur_select = 0xFFFFFFFF;
    /* full JTAG-to-SWD sequence from OpenOCD swd_seq_jtag_to_swd */
    pinMode(PIN_SWDIO, OUTPUT);
    digitalWrite(PIN_SWDIO, HIGH);
    for (int i = 0; i < 7; i++) swd_exchange(false, 0xff, 8, NULL);
    swd_exchange(false, 0x9e, 8, NULL);
    swd_exchange(false, 0xe7, 8, NULL);
    for (int i = 0; i < 7; i++) swd_exchange(false, 0xff, 8, NULL);
    swd_exchange(false, 0x00, 8, NULL);
    digitalWrite(PIN_SWDIO, HIGH);

    uint32_t dpidr = 0;
    int st = swd_read_reg(false, 0x00, &dpidr, false);
    if (st != 1) return st;
    g_dpidr = dpidr;   /* accept any DPIDR (CKS32/other clones differ); 'I' reports it */
    (void)dpidr;

    /* Clear sticky errors latched in the target DP. */
    st = swd_write_reg(false, 0x00, 0x1e, false);
    if (st != 1) return st;

    /* Request debug + system power; wait for both PWRUPACKs. */
    st = swd_write_reg(false, 0x04, 0x50000000, false);
    if (st != 1) return st;

    uint32_t cs;
    for (int i = 0; i < 100; i++) {
        st = swd_read_reg(false, 0x04, &cs, false);
        if (st != 1) return st;
        if ((cs & 0x30000000) == 0x30000000) break;
        delayMicroseconds(10);
    }
    if ((cs & 0x30000000) != 0x30000000) return -6;
    cur_select = 0xFFFFFFFF;
    return 1;
}

/* ------------------------------------------------------------------ *
 * AP/MEM-AP layer: SELECT management + generic memory access.
 * ------------------------------------------------------------------ */

static int swd_select(uint32_t sel)
{
    if (sel == cur_select) return 1;
    int st = swd_write_reg(false, 0x08, sel, false);
    if (st != 1) return st;
    cur_select = sel;
    return 1;
}

/* AP IDR Class/Type fields (ADIv5). Verified against blackmagic's adiv5.h:
 * CLASS lives at bits[16:13] (8 == MEM-AP, 0 == JTAG-AP), TYPE at bits[3:0]
 * (1 == AHB2/AHB3 -- what our known-good AP0 on this board reports: IDR
 * 0x24770011 decodes to CLASS=8, TYPE=1, matching OpenOCD's own "MEM-AP
 * AHB3" for this exact chip). */
#define ADIV5_AP_IDR_CLASS_MASK  0x0001E000u
#define ADIV5_AP_IDR_CLASS_SHIFT 13u
#define ADIV5_AP_IDR_CLASS_MEM   8u

static uint32_t g_ap_sel = 0;
static bool     g_ap_found = false;

/* Find the first APSEL that answers with a MEM-AP, instead of assuming
 * APSEL 0 like the probe originally did. Cached for the whole boot session
 * (like fpb_is_v1()'s cache) -- ap_init() below runs on nearly every SWD
 * transaction, so re-scanning every time would be a serious slowdown for a
 * property that can't change without physically rewiring the target. */
static int ap_probe(void)
{
    if (g_ap_found) return 1;
    for (uint32_t apsel = 0; apsel <= 7; apsel++) {
        int st = swd_select(0x00000000u);
        if (st != 1) return st;
        st = swd_select((apsel << 24) | 0x000000F0u);
        if (st != 1) continue;
        uint32_t idr = 0;
        /* AP reads are posted: this primes the read, it does not return the
         * value -- ap_init()'s warm-up read gets away with a single read
         * because it never looks at the result, but this does, so it needs
         * the DP RDBUFF follow-up read too (same two-step the 'D' command
         * already uses). Missing this the first time around made every
         * probed AP read back as 0, which the class check then correctly
         * (if uselessly) rejected as "not a MEM-AP" for every single slot. */
        st = swd_read_reg(true, 0x0c, &idr, true);
        if (st == 1) st = swd_read_reg(false, 0x0c, &idr, false);
        swd_select(0x00000000u);
        if (st != 1 || idr == 0) continue;      /* absent/faulted AP index */
        uint32_t cls = (idr & ADIV5_AP_IDR_CLASS_MASK) >> ADIV5_AP_IDR_CLASS_SHIFT;
        if (cls == ADIV5_AP_IDR_CLASS_MEM) {
            g_ap_sel = apsel;
            g_ap_found = true;
            return 1;
        }
    }
    return -16;                                 /* no MEM-AP found on this DP */
}

/* Warm up the AHB-AP exactly like OpenOCD: SELECT bank F, read AP IDR,
 * then return to bank 0.  Required before the first AP write or the AP
 * answers with ack=7 while it is still waking. */
static int ap_init(void)
{
    int st = ap_probe();
    if (st != 1) return st;
    st = swd_select((g_ap_sel << 24) | 0x00000000u);
    if (st != 1) return st;
    st = swd_select((g_ap_sel << 24) | 0x000000F0u);
    if (st != 1) return st;
    uint32_t idr = 0;
    st = swd_read_reg(true, 0x0c, &idr, true);
    if (st != 1) return st;
    st = swd_select((g_ap_sel << 24) | 0x00000000u);
    if (st != 1) return st;
    return 1;
}

/* Fast 32-bit block read via TAR auto-increment. */
static int swd_read_block(uint32_t addr, uint32_t *buf, uint32_t nwords)
{
    int st = ap_init();
    if (st != 1) return st;
    st = swd_write_reg(true, 0x00, 0x23000052, true);   /* CSW word, autoinc */
    if (st != 1) return st;
    st = swd_write_reg(true, 0x04, addr, true);         /* TAR */
    if (st != 1) return st;
    uint32_t tmp;
    st = swd_read_reg(true, 0x0c, &tmp, true);          /* prime AP DRW */
    if (st != 1) return st;
    for (uint32_t i = 0; i < nwords; i++) {
        st = swd_read_reg(false, 0x0c, &buf[i], false); /* DP RDBUFF */
        if (st != 1) return st;
        if (i + 1 < nwords) {
            uint32_t next_addr = addr + (i + 1) * 4;
            /* AHB-AP TAR auto-increment wraps within the current 1 KB
             * window instead of carrying into the upper address bits, so
             * TAR must be re-issued at every 1 KB boundary. */
            if ((next_addr & 0x3FFu) == 0) {
                st = swd_write_reg(true, 0x04, next_addr, true);
                if (st != 1) return st;
            }
            st = swd_read_reg(true, 0x0c, &tmp, true);
            if (st != 1) return st;
        }
    }
    return 1;
}

/* Fast 32-bit block write via TAR auto-increment. */
static int swd_write_block(uint32_t addr, const uint8_t *data, uint32_t nwords)
{
    int st = ap_init();
    if (st != 1) return st;
    st = swd_write_reg(true, 0x00, 0x23000052, true);
    if (st != 1) return st;
    st = swd_write_reg(true, 0x04, addr, true);
    if (st != 1) return st;
    const uint32_t *w = (const uint32_t *)data;
    for (uint32_t i = 0; i < nwords; i++) {
        uint32_t cur_addr = addr + i * 4;
        /* Same 1 KB TAR auto-increment wrap as swd_read_block. */
        if (i > 0 && (cur_addr & 0x3FFu) == 0) {
            st = swd_write_reg(true, 0x04, cur_addr, true);
            if (st != 1) return st;
        }
        st = swd_write_reg(true, 0x0c, w[i], true);
        if (st != 1) return st;
    }
    uint32_t dummy;
    st = swd_read_reg(false, 0x0c, &dummy, false);      /* flush write buffer */
    return st;
}

static int mem_rd32(uint32_t addr, uint32_t *val)
{
    return swd_read_block(addr, val, 1);
}

static int mem_wr32(uint32_t addr, uint32_t val)
{
    return swd_write_block(addr, (const uint8_t *)&val, 1);
}

/* Read len bytes from addr, handling arbitrary alignment/size.  Uses only
 * whole-word AHB-AP reads (the probe's sub-word reads are unreliable at
 * byte offsets != 0) and slices the requested bytes out of the result. */
static int mem_read(uint32_t addr, uint8_t *out, uint32_t len)
{
    if (len == 0) return 1;
    uint32_t off = addr & 3;
    uint32_t nwords = (off + len + 3) >> 2;
    uint8_t *tmp = (uint8_t *)malloc(nwords * 4);
    if (!tmp) return -12;
    int st = swd_read_block(addr & ~3u, (uint32_t *)tmp, nwords);
    if (st == 1) memcpy(out, tmp + off, len);
    free(tmp);
    return st;
}

/* Write len bytes to addr, handling arbitrary alignment/size. */
static int mem_write(uint32_t addr, const uint8_t *data, uint32_t len)
{
    if (len == 0) return 1;
    if ((addr & 3) == 0 && (len & 3) == 0) {
        return swd_write_block(addr, data, len >> 2);
    }
    int st = ap_init();
    if (st != 1) return st;
    uint32_t rem = len, a = addr;
    const uint8_t *p = data;
    while (rem) {
        uint32_t s;
        if ((a & 3) == 0 && rem >= 4) s = 2;
        else if ((a & 1) == 0 && rem >= 2) s = 1;
        else s = 0;
        uint32_t bytes = 1u << s;
        uint32_t off = a & 3;
        uint32_t v = 0;
        for (uint32_t k = 0; k < bytes; k++) v |= ((uint32_t)p[k]) << (8 * (off + k));
        st = swd_write_reg(true, 0x00, 0x80000010 | s, true);   /* CSW size */
        if (st != 1) return st;
        st = swd_write_reg(true, 0x04, a, true);                /* TAR */
        if (st != 1) return st;
        st = swd_write_reg(true, 0x0c, v, true);                /* AP DRW */
        if (st != 1) return st;
        uint32_t dummy;
        st = swd_read_reg(false, 0x0c, &dummy, false);
        if (st != 1) return st;
        a += bytes; p += bytes; rem -= bytes;
    }
    return 1;
}

/* ------------------------------------------------------------------ *
 * Cortex-M core control (ARMv7-M / STM32F1).
 * ------------------------------------------------------------------ */

#define DHCSR   0xE000EDF0u
#define DCRSR   0xE000EDF4u
#define DCRDR   0xE000EDF8u
#define DEMCR   0xE000EDFCu
#define AIRCR   0xE000ED0Cu
#define DFSR    0xE000ED30u

#define DHCSR_S_HALT   (1u << 17)

/* Last halt reason (DFSR bits: HALTED|BKPT|DWTTRAP|VCATCH|EXTERNAL), cached
 * for the 'J' native command. */
static uint32_t g_last_dfsr = 0;

/* DFSR is sticky (write-1-to-clear): read it, then clear whatever was set,
 * so the next halt's reason isn't polluted by a prior one. */
static int cortex_read_clear_dfsr(void)
{
    uint32_t dfsr = 0;
    int st = mem_rd32(DFSR, &dfsr);
    if (st != 1) return st;
    g_last_dfsr = dfsr;
    if (dfsr) {
        st = mem_wr32(DFSR, dfsr);
        if (st != 1) return st;
    }
    return 1;
}

static int cortex_poll_halted(uint32_t timeout_ms, bool *halted)
{
    uint32_t t0 = millis();
    do {
        uint32_t dhcsr;
        int st = mem_rd32(DHCSR, &dhcsr);
        if (st != 1) return st;
        if (dhcsr & DHCSR_S_HALT) { *halted = true; return 1; }
        delay(1);
    } while (millis() - t0 < timeout_ms);
    *halted = false;
    return 1;
}

static int cortex_halt(void)
{
    int st = mem_wr32(DHCSR, 0xA05F0003);   /* DBGKEY|C_DEBUGEN|C_HALT */
    if (st != 1) return st;
    bool h = false;
    st = cortex_poll_halted(2000, &h);
    if (st != 1) return st;
    if (h) cortex_read_clear_dfsr();
    return h ? 1 : -7;
}

/* Single-step with interrupts masked. Per the ARMv7-M architecture, a write
 * that changes C_MASKINTS must not simultaneously change C_HALT/C_STEP, so
 * this takes two writes: first set C_MASKINTS while still halted, then clear
 * C_HALT and set C_STEP in a second write with C_MASKINTS held. Without this,
 * a pending interrupt can fire mid-step and land the core somewhere other
 * than the next instruction, which is what made stepping flaky. */
static int cortex_step_write(void)
{
    int st = mem_wr32(DHCSR, 0xA05F000B);   /* DBGKEY|C_DEBUGEN|C_HALT|C_MASKINTS */
    if (st != 1) return st;
    return mem_wr32(DHCSR, 0xA05F000D);     /* DBGKEY|C_DEBUGEN|C_MASKINTS|C_STEP */
}

static int cortex_resume(void)
{
    return mem_wr32(DHCSR, 0xA05F0001);     /* DBGKEY|C_DEBUGEN */
}

/* Software-watchpoint fallback.  The DWT on the earliest Cortex-M3 r1p0
 * cores (first STM32F103 silicon) is clocked but its comparators never
 * generate a debug halt, so hardware watchpoints cannot stop the CPU.
 * We record watchpoints here and, on resume, single-step the CPU comparing
 * the watched memory; when it changes we leave the core halted exactly as if
 * the watchpoint had fired.  On cores with a working DWT this is a harmless
 * (slower) duplicate of the hardware mechanism. */
#define SW_WP_MAX 8
typedef struct {
    uint32_t addr;
    uint8_t  kind;
    bool     active;
} sw_wp_t;
static sw_wp_t sw_wp[SW_WP_MAX];

static int sw_wp_add(uint32_t addr, uint8_t kind)
{
    for (int i = 0; i < SW_WP_MAX; i++) {
        if (!sw_wp[i].active) {
            sw_wp[i].addr = addr;
            sw_wp[i].kind = kind;
            sw_wp[i].active = true;
            return i;
        }
    }
    return -1;
}

static void sw_wp_remove(uint32_t addr)
{
    for (int i = 0; i < SW_WP_MAX; i++)
        if (sw_wp[i].active && sw_wp[i].addr == addr)
            sw_wp[i].active = false;
}

static void sw_wp_clear_all(void)
{
    for (int i = 0; i < SW_WP_MAX; i++) sw_wp[i].active = false;
}

static int cortex_read_reg(uint32_t sel, uint32_t *out);

/* Resume, honouring software watchpoints: step the core one instruction at
 * a time and halt as soon as any watched memory location changes. */
static int cortex_resume_checked(void)
{
    int n = 0;
    uint32_t addrs[SW_WP_MAX], old[SW_WP_MAX];
    uint8_t kinds[SW_WP_MAX];
    for (int i = 0; i < SW_WP_MAX; i++) {
        if (!sw_wp[i].active) continue;
        uint32_t v = 0;
        int st = mem_read(sw_wp[i].addr, (uint8_t *)&v, 4);
        if (st != 1) return st;
        addrs[n] = sw_wp[i].addr;
        kinds[n] = sw_wp[i].kind;
        old[n] = v;
        n++;
    }
    if (n == 0) return cortex_resume();

    DBG_PRINT("RC n=");
    DBG_PRINT(n);
    DBG_PRINT(" wp=");
    DBG_PRINT(addrs[0], HEX);
    DBG_PRINT(" old=");
    DBG_PRINTLN(old[0], HEX);

    const int max_steps = 3000;
    for (int i = 0; i < max_steps; i++) {
        int st = cortex_step_write();           /* single step, interrupts masked */
        if (st != 1) { DBG_PRINT("RC step-wr fail "); DBG_PRINTLN(st); return st; }
        bool h = false;
        st = cortex_poll_halted(100, &h);
        if (st != 1) { DBG_PRINT("RC poll fail "); DBG_PRINTLN(st); return st; }
        if (!h) { DBG_PRINTLN("RC not halted after step"); return -7; }
        if ((i % 200) == 0) {
            uint32_t pc = 0;
            cortex_read_reg(15, &pc);
            DBG_PRINT("RC step ");
            DBG_PRINT(i);
            DBG_PRINT(" pc=");
            DBG_PRINTLN(pc, HEX);
        }
        for (int k = 0; k < n; k++) {
            if (kinds[k] != 7 && kinds[k] != 4) continue;  /* write/any only */
            uint32_t v = 0;
            st = mem_read(addrs[k], (uint8_t *)&v, 4);
            if (st != 1) return st;
            if (v != old[k]) {
                DBG_PRINT("WP hit 0x");
                DBG_PRINT(addrs[k], HEX);
                DBG_PRINT(" old=");
                DBG_PRINT(old[k], HEX);
                DBG_PRINT(" new=");
                DBG_PRINTLN(v, HEX);
                return 1;                       /* leave core halted */
            }
        }
    }
    DBG_PRINTLN("WP step limit reached, resuming");
    return cortex_resume();
}

static int cortex_step(void)
{
    int st = cortex_step_write();
    if (st != 1) return st;
    bool h = false;
    st = cortex_poll_halted(2000, &h);
    if (st != 1) return st;
    if (h) cortex_read_clear_dfsr();
    return h ? 1 : -7;
}

static int cortex_read_reg(uint32_t sel, uint32_t *out)
{
    int st = mem_wr32(DCRSR, sel);          /* REGWnR = 0 (read) */
    if (st != 1) return st;
    uint32_t dummy;
    st = mem_rd32(DHCSR, &dummy);           /* barrier */
    if (st != 1) return st;
    return mem_rd32(DCRDR, out);
}

static int cortex_write_reg(uint32_t sel, uint32_t val)
{
    int st = mem_wr32(DCRDR, val);
    if (st != 1) return st;
    st = mem_wr32(DCRSR, sel | 0x00010000); /* REGWnR = 1 (write) */
    if (st != 1) return st;
    uint32_t dummy;
    return mem_rd32(DHCSR, &dummy);         /* barrier */
}

static int cortex_status(uint32_t *dhcsr)
{
    return mem_rd32(DHCSR, dhcsr);
}

static int cortex_reset_sysreq(void)
{
    return mem_wr32(AIRCR, 0x05FA0000u | 0x04u);   /* SYSRESETREQ */
}

/* ------------------------------------------------------------------ *
 * Breakpoints (FPB) and watchpoints (DWT).
 * ------------------------------------------------------------------ */

#define FPB_CTRL   0xE0002000u
#define FPB_COMP0  0xE0002008u
#define DWT_COMP0  0xE0001020u
#define DWT_FUNC0  0xE0001024u

static int debug_enable_trace(void)
{
    uint32_t demcr;
    int st = mem_rd32(DEMCR, &demcr);
    if (st != 1) return st;
    if (!(demcr & 0x01000000u)) {           /* TRCENA */
        st = mem_wr32(DEMCR, demcr | 0x01000000u);
        if (st != 1) return st;
    }
    return 1;
}

/* Detect FPB comparator layout via FP_CTRL.REV (bits 31:28): REV==0 is V1
 * (Cortex-M3/M4 -- COMP reserves bits 1 and 29, low address bit goes in
 * REPLACE[31:30]); any other value is V2 (full address in COMP[31:1]).
 * This is the architected field for exactly this purpose (see blackmagic
 * cortexm.c: priv->flash_patch_revision from CORTEXM_FPB_CTRL_REV_SHIFT) --
 * more reliable than probing behavior by writing all-ones and guessing from
 * which bits come back set. */
static int fpb_is_v1(void)
{
    static int v1 = -1;
    if (v1 >= 0) return v1;
    uint32_t ctrl;
    if (mem_rd32(FPB_CTRL, &ctrl) != 1) return -1;
    v1 = (((ctrl >> 28) & 0xFu) == 0) ? 1 : 0;
    return v1;
}

static uint32_t fpb_comp_val(int v1, uint32_t addr)
{
    if (v1) return (addr & 0x1FFFFFFCu) | ((addr & 2) ? 0x80000000u : 0x40000000u) | 1u;
    return (addr & 0x7FFFFFFEu) | 1u;
}

static int bp_set(uint32_t addr, int *slot)
{
    int v1 = fpb_is_v1();
    if (v1 < 0) return v1;
    if (v1 && (addr & 0x20000000u)) return -15;  /* V1 code region only */
    int st = debug_enable_trace();
    if (st != 1) return st;
    uint32_t ctrl;
    st = mem_rd32(FPB_CTRL, &ctrl);
    if (st != 1) return st;
    if (!(ctrl & 1)) {
        /* KEY is bit 1, not bit 31 -- confirmed against blackmagic
         * (CORTEXM_FPB_CTRL_KEY = 1U<<1U). The previous 0x80000000 landed on
         * FP_CTRL's read-only REV field, a no-op, so ENABLE may never have
         * actually latched: this was the likely reason breakpoints armed
         * cleanly (SPI-level writes all succeeded) but never halted the
         * core. */
        st = mem_wr32(FPB_CTRL, 0x00000002u | 1u);  /* KEY | ENABLE */
        if (st != 1) return st;
    }
    for (int i = 0; i < 6; i++) {
        uint32_t comp;
        st = mem_rd32(FPB_COMP0 + 4 * i, &comp);
        if (st != 1) return st;
        if (!(comp & 1)) {
            st = mem_wr32(FPB_COMP0 + 4 * i, fpb_comp_val(v1, addr));
            if (st != 1) return st;
            if (slot) *slot = i;
            return 1;
        }
    }
    return -8;                              /* no free comparator */
}

static int bp_clear(uint32_t addr)
{
    int v1 = fpb_is_v1();
    if (v1 < 0) return v1;
    for (int i = 0; i < 6; i++) {
        uint32_t comp;
        int st = mem_rd32(FPB_COMP0 + 4 * i, &comp);
        if (st != 1) return st;
        if (comp == fpb_comp_val(v1, addr)) {
            st = mem_wr32(FPB_COMP0 + 4 * i, 0);
            if (st != 1) return st;
            return 1;
        }
    }
    return 1;                               /* not found is OK */
}

static int bp_clear_all(void)
{
    for (int i = 0; i < 6; i++) {
        uint32_t comp;
        int st = mem_rd32(FPB_COMP0 + 4 * i, &comp);
        if (st != 1) return st;
        if (comp & 1) {
            st = mem_wr32(FPB_COMP0 + 4 * i, 0);
            if (st != 1) return st;
        }
    }
    return 1;
}

/* kind: 4 = any access, 6 = read, 7 = write (DWT_FUNCTION encodings). */
static int wp_set(uint32_t addr, uint32_t kind, int *slot)
{
    int st = debug_enable_trace();
    if (st != 1) return st;
    int dwt_slot = -1;
    for (int i = 0; i < 4; i++) {
        uint32_t fn;
        st = mem_rd32(DWT_FUNC0 + 16 * i, &fn);
        if (st != 1) return st;
        if ((fn & 0x1F) == 0) {
            st = mem_wr32(DWT_COMP0 + 16 * i, addr);
            if (st != 1) return st;
            st = mem_wr32(DWT_FUNC0 + 16 * i, kind & 0x1F);
            if (st != 1) return st;
            dwt_slot = i;
            break;
        }
    }
    int sw = sw_wp_add(addr, kind);
    if (dwt_slot < 0 && sw < 0) return -9;  /* no hardware or software slot */
    if (slot) *slot = (dwt_slot >= 0) ? dwt_slot : sw;
    return 1;
}

static int wp_clear(uint32_t addr)
{
    sw_wp_remove(addr);
    for (int i = 0; i < 4; i++) {
        uint32_t comp;
        int st = mem_rd32(DWT_COMP0 + 16 * i, &comp);
        if (st != 1) return st;
        if (comp == addr) {
            st = mem_wr32(DWT_FUNC0 + 16 * i, 0);
            if (st != 1) return st;
            return 1;
        }
    }
    return 1;
}

static int wp_clear_all(void)
{
    sw_wp_clear_all();
    for (int i = 0; i < 4; i++) {
        uint32_t fn;
        int st = mem_rd32(DWT_FUNC0 + 16 * i, &fn);
        if (st != 1) return st;
        if (fn & 0x1F) {
            st = mem_wr32(DWT_FUNC0 + 16 * i, 0);
            if (st != 1) return st;
        }
    }
    return 1;
}

/* Clear any leftover breakpoints/watchpoints from a previous session so a
 * new client starts with a clean debug state.  Best-effort: failures are
 * ignored so a connection never breaks on cleanup. */
static void cortex_cleanup(void)
{
    if (swd_init() != 1) return;
    /* SYSRESETREQ resets the STM32 core but not its always-on debug block,
     * so a C_HALT/C_STEP/C_MASKINTS left over from a previous session (or a
     * client that disconnected mid-step) survives target resets and even
     * ESP32 reboots. Releasing debug entirely puts DHCSR back to its true
     * power-on state so a freshly (re)connected client starts clean. */
    mem_wr32(DHCSR, 0xA05F0000);
    bp_clear_all();
    wp_clear_all();
}

/* ------------------------------------------------------------------ *
 * STM32F1 flash programming. Bank1-only register addresses, page size, and
 * dual-bank support all verified against blackmagic's stm32f1.c rather than
 * assumed -- bank1 layout matched what was already here byte-for-byte (a
 * good sign the original constants were already correct), which is the
 * useful part: it means the dual-bank/page-size additions below extend a
 * correct base instead of guessing on top of an unverified one.
 * ------------------------------------------------------------------ */

#define FLASH_BASE       0x40022000u
#define FLASH_BANK2_OFF  0x40u              /* XL-density only */
#define FLASH_BANK_SPLIT 0x08080000u
#define FLASH_KEYR(off)  (FLASH_BASE + 0x04u + (off))
#define FLASH_SR(off)    (FLASH_BASE + 0x0Cu + (off))
#define FLASH_CR(off)    (FLASH_BASE + 0x10u + (off))
#define FLASH_AR(off)    (FLASH_BASE + 0x14u + (off))

#define DBGMCU_IDCODE    0xE0042000u

/* Device density -> page size / dual-bank, from DBGMCU_IDCODE's low 12 bits
 * (confirmed against blackmagic's stm32f1_probe(): 0x410/0x412 medium/low
 * density = 1KB pages single bank; 0x414/0x418 high density/connectivity
 * line = 2KB pages single bank; 0x430 XL-density = 2KB pages, dual bank).
 * This board's own idcode (0x20030410, from the native 'I' command) is
 * medium density -- exactly the default this falls back to, so behavior on
 * tested hardware is unchanged either way. */
static uint32_t g_flash_page_size = 0x400u;
static bool     g_flash_dual_bank = false;
static bool     g_flash_ident_done = false;

static void flash_identify(void)
{
    if (g_flash_ident_done) return;
    uint32_t idcode = 0;
    if (mem_rd32(DBGMCU_IDCODE, &idcode) != 1) return;  /* leave defaults */
    switch (idcode & 0xFFFu) {
    case 0x412u: case 0x410u:                 /* low / medium density */
        g_flash_page_size = 0x400u; g_flash_dual_bank = false; break;
    case 0x414u: case 0x418u:                 /* high density / connectivity line */
        g_flash_page_size = 0x800u; g_flash_dual_bank = false; break;
    case 0x430u:                               /* XL-density: dual bank */
        g_flash_page_size = 0x800u; g_flash_dual_bank = true; break;
    default:
        g_flash_page_size = 0x400u; g_flash_dual_bank = false; break;
    }
    g_flash_ident_done = true;
}

static uint32_t flash_bank_offset(uint32_t addr)
{
    return (g_flash_dual_bank && addr >= FLASH_BANK_SPLIT) ? FLASH_BANK2_OFF : 0u;
}

static int flash_wait_bsy(uint32_t bank_off, uint32_t timeout_ms)
{
    uint32_t t0 = millis();
    do {
        uint32_t sr;
        int st = mem_rd32(FLASH_SR(bank_off), &sr);
        if (st != 1) return st;
        if (!(sr & 1)) {
            if (sr & 0x0014) return -10;    /* PGERR | WRPRTERR */
            return 1;
        }
        delay(1);
    } while (millis() - t0 < timeout_ms);
    return -11;
}

static int flash_unlock(uint32_t bank_off)
{
    int st = mem_wr32(FLASH_KEYR(bank_off), 0x45670123);
    if (st != 1) return st;
    return mem_wr32(FLASH_KEYR(bank_off), 0xCDEF89AB);
}

static int flash_lock(uint32_t bank_off)
{
    return mem_wr32(FLASH_CR(bank_off), 0x00000080);  /* LOCK */
}

static int flash_page_erase(uint32_t addr)
{
    uint32_t bank_off = flash_bank_offset(addr);
    int st = mem_wr32(FLASH_CR(bank_off), 0x00000002);    /* PER */
    if (st != 1) return st;
    st = mem_wr32(FLASH_AR(bank_off), addr);
    if (st != 1) return st;
    st = mem_wr32(FLASH_CR(bank_off), 0x00000042);        /* PER | STRT */
    if (st != 1) return st;
    return flash_wait_bsy(bank_off, 2000);
}

static int flash_mass_erase_bank(uint32_t bank_off)
{
    int st = mem_wr32(FLASH_CR(bank_off), 0x00000004);    /* MER */
    if (st != 1) return st;
    st = mem_wr32(FLASH_CR(bank_off), 0x00000044);        /* MER | STRT */
    if (st != 1) return st;
    return flash_wait_bsy(bank_off, 2000);
}

/* Dual-bank mass erase does each bank separately with its own unlock --
 * confirmed against blackmagic's stm32f1_mass_erase(), which has no single
 * register/bit that erases both banks at once. */
static int flash_mass_erase(void)
{
    int st = flash_unlock(0u);
    if (st != 1) return st;
    st = flash_mass_erase_bank(0u);
    flash_lock(0u);
    if (st != 1 || !g_flash_dual_bank) return st;

    st = flash_unlock(FLASH_BANK2_OFF);
    if (st != 1) return st;
    st = flash_mass_erase_bank(FLASH_BANK2_OFF);
    flash_lock(FLASH_BANK2_OFF);
    return st;
}

static int flash_erase_region(uint32_t addr, uint32_t len)
{
    flash_identify();

    if (addr == 0 && len == 0)
        return flash_mass_erase();

    uint32_t end = addr + len;
    bool need1 = !g_flash_dual_bank || addr < FLASH_BANK_SPLIT;
    bool need2 = g_flash_dual_bank && end > FLASH_BANK_SPLIT;

    int st = 1;
    if (need1) st = flash_unlock(0u);
    if (st == 1 && need2) st = flash_unlock(FLASH_BANK2_OFF);
    if (st == 1) {
        for (uint32_t a = addr; a < end; a += g_flash_page_size) {
            st = flash_page_erase(a);
            if (st != 1) break;
        }
    }
    if (need2) flash_lock(FLASH_BANK2_OFF);
    if (need1) flash_lock(0u);
    return st;
}

/* PG only needs to be set once per contiguous run on a bank, not once per
 * halfword -- it stays latched until explicitly cleared, and re-writing it
 * on every single write was a full extra SWD register-write transaction per
 * halfword for no reason (confirmed against blackmagic's stm32f1_flash_write:
 * PG is set once before the aligned write loop, not per write). On this
 * board (single bank) that means PG is set exactly once for an entire
 * program call instead of once per halfword. */
static int flash_program_region(uint32_t addr, const uint8_t *data, uint32_t len)
{
    flash_identify();

    uint32_t end = addr + len;
    bool need1 = !g_flash_dual_bank || addr < FLASH_BANK_SPLIT;
    bool need2 = g_flash_dual_bank && end > FLASH_BANK_SPLIT;

    int st = 1;
    if (need1) st = flash_unlock(0u);
    if (st == 1 && need2) st = flash_unlock(FLASH_BANK2_OFF);

    if (st == 1) {
        uint32_t last_bank_off = 0xFFFFFFFFu;   /* forces PG on first iter */
        for (uint32_t off = 0; off + 1 < len; off += 2) {
            uint32_t a = addr + off;
            uint32_t bank_off = flash_bank_offset(a);
            if (bank_off != last_bank_off) {
                st = mem_wr32(FLASH_CR(bank_off), 0x00000001);  /* PG */
                if (st != 1) break;
                last_bank_off = bank_off;
            }
            uint16_t hw = (uint16_t)(data[off] | ((uint16_t)data[off + 1] << 8));
            st = mem_write(a, (const uint8_t *)&hw, 2);
            if (st != 1) break;
            st = flash_wait_bsy(bank_off, 500);
            if (st != 1) break;
        }
    }
    if (need2) flash_lock(FLASH_BANK2_OFF);
    if (need1) flash_lock(0u);
    return st;
}

static int flash_verify_region(uint32_t addr, const uint8_t *data, uint32_t len)
{
    uint8_t *buf = (uint8_t *)malloc(len);
    if (!buf) return -12;
    int st = mem_read(addr, buf, len);
    if (st == 1 && memcmp(buf, data, len) != 0) st = -13;
    free(buf);
    return st;
}

/* Program a region: halt, erase covering pages, program, verify. */
static int flash_program_flash(uint32_t addr, const uint8_t *data, uint32_t len)
{
    int st = cortex_halt();
    if (st != 1) return st;
    st = flash_erase_region(addr, len);
    if (st != 1) return st;
    st = flash_program_region(addr, data, len);
    if (st != 1) return st;
    return flash_verify_region(addr, data, len);
}

/* ------------------------------------------------------------------ *
 * Native binary protocol (port 5555).  Reply is <status:u8> <detail:u8>
 * followed by optional payload.  status 0 = OK.
 * ------------------------------------------------------------------ */

static bool recv_bytes(Stream &c, uint8_t *buf, int n, unsigned long timeout_ms)
{
    unsigned long t0 = millis();
    int got = 0;
    while (got < n) {
        if (c.available()) {
            buf[got++] = (uint8_t)c.read();
        } else if (millis() - t0 > timeout_ms) {
            return false;
        } else {
            delay(1);
        }
    }
    return got == n;
}

static void reply_st(Stream &c, int st)
{
    if (st == 1) {
        c.write((uint8_t)0);
        c.write((uint8_t)0);
    } else {
        c.write((uint8_t)1);
        c.write((uint8_t)(st & 0xFF));
    }
}

#define RUN_OP(op) do {                                                       \
    int __st = swd_init();                                                    \
    if (__st == 1) __st = (op);                                               \
    if (__st != 1) {                                                          \
        __st = swd_init();                                                    \
        if (__st == 1) __st = (op);                                           \
    }                                                                         \
    reply_st(c, __st);                                                        \
} while (0)

static void handle_native_cmd(char cmd, Stream &c)
{
    switch (cmd) {

    case 'A':                                   /* alive ping */
        reply_st(c, 1);
        break;

    case 'X': {                                 /* block read */
        uint8_t hdr[6];
        if (!recv_bytes(c, hdr, 6, 5000)) { reply_st(c, -14); break; }
        uint32_t addr = (uint32_t)hdr[0] | ((uint32_t)hdr[1] << 8) |
                        ((uint32_t)hdr[2] << 16) | ((uint32_t)hdr[3] << 24);
        uint32_t nwords = (uint32_t)hdr[4] | ((uint32_t)hdr[5] << 8);
        if (nwords == 0) nwords = 1;
        if (nwords > 1024) nwords = 1024;
        uint32_t *buf = (uint32_t *)malloc(nwords * 4);
        if (!buf) { reply_st(c, -12); break; }
        int st = swd_init();
        if (st == 1) st = mem_read(addr, (uint8_t *)buf, nwords * 4);
        if (st != 1) {
            st = swd_init();
            if (st == 1) st = mem_read(addr, (uint8_t *)buf, nwords * 4);
        }
        if (st == 1) {
            c.write((uint8_t)0);
            c.write((uint8_t)0);
            c.write((const uint8_t *)buf, (size_t)(nwords * 4));
        } else {
            reply_st(c, st);
        }
        free(buf);
        break;
    }

    case 'M': {                                 /* memory write */
        uint8_t hdr[8];
        if (!recv_bytes(c, hdr, 8, 5000)) { reply_st(c, -14); break; }
        uint32_t addr = (uint32_t)hdr[0] | ((uint32_t)hdr[1] << 8) |
                        ((uint32_t)hdr[2] << 16) | ((uint32_t)hdr[3] << 24);
        uint32_t len = (uint32_t)hdr[4] | ((uint32_t)hdr[5] << 8) |
                       ((uint32_t)hdr[6] << 16) | ((uint32_t)hdr[7] << 24);
        if (len > 4096) { reply_st(c, -14); break; }
        uint8_t *buf = (uint8_t *)malloc(len ? len : 1);
        if (!buf) { reply_st(c, -12); break; }
        if (!recv_bytes(c, buf, len, 30000)) {
            free(buf); reply_st(c, -14); break;
        }
        RUN_OP(mem_write(addr, buf, len));
        free(buf);
        break;
    }

    case 'H':                                   /* halt */
        RUN_OP(cortex_halt());
        break;

    case 'C':                                   /* resume */
        RUN_OP(cortex_resume_checked());
        break;

    case 'S':                                   /* step */
        RUN_OP(cortex_step());
        break;

    case 'U':                                   /* system reset */
        RUN_OP(cortex_reset_sysreq());
        break;

    case 'T': {                                 /* status */
        int st = swd_init();
        if (st != 1) { reply_st(c, st); break; }
        uint32_t dhcsr = 0;
        st = mem_rd32(DHCSR, &dhcsr);
        if (st != 1) {
            st = swd_init();
            if (st == 1) st = mem_rd32(DHCSR, &dhcsr);
        }
        if (st == 1) {
            c.write((uint8_t)0);
            c.write((uint8_t)0);
            for (int i = 0; i < 4; i++) c.write((uint8_t)(dhcsr >> (8 * i)));
        } else {
            reply_st(c, st);
        }
        break;
    }

    case 'G': {                                 /* read core regs r0-r15+xpsr */
        uint32_t regs[17];
        int st = swd_init();
        if (st == 1) {
            for (int i = 0; i < 16; i++) {
                st = cortex_read_reg(i, &regs[i]);
                if (st != 1) break;
            }
            if (st == 1) st = cortex_read_reg(16, &regs[16]);
        }
        if (st != 1) {
            st = swd_init();
            for (int i = 0; i < 16 && st == 1; i++)
                st = cortex_read_reg(i, &regs[i]);
            if (st == 1) st = cortex_read_reg(16, &regs[16]);
        }
        if (st == 1) {
            c.write((uint8_t)0);
            c.write((uint8_t)0);
            for (int i = 0; i < 17; i++) {
                for (int k = 0; k < 4; k++)
                    c.write((uint8_t)(regs[i] >> (8 * k)));
            }
        } else {
            reply_st(c, st);
        }
        break;
    }

    case 'N': {                                 /* write core reg */
        uint8_t hdr[5];
        if (!recv_bytes(c, hdr, 5, 5000)) { reply_st(c, -14); break; }
        uint32_t sel = hdr[0];
        uint32_t val = (uint32_t)hdr[1] | ((uint32_t)hdr[2] << 8) |
                       ((uint32_t)hdr[3] << 16) | ((uint32_t)hdr[4] << 24);
        RUN_OP(cortex_write_reg(sel, val));
        break;
    }

    case 'F': {                                 /* FPB breakpoint */
        uint8_t hdr[5];
        if (!recv_bytes(c, hdr, 5, 5000)) { reply_st(c, -14); break; }
        uint8_t op = hdr[0];
        uint32_t addr = (uint32_t)hdr[1] | ((uint32_t)hdr[2] << 8) |
                        ((uint32_t)hdr[3] << 16) | ((uint32_t)hdr[4] << 24);
        int slot = -1;
        int st = swd_init();
        if (st == 1) {
            if (op == 1) st = bp_set(addr, &slot);
            else if (op == 0) st = bp_clear(addr);
            else st = bp_clear_all();
        }
        if (st != 1) {
            st = swd_init();
            if (st == 1) {
                if (op == 1) st = bp_set(addr, &slot);
                else if (op == 0) st = bp_clear(addr);
                else st = bp_clear_all();
            }
        }
        if (st == 1) {
            c.write((uint8_t)0);
            c.write((uint8_t)0);
            c.write((uint8_t)(slot & 0xFF));
        } else {
            reply_st(c, st);
        }
        break;
    }

    case 'V': {                                 /* DWT watchpoint */
        uint8_t hdr[6];
        if (!recv_bytes(c, hdr, 6, 5000)) { reply_st(c, -14); break; }
        uint8_t op = hdr[0];
        uint32_t addr = (uint32_t)hdr[1] | ((uint32_t)hdr[2] << 8) |
                        ((uint32_t)hdr[3] << 16) | ((uint32_t)hdr[4] << 24);
        uint8_t kind = hdr[5];
        int slot = -1;
        int st = swd_init();
        if (st == 1) {
            if (op == 1) st = wp_set(addr, kind, &slot);
            else if (op == 0) st = wp_clear(addr);
            else st = wp_clear_all();
        }
        if (st != 1) {
            st = swd_init();
            if (st == 1) {
                if (op == 1) st = wp_set(addr, kind, &slot);
                else if (op == 0) st = wp_clear(addr);
                else st = wp_clear_all();
            }
        }
        if (st == 1) {
            c.write((uint8_t)0);
            c.write((uint8_t)0);
            c.write((uint8_t)(slot & 0xFF));
        } else {
            reply_st(c, st);
        }
        break;
    }

    case 'Y': {                                 /* flash erase */
        uint8_t hdr[8];
        if (!recv_bytes(c, hdr, 8, 5000)) { reply_st(c, -14); break; }
        uint32_t addr = (uint32_t)hdr[0] | ((uint32_t)hdr[1] << 8) |
                        ((uint32_t)hdr[2] << 16) | ((uint32_t)hdr[3] << 24);
        uint32_t len = (uint32_t)hdr[4] | ((uint32_t)hdr[5] << 8) |
                       ((uint32_t)hdr[6] << 16) | ((uint32_t)hdr[7] << 24);
        int st = swd_init();
        if (st == 1) {
            st = cortex_halt();
            if (st == 1) st = flash_erase_region(addr, len);
        }
        if (st != 1) {
            st = swd_init();
            if (st == 1) {
                st = cortex_halt();
                if (st == 1) st = flash_erase_region(addr, len);
            }
        }
        reply_st(c, st);
        break;
    }

    case 'P': {                                 /* flash program + verify */
        uint8_t hdr[8];
        if (!recv_bytes(c, hdr, 8, 5000)) { reply_st(c, -14); break; }
        uint32_t addr = (uint32_t)hdr[0] | ((uint32_t)hdr[1] << 8) |
                        ((uint32_t)hdr[2] << 16) | ((uint32_t)hdr[3] << 24);
        uint32_t len = (uint32_t)hdr[4] | ((uint32_t)hdr[5] << 8) |
                       ((uint32_t)hdr[6] << 16) | ((uint32_t)hdr[7] << 24);
        if (len > 4096 || (len & 1) || (addr & 1)) { reply_st(c, -14); break; }
        uint8_t *buf = (uint8_t *)malloc(len ? len : 2);
        if (!buf) { reply_st(c, -12); break; }
        if (!recv_bytes(c, buf, len, 30000)) {
            free(buf); reply_st(c, -14); break;
        }
        int st = swd_init();
        if (st == 1) st = flash_program_flash(addr, buf, len);
        if (st != 1) {
            st = swd_init();
            if (st == 1) st = flash_program_flash(addr, buf, len);
        }
        reply_st(c, st);
        free(buf);
        break;
    }

    case 'D': {                                 /* AP/DP register read */
        uint8_t hdr[3];
        if (!recv_bytes(c, hdr, 3, 5000)) { reply_st(c, -14); break; }
        uint8_t is_ap = hdr[0], bank = hdr[1], reg = hdr[2];
        uint32_t val = 0;
        int st = swd_init();
        if (st == 1 && is_ap) st = ap_probe();
        if (st == 1) {
            /* bank<<4 used to implicitly assume APSEL 0 (the AP index byte
             * lived in bits[31:24], never touched here) -- now ORs in the
             * auto-probed AP instead of hardcoding it. Identical behavior
             * on this board, since g_ap_sel resolves to 0 here anyway. */
            if (is_ap) st = swd_select((g_ap_sel << 24) | ((uint32_t)bank << 4));
            if (st == 1) {
                if (is_ap) {
                    /* AP reads are posted: prime AP read, then get data
                     * from DP RDBUFF. */
                    st = swd_read_reg(true, reg & 0xc, &val, true);
                    if (st == 1) st = swd_read_reg(false, 0x0c, &val, false);
                } else {
                    st = swd_read_reg(false, reg & 0xc, &val, false);
                }
            }
        }
        if (st != 1) {
            st = swd_init();
            if (st == 1 && is_ap) st = ap_probe();
            if (st == 1) {
                if (is_ap) st = swd_select((g_ap_sel << 24) | ((uint32_t)bank << 4));
                if (st == 1) {
                    if (is_ap) {
                        st = swd_read_reg(true, reg & 0xc, &val, true);
                        if (st == 1) st = swd_read_reg(false, 0x0c, &val, false);
                    } else {
                        st = swd_read_reg(false, reg & 0xc, &val, false);
                    }
                }
            }
        }
        if (st == 1) {
            c.write((uint8_t)0);
            c.write((uint8_t)0);
            for (int i = 0; i < 4; i++) c.write((uint8_t)(val >> (8 * i)));
        } else {
            reply_st(c, st);
        }
        break;
    }

    case 'W': {                                 /* AP/DP register write */
        uint8_t hdr[7];
        if (!recv_bytes(c, hdr, 7, 5000)) { reply_st(c, -14); break; }
        uint8_t is_ap = hdr[0], bank = hdr[1], reg = hdr[2];
        uint32_t val = (uint32_t)hdr[3] | ((uint32_t)hdr[4] << 8) |
                       ((uint32_t)hdr[5] << 16) | ((uint32_t)hdr[6] << 24);
        int st = swd_init();
        if (st == 1 && is_ap) st = ap_probe();
        if (st == 1) {
            if (is_ap) st = swd_select((g_ap_sel << 24) | ((uint32_t)bank << 4));
            if (st == 1) {
                if (is_ap) st = swd_write_reg(true, reg & 0xc, val, true);
                else st = swd_write_reg(false, reg & 0xc, val, false);
            }
        }
        if (st != 1) {
            st = swd_init();
            if (st == 1 && is_ap) st = ap_probe();
            if (st == 1) {
                if (is_ap) st = swd_select((g_ap_sel << 24) | ((uint32_t)bank << 4));
                if (st == 1) {
                    if (is_ap) st = swd_write_reg(true, reg & 0xc, val, true);
                    else st = swd_write_reg(false, reg & 0xc, val, false);
                }
            }
        }
        reply_st(c, st);
        break;
    }

    case 'J': {                                  /* halt reason: live DFSR read+clear */
        /* cortex_halt()/cortex_step() cache DFSR right after they cause a
         * halt, but a breakpoint or watchpoint can also halt the core
         * asynchronously while it's free-running after 'C' -- read live
         * here rather than trusting the cache, or an async halt's real
         * reason is invisible until the next explicit halt/step. */
        int st = cortex_read_clear_dfsr();
        if (st == 1) {
            c.write((uint8_t)0);
            c.write((uint8_t)0);
            for (int i = 0; i < 4; i++) c.write((uint8_t)(g_last_dfsr >> (8 * i)));
        } else {
            reply_st(c, st);
        }
        break;
    }

    case 'K': {                                  /* extended part info: CPUID, flash geometry, AP used */
        uint32_t cpuid = 0;
        int st = swd_init();
        if (st == 1) st = ap_probe();
        if (st == 1) st = mem_rd32(0xE000ED00u, &cpuid);       /* CPUID */
        if (st == 1) flash_identify();
        if (st == 1) {
            c.write((uint8_t)0);
            c.write((uint8_t)0);
            uint32_t v[2] = { cpuid, g_flash_page_size };
            for (int i = 0; i < 2; i++)
                for (int k = 0; k < 4; k++)
                    c.write((uint8_t)(v[i] >> (8 * k)));
            c.write((uint8_t)(g_flash_dual_bank ? 1 : 0));
            c.write((uint8_t)g_ap_sel);
        } else {
            reply_st(c, st);
        }
        break;
    }

    case 'I': {                                 /* chip info */
        uint32_t dpidr = 0, fsize = 0, idcode = 0;
        int st = swd_init();
        if (st == 1) st = swd_read_reg(false, 0x00, &dpidr, false);
        if (st == 1) st = mem_rd32(0x1FFFF7E0u, &fsize);
        if (st == 1) st = mem_rd32(0xE0042000u, &idcode);
        if (st == 1) {
            c.write((uint8_t)0);
            c.write((uint8_t)0);
            uint32_t v[3] = { dpidr, fsize, idcode };
            for (int i = 0; i < 3; i++)
                for (int k = 0; k < 4; k++)
                    c.write((uint8_t)(v[i] >> (8 * k)));
        } else {
            reply_st(c, st);
        }
        break;
    }

    default:
        reply_st(c, -14);
        break;
    }
}

static void handle_native(WiFiClient &c)
{
    Serial.print("Native client from ");
    Serial.println(c.remoteIP());
    c.setNoDelay(true);
    cortex_cleanup();
    blink_active = true;
    unsigned long last_cmd = millis();
    while (c.connected()) {
        if (c.available()) {
            char cmd = (char)c.read();
            last_cmd = millis();
            if (cmd == 'Q') break;
            /* remote_bitbang single-char commands vs native binary commands */
            if (strchr("01234567RrstuOocdefgZzBb", cmd)) {
                handle_bitbang(cmd, c);
            } else {
                handle_native_cmd(cmd, c);
            }
        } else if (millis() - last_cmd > 60000) {
            Serial.println("Native idle timeout");
            break;
        } else {
            delay(1);
        }
    }
    c.stop();
    blink_active = false;
    digitalWrite(PIN_LED, LOW);
    Serial.println("Native client disconnected");
}

/* ------------------------------------------------------------------ *
 * GDB Remote Serial Protocol server (port 3333).
 * ------------------------------------------------------------------ */

static const char target_xml[] =
    "<target version=\"1.0\"><architecture>armv7m</architecture>"
    "<feature name=\"org.gnu.gdb.arm.m-profile\">"
    "<reg name=\"r0\" bitsize=\"32\"/><reg name=\"r1\" bitsize=\"32\"/>"
    "<reg name=\"r2\" bitsize=\"32\"/><reg name=\"r3\" bitsize=\"32\"/>"
    "<reg name=\"r4\" bitsize=\"32\"/><reg name=\"r5\" bitsize=\"32\"/>"
    "<reg name=\"r6\" bitsize=\"32\"/><reg name=\"r7\" bitsize=\"32\"/>"
    "<reg name=\"r8\" bitsize=\"32\"/><reg name=\"r9\" bitsize=\"32\"/>"
    "<reg name=\"r10\" bitsize=\"32\"/><reg name=\"r11\" bitsize=\"32\"/>"
    "<reg name=\"r12\" bitsize=\"32\"/><reg name=\"sp\" bitsize=\"32\" type=\"data_ptr\"/>"
    "<reg name=\"lr\" bitsize=\"32\"/><reg name=\"pc\" bitsize=\"32\" type=\"code_ptr\"/>"
    "<reg name=\"xpsr\" bitsize=\"32\"/><reg name=\"msp\" bitsize=\"32\" type=\"data_ptr\"/>"
    "<reg name=\"psp\" bitsize=\"32\" type=\"data_ptr\"/><reg name=\"primask\" bitsize=\"32\"/>"
    "<reg name=\"basepri\" bitsize=\"32\"/><reg name=\"faultmask\" bitsize=\"32\"/>"
    "<reg name=\"control\" bitsize=\"32\"/></feature></target>";

/* GDB register index -> DCRSR REGSEL. */
static const uint8_t gdb_to_dcrsr[23] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
    16, 17, 18, 23, 22, 21, 20
};

static const char *hex_chars = "0123456789abcdef";

static void gdb_put_packet(WiFiClient &c, const uint8_t *data, int len)
{
    uint8_t sum = 0;
    for (int i = 0; i < len; i++) sum += data[i];
    c.write('$');
    if (len) c.write(data, (size_t)len);
    c.write('#');
    c.write((uint8_t)hex_chars[sum >> 4]);
    c.write((uint8_t)hex_chars[sum & 15]);
}

static void gdb_put_str(WiFiClient &c, const char *s)
{
    gdb_put_packet(c, (const uint8_t *)s, (int)strlen(s));
}

static bool gdb_get_packet(WiFiClient &c, uint8_t *buf, int maxlen, int *outlen)
{
    while (c.connected()) {
        while (c.connected() && !c.available()) delay(1);
        if (!c.connected()) return false;
        int b = c.read();
        if (b == '$') break;
    }
    int len = 0;
    uint8_t sum = 0;
    while (c.connected()) {
        while (c.connected() && !c.available()) delay(1);
        if (!c.connected()) return false;
        int b = c.read();
        if (b == '#') break;
        buf[len++] = (uint8_t)b;
        sum += (uint8_t)b;
        if (len >= maxlen) return false;
    }
    char cs0 = 0, cs1 = 0;
    while (c.connected()) {
        while (c.connected() && !c.available()) delay(1);
        if (!c.connected()) return false;
        int b = c.read();
        if (cs0 == 0) cs0 = (char)b;
        else { cs1 = (char)b; break; }
    }
    int hv0 = cs0 >= '0' && cs0 <= '9' ? cs0 - '0' :
              (cs0 >= 'a' && cs0 <= 'f' ? cs0 - 'a' + 10 : 0);
    int hv1 = cs1 >= '0' && cs1 <= '9' ? cs1 - '0' :
              (cs1 >= 'a' && cs1 <= 'f' ? cs1 - 'a' + 10 : 0);
    if ((hv0 * 16 + hv1) != sum) {
        c.write('-');
        return false;
    }
    c.write('+');
    *outlen = len;
    return true;
}

static int gdb_hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static uint32_t gdb_parse_hex(const char *s, int *n)
{
    uint32_t v = 0;
    int i = 0;
    while (s[i] && s[i] != ',' && s[i] != ':' && gdb_hexval(s[i]) >= 0) {
        v = (v << 4) | (uint32_t)gdb_hexval(s[i]);
        i++;
    }
    if (n) *n = i;
    return v;
}

static void gdb_read_regs_reply(WiFiClient &c)
{
    char buf[23 * 8 + 1];
    uint32_t v;
    for (int i = 0; i < 23; i++) {
        if (cortex_read_reg(gdb_to_dcrsr[i], &v) != 1) v = 0;
        for (int k = 0; k < 4; k++) {
            uint8_t b = (uint8_t)(v >> (8 * k));
            buf[i * 8 + 7 - k * 2] = hex_chars[b & 15];
            buf[i * 8 + 6 - k * 2] = hex_chars[b >> 4];
        }
    }
    buf[23 * 8] = 0;
    gdb_put_str(c, buf);
}

static void gdb_mem_read_reply(WiFiClient &c, uint32_t addr, uint32_t len)
{
    if (len > 2048) len = 2048;
    uint8_t *buf = (uint8_t *)malloc(len ? len : 1);
    if (!buf) { gdb_put_str(c, "E01"); return; }
    int st = mem_read(addr, buf, len);
    if (st != 1) {
        free(buf);
        gdb_put_str(c, "E01");
        return;
    }
    uint8_t *out = (uint8_t *)malloc(len * 2 + 1);
    if (!out) { free(buf); gdb_put_str(c, "E01"); return; }
    for (uint32_t i = 0; i < len; i++) {
        out[2 * i] = (uint8_t)hex_chars[buf[i] >> 4];
        out[2 * i + 1] = (uint8_t)hex_chars[buf[i] & 15];
    }
    out[len * 2] = 0;
    gdb_put_packet(c, out, (int)(len * 2));
    free(out);
    free(buf);
}

static void gdb_mem_write_cmd(WiFiClient &c, const char *cmd, int len)
{
    /* M<addr>,<len>:<hex> */
    const char *p = cmd + 1;
    int n;
    uint32_t addr = gdb_parse_hex(p, &n);
    p += n;
    if (*p != ',') { gdb_put_str(c, "E01"); return; }
    p++;
    uint32_t mlen = gdb_parse_hex(p, &n);
    p += n;
    if (*p != ':' || mlen > 2048) { gdb_put_str(c, "E01"); return; }
    p++;
    uint8_t *buf = (uint8_t *)malloc(mlen ? mlen : 1);
    if (!buf) { gdb_put_str(c, "E01"); return; }
    for (uint32_t i = 0; i < mlen; i++) {
        int hi = gdb_hexval(p[2 * i]);
        int lo = gdb_hexval(p[2 * i + 1]);
        if (hi < 0 || lo < 0) { free(buf); gdb_put_str(c, "E01"); return; }
        buf[i] = (uint8_t)((hi << 4) | lo);
    }
    int st = mem_write(addr, buf, mlen);
    free(buf);
    gdb_put_str(c, st == 1 ? "OK" : "E01");
    (void)len;
}

static void gdb_set_pc(WiFiClient &c, uint32_t addr)
{
    (void)c;
    cortex_write_reg(15, addr);
}

static void gdb_qxfer(WiFiClient &c, const char *cmd)
{
    /* qXfer:features:read:target.xml:offset,len */
    if (strncmp(cmd, "qXfer:features:read:target.xml:", 31) != 0) {
        gdb_put_str(c, "");
        return;
    }
    const char *p = cmd + 31;
    long off = strtol(p, NULL, 16);
    const char *comma = strchr(p, ',');
    long qlen = comma ? strtol(comma + 1, NULL, 16) : 0;
    int xml_len = (int)strlen(target_xml);
    if (off >= xml_len) { gdb_put_str(c, "l"); return; }
    if (qlen > 400) qlen = 400;
    if (off + qlen > xml_len) qlen = xml_len - off;
    bool more = (off + qlen) < xml_len;
    uint8_t *resp = (uint8_t *)malloc((size_t)qlen + 1);
    if (!resp) { gdb_put_str(c, "E01"); return; }
    resp[0] = (uint8_t)(more ? 'm' : 'l');
    memcpy(resp + 1, target_xml + off, (size_t)qlen);
    gdb_put_packet(c, resp, (int)qlen + 1);
    free(resp);
}

static void handle_gdb(WiFiClient &c)
{
    Serial.print("GDB client from ");
    Serial.println(c.remoteIP());
    c.setNoDelay(true);
    cortex_cleanup();
    blink_active = true;

    bool dap_ok = (swd_init() == 1);
    if (dap_ok) {
        /* attach: halt the core so gdb sees a stopped target */
        cortex_halt();
    }

    uint8_t pkt[2048];
    while (c.connected()) {
        int len = 0;
        if (!gdb_get_packet(c, pkt, sizeof(pkt), &len)) {
            if (!c.connected()) break;
            continue;
        }
        char cmd = (char)pkt[0];
        pkt[len] = 0;
        const char *s = (const char *)pkt;

        if (!dap_ok && cmd != 'q' && cmd != 'Q' && cmd != 'H' && cmd != 'D' &&
            cmd != 'k' && cmd != '?' && cmd != 'v') {
            gdb_put_str(c, "E01");
            continue;
        }

        switch (cmd) {
        case '?':
            gdb_put_str(c, "S05");
            break;

        case 'g':
            gdb_read_regs_reply(c);
            break;

        case 'G': {
            for (int i = 0; i < 23 && i * 8 + 7 < len; i++) {
                uint32_t v = 0;
                for (int k = 0; k < 4; k++) {
                    int hi = gdb_hexval(s[i * 8 + 2 * k]);
                    int lo = gdb_hexval(s[i * 8 + 2 * k + 1]);
                    if (hi < 0 || lo < 0) break;
                    v |= (uint32_t)((hi << 4) | lo) << (8 * k);
                }
                cortex_write_reg(gdb_to_dcrsr[i], v);
            }
            gdb_put_str(c, "OK");
            break;
        }

        case 'p': {
            int n;
            uint32_t idx = gdb_parse_hex(s + 1, &n);
            if (idx >= 23) { gdb_put_str(c, "E01"); break; }
            uint32_t v;
            if (cortex_read_reg(gdb_to_dcrsr[idx], &v) != 1) {
                gdb_put_str(c, "E01");
                break;
            }
            char out[9];
            for (int k = 0; k < 4; k++) {
                uint8_t b = (uint8_t)(v >> (8 * k));
                out[7 - k * 2] = hex_chars[b & 15];
                out[6 - k * 2] = hex_chars[b >> 4];
            }
            out[8] = 0;
            gdb_put_str(c, out);
            break;
        }

        case 'P': {
            int n;
            uint32_t idx = gdb_parse_hex(s + 1, &n);
            if (idx >= 23 || s[1 + n] != '=') { gdb_put_str(c, "E01"); break; }
            uint32_t v = gdb_parse_hex(s + 1 + n + 1, NULL);
            cortex_write_reg(gdb_to_dcrsr[idx], v);
            gdb_put_str(c, "OK");
            break;
        }

        case 'm': {
            int n;
            uint32_t addr = gdb_parse_hex(s + 1, &n);
            const char *p = s + 1 + n;
            uint32_t mlen = 0;
            if (*p == ',') mlen = gdb_parse_hex(p + 1, NULL);
            gdb_mem_read_reply(c, addr, mlen);
            break;
        }

        case 'M':
            gdb_mem_write_cmd(c, s, len);
            break;

        case 'c':
            if (len > 1) gdb_set_pc(c, gdb_parse_hex(s + 1, NULL));
            if (cortex_resume_checked() != 1) { gdb_put_str(c, "E01"); break; }
            delay(10);
            for (;;) {
                if (c.available()) {
                    int b = c.read();
                    if (b == 0x03) {
                        cortex_halt();
                        gdb_put_str(c, "S02");
                        break;
                    }
                }
                uint32_t dhcsr = 0;
                int st = mem_rd32(DHCSR, &dhcsr);
                if (st != 1) {
                    st = swd_init();
                    if (st == 1) st = mem_rd32(DHCSR, &dhcsr);
                    if (st != 1) { gdb_put_str(c, "E01"); break; }
                }
                if (dhcsr & DHCSR_S_HALT) {
                    gdb_put_str(c, "S05");
                    break;
                }
                delay(2);
            }
            break;

        case 's':
            if (len > 1) gdb_set_pc(c, gdb_parse_hex(s + 1, NULL));
            if (cortex_step() != 1) { gdb_put_str(c, "E01"); break; }
            gdb_put_str(c, "S05");
            break;

        case 'Z':
        case 'z': {
            if (len < 3 || s[1] != '1' && s[1] != '2' && s[1] != '3' && s[1] != '4' &&
                s[1] != '0') { gdb_put_str(c, "E01"); break; }
            int n;
            uint32_t addr = gdb_parse_hex(s + 2, &n);
            const char *p = s + 2 + n;
            uint32_t blen = (*p == ',') ? gdb_parse_hex(p + 1, NULL) : 0;
            (void)blen;
            int st;
            if (cmd == 'z') {
                st = (s[1] == '2' || s[1] == '3' || s[1] == '4') ? wp_clear(addr)
                                                                 : bp_clear(addr);
            } else if (s[1] == '0' || s[1] == '1') {
                st = bp_set(addr, NULL);
            } else {
                uint32_t kind = (s[1] == '2') ? 7 : (s[1] == '3') ? 6 : 4;
                st = wp_set(addr, kind, NULL);
            }
            gdb_put_str(c, st == 1 ? "OK" : "E01");
            break;
        }

        case 'H':
        case 'D':
        case 'k':
            gdb_put_str(c, "OK");
            break;

        case 'q':
            if (strncmp(s, "qSupported", 10) == 0) {
                gdb_put_str(c, "PacketSize=400;qXfer:features:read+");
            } else if (strncmp(s, "qXfer", 5) == 0) {
                gdb_qxfer(c, s);
            } else if (strncmp(s, "qAttached", 9) == 0) {
                gdb_put_str(c, "1");
            } else if (strncmp(s, "qC", 2) == 0) {
                gdb_put_str(c, "QC 1");
            } else if (strncmp(s, "qOffsets", 8) == 0) {
                gdb_put_str(c, "Text=0;Data=0;Bss=0");
            } else if (strncmp(s, "qSymbol:", 8) == 0) {
                gdb_put_str(c, "OK");
            } else {
                gdb_put_str(c, "");
            }
            break;

        case 'v':
            gdb_put_str(c, "");
            break;

        case 'Q':
            gdb_put_str(c, "");
            break;

        default:
            gdb_put_str(c, "");
            break;
        }
    }

    /* detach: leave the target running */
    cortex_resume();
    c.stop();
    blink_active = false;
    digitalWrite(PIN_LED, LOW);
    Serial.println("GDB client disconnected");
}

/* ------------------------------------------------------------------ *
 * Remote_bitbang compatibility (port 5555, single-char commands).
 * ------------------------------------------------------------------ */

static void handle_bitbang(char c, WiFiClient &client)
{
    switch (c) {
    case '0': case '1': case '2': case '3':
    case '4': case '5': case '6': case '7': {
        int v = c - '0';
        digitalWrite(PIN_SWCLK, (v & 4) ? HIGH : LOW);
        digitalWrite(PIN_TMS,   (v & 2) ? HIGH : LOW);
        digitalWrite(PIN_TDI,   (v & 1) ? HIGH : LOW);
        break;
    }
    case 'R':
        client.write(digitalRead(PIN_TDO) ? '1' : '0');
        break;
    case 'r': digitalWrite(PIN_NRST, HIGH); break;
    case 's': digitalWrite(PIN_NRST, LOW);  break;
    case 't': digitalWrite(PIN_NRST, HIGH); break;
    case 'u': digitalWrite(PIN_NRST, LOW);  break;
    case 'O':
        pinMode(PIN_SWDIO, OUTPUT);
        digitalWrite(PIN_SWDIO, HIGH);
        swdio_output = true;
        break;
    case 'o':
        pinMode(PIN_SWDIO, INPUT_PULLUP);
        swdio_output = false;
        break;
    case 'c':
        pinMode(PIN_SWDIO, INPUT_PULLUP);
        swdio_output = false;
        client.write(digitalRead(PIN_SWDIO) ? '1' : '0');
        break;
    case 'd': swd_write(0, 0); break;
    case 'e': swd_write(0, 1); break;
    case 'f': swd_write(1, 0); break;
    case 'g': swd_write(1, 1); break;
    case 'Z': delay(1);            break;
    case 'z': delayMicroseconds(1); break;
    case 'B': blink_active = true;  break;
    case 'b':
        blink_active = false;
        digitalWrite(PIN_LED, LOW);
        break;
    default:
        break;
    }
}

void setup()
{
    pinMode(PIN_SWCLK, OUTPUT);
    pinMode(PIN_SWDIO, OUTPUT);
    pinMode(PIN_NRST,  OUTPUT);
    pinMode(PIN_TDI,   OUTPUT);
    pinMode(PIN_TMS,   OUTPUT);
    pinMode(PIN_TDO,   INPUT);
    pinMode(PIN_LED,   OUTPUT);
    digitalWrite(PIN_SWCLK, HIGH);
    digitalWrite(PIN_SWDIO, HIGH);
    digitalWrite(PIN_NRST,  HIGH);
    digitalWrite(PIN_TDI,   HIGH);
    digitalWrite(PIN_TMS,   HIGH);
    digitalWrite(PIN_LED,   LOW);

    /* Max drive strength on the SWD lines: with digitalWrite's overhead gone,
     * edges are fast enough now that a weak driver can round them off over
     * jumper wire. */
    gpio_set_drive_capability((gpio_num_t)PIN_SWCLK, GPIO_DRIVE_CAP_3);
    gpio_set_drive_capability((gpio_num_t)PIN_SWDIO, GPIO_DRIVE_CAP_3);

    Serial.begin(115200);
    delay(200);
    Serial.println("\nESP32 ST-Link probe starting...");
#if SERIAL_ONLY
    esp_log_level_set("*", ESP_LOG_NONE);
    Serial.println("SERIAL_ONLY mode (no WiFi)");
    /* No per-client "connect" event exists on the raw serial channel, so
     * release any stale debug-halt state left on the target right at boot
     * instead (see cortex_cleanup()). Best-effort: does nothing if no
     * target is wired up yet. */
    cortex_cleanup();
#else
    WiFi.onEvent([](WiFiEvent_t event, WiFiEventInfo_t info) {
        if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
            Serial.printf("WiFi disconnect reason=%d\n", info.wifi_sta_disconnected.reason);
        }
    });
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);                       /* keep radio awake: avoids
                                                 * multi-second latency spikes */
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.print("Connecting to ");
    Serial.println(WIFI_SSID);
    unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 30000) {
        delay(500);
        Serial.print(".");
    }
    Serial.println();
    Serial.print("STA IP: ");
    Serial.println(WiFi.localIP());
    if (WiFi.status() != WL_CONNECTED) {
        int n = WiFi.scanNetworks();
        Serial.printf("WiFi scan: %d networks\n", n);
        for (int i = 0; i < n && i < 20; i++) {
            Serial.printf("  %-32s ch=%d rssi=%d\n", WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i));
        }
        WiFi.scanDelete();
    }
    Serial.print("Native TCP port: ");
    Serial.println(TCP_PORT);
    Serial.print("GDB TCP port: ");
    Serial.println(GDB_PORT);
#endif
}

void loop()
{
    /* Serial command channel: same binary protocol as TCP, no WiFi needed. */
    if (Serial.available()) {
        serial_cmd_active = true;
        char cmd = (char)Serial.read();
        if (cmd != 'Q' && !strchr("01234567RrstuOocdefgZzBb", cmd)) {
            handle_native_cmd(cmd, Serial);
        }
    }

#if !SERIAL_ONLY
    if (WiFi.status() != WL_CONNECTED) {
        static unsigned long last_retry = 0;
        if (millis() - last_retry > 5000) {
            last_retry = millis();
            WiFi.reconnect();
        }
    }

    static WiFiServer native(TCP_PORT);
    static WiFiServer gdb(GDB_PORT);
    native.begin();
    gdb.begin();

    WiFiClient nc = native.available();
    if (nc) {
        handle_native(nc);
    } else {
        WiFiClient gc = gdb.available();
        if (gc) handle_gdb(gc);
    }
#endif

    if (blink_active) {
        digitalWrite(PIN_LED, HIGH);
        delay(80);
        digitalWrite(PIN_LED, LOW);
        delay(80);
    }
}
