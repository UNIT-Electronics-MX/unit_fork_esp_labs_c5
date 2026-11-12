# MicroPython examples for UNIT Pulsar ESP32-C5

These editable scripts adapt MicroPython topics from the Pulsar C6 guide: GPIO,
ADC, I²C, NeoPixel, RGB mixing, an external LED circuit, OLED, SPI/microSD,
UART loopback, Wi-Fi, and Bluetooth LE (including the eight B1–B8 labs). Pin assignments target Pulsar C5
V1.2.3 and match the C5 Arduino examples in `unit_fork_esp_c5_labs_web/sketches/pulsar-c5/`.

Install the [ESP32-C5 MicroPython firmware](https://micropython.org/download/ESP32_GENERIC_C5/)
before running these scripts. Flash the complete `.bin` image at **`0x2000`**,
as specified on the download page. A `.py` file is a script: run it through the
REPL or save it to the MicroPython filesystem. Do not flash it as firmware.

Open an example in the browser editor, change its parameters, and connect the
serial console. You can run or stop the script, save it to the board, or download
it. You can also open your own `.py` file. Save a script as `main.py` on the board
to run it at startup. Use `Ctrl+C` to stop a running script.

| File | Topic / lesson | C5 adaptation |
| --- | --- | --- |
| `gpio.py` | GPIO / P1 | LED on GPIO27; BOOT button on GPIO28 |
| `rgb_mixer.py` | RGB mixer / P2 | Interactive RGB and brightness commands on the WS2812 |
| `adc.py` | ADC / P3 | A2 / GPIO4 |
| `i2c_scan.py` | I²C / P4 | Onboard sensor bus on GPIO2/3 |
| `p4_sensor_stream.py` | Onboard sensors / P4 | Streams all five sensors to the Processing dashboard at 10 Hz |
| `rgb_led.py` | NeoPixel | WS2812 on GPIO0 (see note below) |
| `external_led.py` | First circuit | External LED on GPIO9 (D4) with PWM |
| `bmi270_id.py` | IMU on QWIIC / D1 | BMI270 on QWIIC GPIO1/6 (0x68 or 0x69) |
| `bmi270_motion.py` | IMU motion / D1 | Acceleration and gyro readings; install `libraries/bmi270.py` first |
| `bmi270_cube.py` | IMU cube / D1 | Streams orientation to the web and Processing cubes; install `libraries/bmi270.py` first |
| `tmp235_temperature.py` | Analog temperature / D3 | TMP235 VOUT on A2 / GPIO4 |
| `oled.py` | Standalone OLED / O1 | SSD1306 only on QWIIC GPIO1/6 |
| `oled_light.py` | OLED light graph / D2 | SSD1306 and TEMT6000 DDP on QWIIC GPIO1/6 |
| `st7735_display.py` | TFT display / SPI | 0.96-inch ST7735 80x160: CS D4/GPIO9, DC A2/GPIO4, RES A3/GPIO5, MOSI D11/GPIO7, SCK D13/GPIO27 |
| `st7735_showcase.py` | TFT showcase / O3 | Same ST7735 wiring; BOOT changes four animated scenes |
| `microsd.py` | SPI / SD / P5 | SCK27, MOSI7, MISO26, CS23 |
| `uart_loopback.py` | UART / P6 | TX0/GPIO11 bridged to RX0/GPIO12 |
| `wifi.py` | Communication / Wi-Fi | Editable network credentials |
| `ble_scan.py` | Communication / Bluetooth | BLE advertisement scan |
| `thingspeak_dht.py` | Wi-Fi to the cloud / I1 | DHT11 on A2 (GPIO4); 2.4 or 5 GHz; ThingSpeak key |
| `wifi_w1_scan.py` | Wi-Fi / W1 scan & join | `wifi_kit.py`; password typed once, kept in NVS |
| `wifi_w2_basic.py` | Wi-Fi / W2 basic local page | Edit Wi-Fi credentials or reuse an active W1 connection; view connected signal and control the RGB LED from a phone |
| `wifi_w2_display.py` | Wi-Fi / W2 TFT signal | ST7735 wired as O2; install `st7735.py`; live RSSI, 24-second trend and IP on screen, plus phone LED control |
| `wifi_w2_phone.py` | Wi-Fi / W2 phone control | `wifi_kit.py`; phone page, REST API, WebSocket, set-up network, `relay` |
| `wifi_w3_basic.py` | Wi-Fi / W3 basic weather | Edit Wi-Fi credentials or reuse an active W1 connection; edit coordinates; prints current Open-Meteo data |
| `wifi_w3_oracle.py` | Wi-Fi / W3 internet & AI | `wifi_kit.py`; a network saved by W1; LiteLLM gateway for the oracle |
| `ble_touch.py` | Bluetooth LE touch / I2 | TTP223B on A3 (GPIO5); notifies `touched` / `released` |
| `fht40_temp_humidity.py` | Onboard sensors / FHT40 | Temperature and humidity at 0x44 on GPIO2/GPIO3 |
| `lps22hb_pressure.py` | Onboard sensors / LPS22HB | Pressure and temperature at 0x5C on GPIO2/GPIO3 |
| `vcnl4040_light_prox.py` | Onboard sensors / VCNL4040 | Proximity and ambient light at 0x60 on GPIO2/GPIO3 |
| `max17048_battery.py` | Onboard sensors / MAX17048 | LiPo voltage and charge at 0x36 on GPIO2/GPIO3 |
| `mmc5603_compass.py` | Onboard sensors / MMC5603 | Three magnetic axes at 0x30 on GPIO2/GPIO3, 20 Hz |
| `ble_b1_listen.py` | Bluetooth LE / B1 Listen | Continuous scan; serial list, bytes, watch; BOOT gestures |
| `ble_b2_beacon.py` | Bluetooth LE / B2 Be seen | Bench, iBeacon, Eddystone-URL and name packets |
| `ble_b3_gatt.py` | Bluetooth LE / B3 Connect & talk | Bench GATT service; LED write lights the WS2812 |
| `ble_b4_pair.py` | Bluetooth LE / B4 Pair & trust | Vault needs MITM pairing; bonds in `ble_bonds.json` |
| `ble_b5_app.py` | Bluetooth LE / B5 Phone app | ble-app Canvas, Sensors, Speed; touch replaced by A2 |
| `ble_b6_keyboard.py` | Bluetooth LE / B6 Keyboard & media | HID over GATT, US layout, Just Works bond |
| `ble_b7_midi.py` | Bluetooth LE / B7 Music | Standard BLE-MIDI service; BOOT plays the scale |
| `ble_b8_host.py` | Bluetooth LE / B8 Host | Central: explore GATT, host a BLE keyboard or mouse |

`oled.py` (Display lesson O1) uses only the SSD1306 screen; `oled_light.py` (Sensors lesson D2) additionally needs a TEMT6000 DDP sensor. Both scripts require UNIT's copy of the MicroPython `ssd1306.py` driver. Save
`libraries/ssd1306.py` as `ssd1306.py` on the board before running either example. The browser workspace includes an **Install ssd1306.py** action.
`microsd.py` uses UNIT's SPI `sdcard.py` driver. Save
`libraries/sdcard.py` as `sdcard.py` on the board before running the example.
The workspace includes an **Install sdcard.py** action. Both drivers are MIT
licensed; the license is in `libraries/LICENSE`. Upstream sources:
[UNIT SSD1306 driver](https://github.com/UNIT-Electronics-MX/unit_devlab_micropython_libraries/blob/main/software/ssd1306/ssd1306.py)
and [UNIT SD driver](https://github.com/UNIT-Electronics-MX/unit_devlab_micropython_libraries/blob/main/software/sdcard/sdcard.py).

`bmi270_motion.py` and `bmi270_cube.py` use the MIT-licensed BMI270 driver in
`libraries/bmi270.py`. Save it on the board as `bmi270.py` before running them.
The cube script also accepts an older installed driver, with slower separate reads.
The updated driver reads all six axes in one I²C transfer.

`st7735_display.py` and `st7735_showcase.py` accept both the direct-drawing
`libraries/st7735.py` driver and the earlier web driver with `rgb565` and
`frame`. Either can be saved as `st7735.py` on the board; no reinstall is
needed when one of these versions is already present. The ST7735 TFT shares the SPI bus with the microSD card, so the
example holds the SD chip select (GPIO23) high. The TFT's SDA and SCL labels
are SPI data and clock, not I²C. The driver file does not state a license;
its source is [boochow's MicroPython-ST7735](https://github.com/boochow/MicroPython-ST7735),
which is based on GuyCarver's ST7735 driver.

The five onboard sensors use the sensor bus on GPIO2/GPIO3. Save each driver
from `libraries/` to the board under the same name before running its example:
`fht40.py`, `lps22hb.py`, `vcnl4040.py`, `max1704x.py` and `mmc5603.py`.
`p4_sensor_stream.py` reads all five sensors on the same bus and keeps streaming
when one is absent or its driver fails. Install all five drivers in the web code
workspace, then save the script as `main.py` to let Processing reopen the USB
port after a board reset. The stream format and Processing instructions are in
`unit_fork_esp_c5_labs_web/sketches/processing/p4_sensor_lab/README.md`.
The individual examples check the sensor address first and stop with an error
if the sensor does not answer. The drivers come from the
[UNIT DevLab MicroPython libraries](https://github.com/UNIT-Electronics-MX/unit_devlab_micropython_libraries),
which are MIT licensed. The VCNL4040 driver is a port of Adafruit's library, and
the MMC5603 and MAX17043/48 drivers are based on upstream projects. Their license
headers stay in the files.

## Wi-Fi labs W1–W3

For a basic implementation, run `wifi_w2_basic.py` or `wifi_w3_basic.py` first.
They are standalone: enter `WIFI_SSID` and `WIFI_PASSWORD` in the script, or
connect with W1 and keep the station active. W2 serves `http://<board IP>` to a
phone on the same Wi-Fi and controls the on-board RGB LED. W3 makes one HTTPS
request to Open-Meteo using editable coordinates and prints current weather.
Neither basic script needs `wifi_kit.py`; W2 has no remote access or login.
`wifi_w2_display.py` combines that W2 phone page with the ST7735 screen from
O2/O3. Wire the 80x160 TFT the same way, install `st7735.py` from the
editor, and run the script. The screen shows the connected network RSSI in
dBm, bars, a 24-second trend, the IP address and the selected LED colour.
It uses an off-screen framebuffer to update without visible clearing.
Do not share a script containing your Wi-Fi password.

The advanced examples remain available:

`wifi_w1_scan.py`, `wifi_w2_phone.py` and `wifi_w3_oracle.py` port the C++ labs in
`labs/wifi-lab-w1` … `labs/wifi-lab-w3` to MicroPython on the Pulsar C5. They share
`libraries/wifi_kit.py`, the MicroPython counterpart of `kit/lib/wifi-kit`: the
saved network in NVS, joining with time-outs and retries, scanning, internet time,
`bench.local`, a signal history, a serial console that never echoes secrets, BOOT
gestures, the WS2812 as a status light, and `ScanLab`: the scan table, views and
`join` that W1 and W2 share. Save it on the board as `wifi_kit.py`
first; the workspace has an **Install wifi_kit.py** action.

The commands and BOOT gestures match the C++ labs. The differences come from the
board and from MicroPython:

- No screen. The views become commands (type `help`): W1 has networks, channels,
  status and signal; W3 has now, forecast, week, oracle and status. The WS2812 is
  blue while scanning, blinks amber while joining, turns green when joined and red
  after a failure. W3's oracle sets its colour until `led off`.
- Dual band. The C5 scans and joins 2.4 and 5 GHz networks; the table has a band
  column and the channels view lists both.
- The network lives in the NVS namespace `mpwifi`, not in the Arduino labs' `wifi`
  namespace, so each language keeps its own. W3 reads the one W1 saved and keeps
  its city in `w3` and the gateway in `ai`, like the C++ lab.
- `wlan.scan()` blocks for two to five seconds; the console waits meanwhile. The
  same goes for W3's requests: there is no worker task.
- No ICMP ping in MicroPython. `reach [ip] [port]` times a TCP connection instead:
  a host that accepts or refuses it is reachable; silence means it is not.
- No POSIX time zones. W1 takes an offset (`tz -6`, `tz 5:30`); W3 uses the one
  Open-Meteo reports for your city.
- MicroPython's `requests` encrypts HTTPS but does not check the server's
  certificate. The C++ lab verifies it against root certificates.

W2 runs both C++ variations in one script on `asyncio`: the REST API and phone
page on port 80, the `/ws` WebSocket, the `bench-setup` network with a captive DNS
server, the admin token, and `relay wss://…` for the remote link. Its status JSON
matches `apps/dashboard/shared/protocol.ts`, so the dashboard works unchanged.
What differs:

- No firmware updates over HTTP: `POST /api/ota` answers 501. MicroPython runs
  scripts; save new ones over USB.
- No screen to dim: the brightness slider sets the LED's brightness, and the views
  print on the console. A black LED colour hands the LED back to the Wi-Fi state.
- One program, one radio: a scan stops the web server for two to five seconds, so
  once joined W2 scans only when asked.
- The admin token lives in the NVS namespace `mpapi`; the relay address in `relay`.
  The relay encrypts with TLS but does not check the server's certificate.

## Bluetooth LE labs B1–B8

`ble_b1_listen.py` … `ble_b8_host.py` port the eight C++ labs in `labs/ble-lab-b1`
… `labs/ble-lab-b8` to MicroPython on the Pulsar C5. They share
`libraries/ble_kit.py`, the MicroPython counterpart of `kit/lib/ble-kit`: the
radio and its events, a serial console that does not block the radio, BOOT
gestures, the WS2812 as a status light, a bond store and the Bench GATT service.
Save it on the board as `ble_kit.py` first; the workspace has an
**Install ble_kit.py** action.

The service UUIDs, packet layouts and the HID Report Map match the C++ labs, so
nRF Connect, LightBlue, GarageBand and `apps/ble-app` see the same device. The
differences come from the board and from MicroPython's `bluetooth` module:

- No screen. Each lab prints to the serial console, and the views become commands
  (type `help`). BOOT (GPIO28) replaces the push button: click, double-click and
  long press keep their roles. The WS2812 blinks blue while advertising or
  scanning and turns green when connected.
- No touch sensor on the ESP32-C5. B5's touch value carries the A2 (GPIO4) ADC
  reading, and B6/B7 drop the touch wires.
- MicroPython does not report subscriptions, connection RSSI, connection
  parameters or the PHY. The labs notify every connected device. B5 sends RSSI 0
  and logs Link requests without acting on them. B3 has no `params` or `phy` command.
- No TX power control. B2 advertises the TX power value, but cannot change it.
- Bonds are saved in `ble_bonds.json` (B4, B6) and `ble_host_bonds.json` (B8). Use
  `forget` and also forget the board on the phone.
- B6 types with a US layout. B8 decodes keyboards that send 8-byte boot-style
  reports and prints any other report as hex.

These scripts compile with `mpy-cross` 1.29 and their packet logic was tested on
a computer. Radio behaviour still needs testing on a Pulsar C5.

The ADC example uses 11 dB attenuation. Its millivolt reading is approximate
and may saturate near 3.3 V. Never exceed 3.3 V on A2.

The C5 schematic connects the WS2812 to GPIO0, while one UNIT illustration
labels it GPIO8. If `rgb_led.py` or `rgb_mixer.py` does not light the LED, try `RGB_PIN = 8` and
check your board revision.

The P1–P6, O1 and D2 scripts are selected by the Labs code platform control. Run
`rgb_mixer.py` or `uart_loopback.py` first, then send commands from the USB
serial terminal. Use Stop before running a different script.

These examples passed a Python syntax check. Their hardware behavior still
needs testing on a Pulsar C5 and its peripherals.
