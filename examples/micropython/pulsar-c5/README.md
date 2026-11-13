# MicroPython examples for UNIT Pulsar ESP32-C5

These editable scripts adapt MicroPython topics from the Pulsar C6 guide: GPIO,
ADC, I²C, NeoPixel, RGB mixing, an external LED circuit, OLED, SPI/microSD,
UART loopback, Wi-Fi, Bluetooth LE (B1–B8), and PN532 I²C RFID/NFC (R1–R8). Pin assignments target Pulsar C5
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
| `ics43434_mic.py` | I²S microphone / S1 extension | DevLab ICS-43434 on A2/GPIO4 (SCK), A3/GPIO5 (WS), D4/GPIO9 (SD); custom firmware required |
| `mic_ics43434_j20.py` | I²S microphone / J20 | ICS-43434 on J20; prints the 24-bit peak level over the web serial console |
| `mic_ics43434_usb_pads.py` | I²S microphone / web audio | Original A2/GPIO4, A3/GPIO5, D4/GPIO9 edge-pad wiring; streams 48 kHz mono PCM |
| `mic_ics43434_usb_master.py` | I²S microphone / web audio | ICS-43434 on J20; streams 48 kHz mono PCM for browser listening |
| `rfid_r1_pn532.py` | RFID & NFC / R1 | PN532 V3 over QWIIC GPIO1/6; install `pn532.py` first |
| `rfid_r2_pn532.py` | RFID & NFC / R2 | PN532 V3 over QWIIC GPIO1/6; install `pn532.py` first |
| `rfid_r3_pn532.py` | RFID & NFC / R3 | PN532 V3 over QWIIC GPIO1/6; install `pn532.py` first |
| `rfid_r4_pn532.py` | RFID & NFC / R4 | PN532 V3 over QWIIC GPIO1/6; install `pn532.py` first |
| `rfid_r5_pn532.py` | RFID & NFC / R5 | PN532 V3 over QWIIC GPIO1/6; install `pn532.py` first |
| `rfid_r6_pn532.py` | RFID & NFC / R6 | PN532 V3 over QWIIC GPIO1/6; install `pn532.py` first |
| `rfid_r7_pn532.py` | RFID & NFC / R7 | PN532 V3 over QWIIC GPIO1/6; install `pn532.py` first |
| `rfid_r8_pn532.py` | RFID & NFC / R8 | PN532 V3 over QWIIC GPIO1/6; install `pn532.py` first |

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

## DevLab ICS-43434 in the web serial editor

`ics43434_mic.py` reads the **left** I²S slot at 16 kHz and prints one
`MIC,dBFS,peak16` line about every 100 ms. Open the example in the browser's
**Firmware & console → Code editor** view; connect Web Serial and press **Run**.
Speak or clap near the microphone and watch the numbers change. Press **Stop**
to release I²S. The script does not record, save or upload audio.

