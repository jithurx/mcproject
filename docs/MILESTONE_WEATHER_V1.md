# Milestone — Weather Station v1 on OLED (2026-10-10)

## Result
Blue Pill (CKS32F103C8T6) runs the full Smart Weather Station firmware:
DHT11 temperature/humidity + rain-sensor status on a cycling 3-screen
128×64 OLED UI, with the ESP8266/ML uplink stub live on USART1. Verified on
hardware: humidity reads, rain detects, temperature reads after fix.

## Firmware (`firmware/STM32_Weather/`, PIO `ststm32` + Arduino)
- `src/main.cpp`: 2s sensor poll, 3s screen cycle, 5s telemetry TX, ESP RX parse.
- Screen 1 `WEATHER STATION`: `Temp: 28.4C` / `Hum: 82 %`
- Screen 2 `RAIN STATUS`: `RAIN DETECTED` / `NO RAIN` + analog intensity
- Screen 3 `RAIN FORECAST`: `LIKELY/POSSIBLE/UNLIKELY` + probability % +
  `SRC:LOCAL` (heuristic) or `SRC:ESP` (fresh `<60s` ML value)
- ESP protocol (USART1 `PA9` TX / `PA10` RX @115200, attach ESP8266 later):
  out `T:28.4,H:82,R:0`, in `F:78` (0–100).
- Build: 43,800 bytes flash (66.9%), 1,808 bytes RAM (8.8%).

## Bug fixed this round
`Temp:` showed blank while humidity worked: `snprintf %2.1f` emits nothing
under newlib-nano (no float support). Fixed with `display.print(g_temp, 1)`
(Arduino `Print` formats floats itself). Humidity used `%2d`, unaffected.

## Flash record
- Path: ESP32 SWD probe @ `swd_delay_cycles = 250` (120 = garbage, 400 = slow).
- Chunked writer (1KB `P` chunks, 180s timeout — see `tools/README.md`): 28KB image
  ~23s/chunk, 43.8KB image ~14.8s/chunk; full read-back VERIFY PASS each time.
- Target resumed via `reset + resume` after each flash (script leaves it halted).

## Sensor wiring (working)
- OLED: `VCC→3V3, GND→GND, SCL→PB6, SDA→PB7` (I2C1, `0x3C`)
- DHT11: `VCC→3V3, GND→GND, DATA→PB8` + 4.7k–10k pull-up DATA↔3V3
- Rain: `VCC→3V3, GND→GND, DO→PB9` (LOW = rain), `AO→PA0`
- SWD kept: `ESP21→DIO, ESP22→CLK, ESP19→RST, GND→GND`; Blue Pill on own USB.
- Reserved: `PA9/PA10` USART1 for ESP8266, `PA13/PA14` for SWD.

## Sign-off
- [x] Humidity value on screen 1
- [x] Temperature value on screen 1 (after `%f` fix + reflash + VERIFY PASS)
- [x] Rain detection flips screen 2 (`DO LOW` = rain)
- [x] 3 screens cycle every 3s; forecast shows `SRC:LOCAL` until ESP attaches
- Next: attach ESP8266 → cloud dashboard → offline ML (Decision Tree) → `F:<prob>`
  back to screen 3 as `SRC:ESP`.
