# Wiring

The probe drives SWD over the same pins OpenOCD's `bitbang` driver expects. A
hardware NRST line is optional but recommended; without it the probe cannot reset
the target.

## SWD (minimum, STM32F103 "Blue Pill" example)

```
ESP32 DevKitC                          Blue Pill (STM32F103CB)
┌──────────────┐                       ┌──────────────────┐
│ GPIO21 SWDIO ├───────────────────────┤ PA13 (SWDIO/JTMS)│
│ GPIO22 SWCLK ├───────────────────────┤ PA14 (SWCLK/JTCK)│
│ GPIO19 NRST  ├─────[optional]───────►│ NRST             │
│ GND          ├───────────────────────┤ GND              │
└──────────────┘                       └──────────────────┘
```

- Use short wires; SWD at these bit-banged speeds is forgiving but clean GND helps.
- **Common ground is required** — the probe and target must share GND.
- NRST is active-low; the probe drives it high (released) on boot.

## Full pin map

| ESP32 pin | Role      | Direction       | Notes                              |
| --------- | --------- | --------------- | ---------------------------------- |
| GPIO21    | SWDIO     | bi-directional  | PA13 on STM32 (SWDIO/JTMS)         |
| GPIO22    | SWCLK     | output          | PA14 on STM32 (SWCLK/JTCK)         |
| GPIO19    | NRST      | output          | optional, active-low reset         |
| GPIO18    | TDI       | output          | PA15 on STM32 (JTDI)               |
| GPIO23    | TMS       | output          | PA13 on STM32 (JTMS) — SWD mode    |
| GPIO32    | TDO       | input           | PB3 on STM32 (JTDO)                |
| GPIO2     | LED       | output          | activity blink                     |
| GND       | GND       | —               | must be common with the target     |

## Other targets

Any ARM Cortex-M target with SWD pins works the same way:

- **SWDIO** → the target's SWDIO/TCKIO pin
- **SWCLK** → the target's SWCLK/TCK pin
- **NRST** → the target's reset pin (optional)

The DPIDR is verified at init (`0x1ba01477` for STM32F1). If your target has a
different DPIDR, update the check in `swd_init()` in `esp32_swd_probe.ino`.

## Power

The probe is powered over USB (5 V from the DevKitC's USB port). It does **not**
power the target — power the target separately (e.g. 3.3 V from the Blue Pill's own
regulator or bench supply). Ensure logic levels match (both 3.3 V).