With USB disconnected, wire the module's edge pads as follows. Confirm the
power pin on the released board revision before applying power: the current
[hardware pinout](https://github.com/UNIT-Electronics-MX/unit_devlab_i2s_ics_43434_mems_microphone/blob/main/hardware/README.md#pinout)
describes `VSYS` as the 3.3 V system rail, while the full module supply range
is pending validation.

| DevLab ICS-43434 | Pulsar C5 | Purpose |
| --- | --- | --- |
| `VSYS` | `3V3` | 3.3 V system rail |
| `GND` | `GND` | Common ground |
| `CH` | `GND` | Select left slot (also the board default) |
| `SCK` | `A2 / GPIO4` | I²S bit clock |
| `WS` | `A3 / GPIO5` | I²S word select |
| `SD` | `D4 / GPIO9` | Audio data into the C5 |

The module's **six-pin JST-SH I²S connector is not the C5's four-pin QWIIC/I²C
connector**. Do not plug one into the other. Use the edge pads and appropriate
wires. The microphone's bottom acoustic port must remain unobstructed.

The official `ESP32_GENERIC_C5` MicroPython v1.29.0 image disables
`machine.I2S` in
[`mpconfigboard.h`](https://github.com/micropython/micropython/blob/v1.29.0/ports/esp32/boards/ESP32_GENERIC_C5/mpconfigboard.h).
A C5 build with `MICROPY_PY_MACHINE_I2S` enabled is required. The site's
firmware view can flash a user-supplied **complete** `.bin` at `0x2000`;
its bundled generic image is not suitable for this example. Check the board
first with `import machine; print(hasattr(machine, "I2S"))` in the web terminal.
The script raises a clear error if the class is absent.

To build a compatible image, follow the official [MicroPython ESP32 build
guide](https://github.com/micropython/micropython/blob/v1.29.0/ports/esp32/README.md)
with MicroPython v1.29.0 and ESP-IDF v5.5.2. In
`ports/esp32/boards/ESP32_GENERIC_C5/mpconfigboard.h`, change
`MICROPY_PY_MACHINE_I2S (0)` to `(1)` before building:

```sh
# First install ESP-IDF for esp32c5 and source its export.sh.
make -C mpy-cross
cd ports/esp32
make submodules
make BOARD=ESP32_GENERIC_C5
```

The combined image is `ports/esp32/build-ESP32_GENERIC_C5/firmware.bin`.
Select it as a custom complete `.bin` in the web flasher at `0x2000`, then
confirm `hasattr(machine, "I2S")` returns `True` in the serial terminal.
This recipe has not been built or tested on the released mic and Pulsar C5
combination here. UNIT's published
[module examples](https://github.com/UNIT-Electronics-MX/unit_devlab_i2s_ics_43434_mems_microphone/tree/main/software/examples/i2s)
currently target Arduino ESP32-S3, so the C5 MicroPython wiring and script
still need a physical module test.

### Browser listening with either microphone wiring

For the original edge-pad wiring, use `mic_ics43434_usb_pads.py`: SCK=A2/GPIO4,
WS=A3/GPIO5, SD=D4/GPIO9, CH=GND, VSYS=3V3 and GND=GND. This uses the
same pins as `ics43434_mic.py`.

For the J20 connection, use `mic_ics43434_j20.py` to see the microphone's
24-bit peak in the serial console. Its pins are SCK/GPIO27, WS/GPIO26,
SD/GPIO7 and CH/GPIO10. The companion `mic_ics43434_usb_master.py` sends
48 kHz, 16-bit mono PCM on the native USB Serial/JTAG port. In the web code
editor, select the web audio example matching your wiring, connect the port and run the
script. The listening dialog opens; press **Escuchar** to start audio in the
browser. **Detener escucha** returns the board to the MicroPython prompt.
Chrome or Edge needs HTTPS or localhost for Web Serial. The browser receives
audio locally; it is not saved or uploaded. The signal indicator is based on
valid PCM frames and audible level, not an electrical identification of the
microphone. `pc_audio_pulsar.py` remains available for computer audio devices
and virtual microphone routing outside the browser.

## RFID & NFC R1–R8: PN532 V3 over I²C

These are the MicroPython scripts opened by the current C5 web lessons. The older
`labs/rfid-lab-r1` … `r8` are Arduino/RC522 examples for an ESP32-S3 and remain
as historical firmware; they are not the code used by these C5 lessons. The
old `door-app` and `friends-app` expect that older firmware and are not
compatible with the new R7/R8 scripts. R7 and R8 now offer local serial
exercises using JSON files on the C5.

With USB unplugged, set the PN532 V3 mode switches to **1 ON, 2 OFF (I²C)**.
Use its I²C pins labelled SDA and SCL, not the top SPI header. Wire VCC to
**3V3**, GND to GND, SDA to QWIIC GPIO1, and SCL to QWIIC GPIO6. The C5 QWIIC
bus has no on-board pull-ups; the PN532 board or wiring must provide them.
Power-cycle the module after moving its switches. MicroPython's 7-bit I²C
address is `0x24`; the NXP manual gives `0x48` as the 8-bit write address.
The PN532 V3 module manual specifies its I²C switch setting and a 3.3–5 V
module VCC range; this guide uses 3.3 V to match the C5.

Install `libraries/pn532.py` on the C5 as `pn532.py` before running any R script.
Reinstall it when updating R4 or R5 so the board has the matching driver.
If you replace the file manually during an active REPL session, run
`import sys; sys.modules.pop('pn532', None)` or reset the board before retrying.
The web editor clears the cached module after saving the library. R4 and R5
also accept the older driver's exchange API for a clearer authentication error.
The web editor's **Install pn532.py** action reads this exact file from this
repository. R1 checks the I²C link; R2 detects one card; R3 inventories cards
one at a time; R4 reads Classic block 4 or Type 2 pages 8–11; R5 writes only
Classic block 4 or Type 2 page 8 after you set `ARM_WRITE = True`, then reads
back. Use a disposable tag you own for R5. R6 measures hits at four distances.
If R4 or R5 reports `PN532 0x14`, the Classic sector 1 authentication failed.
The examples start with factory Key A (`FF FF FF FF FF FF`); edit `CLASSIC_KEY`
and `CLASSIC_KEY_TYPE` (`0x60` for A, `0x61` for B) to use a key you know for
that card, or test with a fresh card that still has its factory key. Hold the
card still during authentication. The UID alone cannot reveal a sector key.
R7 stores a simulated door allowlist in `rfid_r7_cards.json`; R8 stores the
UID-to-character mapping in `rfid_r8_friends.json`. A UID is copyable, so the
R7 exercise is not suitable for a real door.

Protocol reference: [NXP PN532 user manual](https://www.nxp.com/docs/en/user-guide/141520.pdf),
§6.2.4 (I²C frames), §7.2.2 (firmware version), §7.3.1 (passive retries),
§7.3.5 (passive target) and §7.3.8 (data exchange). Board reference:
[Elechouse PN532 V3 manual](https://www.elechouse.com/elechouse/images/product/PN532_module_V3/PN532_%20Manual_V3.pdf).
Host protocol tests: `python -m unittest discover -s examples/micropython/pulsar-c5/tests -p 'test_pn532.py'`.
The driver and scripts have not yet been tested with a physical PN532 V3 and C5.

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
