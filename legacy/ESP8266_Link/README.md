# SUPERSEDED — custom ESP8266 uplink firmware

This directory holds the first ESP8266 approach: a custom PlatformIO firmware
(`nodemcuv2`) where the ESP itself parsed STM32 telemetry, ran the forecast,
and pushed to ThingSpeak.

It is **retired** in favor of the AT-command architecture (see
`docs/THINGSPEAK_AT.md`): the NodeMCU now runs **stock AT firmware v1.7.6**
(flashed with esptool, binaries in `third-party/esp8266-at/`), and the STM32
drives it over UART with `AT+CWJAP` / `AT+CIPSTART` / `AT+CIPSEND`. That keeps
all application logic in one place (`firmware/STM32_Weather/`) and the ESP
reflashable with standard tools.

Why AT won (then): no custom ESP toolchain needed for updates, the STM32 owns the
whole sequence (easier to debug over one serial log), and the ML forecast
moved to the laptop loop (`tools/ml_forecast.py` → ThingSpeak field5 →
ESP fetches `last.txt`), which needs no ESP code at all.

2026-10-11: the AT path itself was retired — UART0 contention (CP2102 vs
PA9 on RX/GPIO3) made it unflashable while wired and unmonitorable, and no
AT session ever completed. Current architecture is custom `esp-uplink`
firmware on flash-safe D5/D6 @9600, STM32 in telemetry mode; see
`docs/ESP_UPLINK.md` and `firmware/ESP8266_Bridge/README.md`.

Kept for reference. `include/config.h` was gitignored (WiFi/password/keys).
