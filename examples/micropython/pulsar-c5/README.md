# MicroPython examples for UNIT Pulsar ESP32-C5

These editable scripts adapt MicroPython topics from the Pulsar C6 guide: GPIO,
ADC, I²C, NeoPixel, RGB mixing, an external LED circuit, OLED, SPI/microSD,
UART loopback, Wi-Fi, and Bluetooth LE. Pin assignments target Pulsar C5
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
| `rgb_led.py` | NeoPixel | WS2812 on GPIO0 (see note below) |
| `external_led.py` | First circuit | External LED on GPIO9 (D4) with PWM |
| `bmi270_id.py` | IMU on QWIIC / D1 | BMI270 on QWIIC GPIO1/6 (0x68 or 0x69) |
| `tmp235_temperature.py` | Analog temperature / D3 | TMP235 VOUT on A2 / GPIO4 |
| `oled.py` | Standalone OLED / O1 | SSD1306 only on QWIIC GPIO1/6 |
| `oled_light.py` | OLED light graph / D2 | SSD1306 and TEMT6000 DDP on QWIIC GPIO1/6 |
| `st7735_display.py` | TFT display / SPI | 0.96-inch ST7735 80x160: CS A1/GPIO1, DC A2/GPIO4, RES A3/GPIO5, MOSI D11/GPIO7, SCK D13/GPIO27 |
| `microsd.py` | SPI / SD / P5 | SCK27, MOSI7, MISO26, CS23 |
| `uart_loopback.py` | UART / P6 | TX0/GPIO11 bridged to RX0/GPIO12 |
| `wifi.py` | Communication / Wi-Fi | Editable network credentials |
| `ble_scan.py` | Communication / Bluetooth | BLE advertisement scan |
| `thingspeak_dht.py` | Wi-Fi to the cloud / I1 | DHT11 on A2 (GPIO4); 2.4 or 5 GHz; ThingSpeak key |
| `ble_touch.py` | Bluetooth LE touch / I2 | TTP223B on A3 (GPIO5); notifies `touched` / `released` |
| `fht40_temp_humidity.py` | Onboard sensors / FHT40 | Temperature and humidity at 0x44 on GPIO2/GPIO3 |
| `lps22hb_pressure.py` | Onboard sensors / LPS22HB | Pressure and temperature at 0x5C on GPIO2/GPIO3 |
| `vcnl4040_light_prox.py` | Onboard sensors / VCNL4040 | Proximity and ambient light at 0x60 on GPIO2/GPIO3 |
| `max17048_battery.py` | Onboard sensors / MAX17048 | LiPo voltage and charge at 0x36 on GPIO2/GPIO3 |
| `mmc5603_compass.py` | Onboard sensors / MMC5603 | Three magnetic axes at 0x30 on GPIO2/GPIO3, 20 Hz |

`oled.py` (Display lesson O1) uses only the SSD1306 screen; `oled_light.py` (Sensors lesson D2) additionally needs a TEMT6000 DDP sensor. Both scripts require UNIT's copy of the MicroPython `ssd1306.py` driver. Save
`libraries/ssd1306.py` as `ssd1306.py` on the board before running either example. The browser workspace includes an **Install ssd1306.py** action.
`microsd.py` uses UNIT's SPI `sdcard.py` driver. Save
`libraries/sdcard.py` as `sdcard.py` on the board before running the example.
The workspace includes an **Install sdcard.py** action. Both drivers are MIT
licensed; the license is in `libraries/LICENSE`. Upstream sources:
[UNIT SSD1306 driver](https://github.com/UNIT-Electronics-MX/unit_devlab_micropython_libraries/blob/main/software/ssd1306/ssd1306.py)
and [UNIT SD driver](https://github.com/UNIT-Electronics-MX/unit_devlab_micropython_libraries/blob/main/software/sdcard/sdcard.py).

`st7735_display.py` uses `libraries/st7735.py`, saved on the board as
`st7735.py`. The ST7735 TFT shares the SPI bus with the microSD card, so the
example holds the SD chip select (GPIO23) high. The TFT's SDA and SCL labels
are SPI data and clock, not I²C. The driver file does not state a license;
its source is [boochow's MicroPython-ST7735](https://github.com/boochow/MicroPython-ST7735),
which is based on GuyCarver's ST7735 driver.

The five onboard sensors use the sensor bus on GPIO2/GPIO3. Save each driver
from `libraries/` to the board under the same name before running its example:
`fht40.py`, `lps22hb.py`, `vcnl4040.py`, `max1704x.py` and `mmc5603.py`.
Each example checks the sensor address first and stops with an error if the
sensor does not answer. The drivers come from the
[UNIT DevLab MicroPython libraries](https://github.com/UNIT-Electronics-MX/unit_devlab_micropython_libraries),
which are MIT licensed. The VCNL4040 driver is a port of Adafruit's library, and
the MMC5603 and MAX17043/48 drivers are based on upstream projects. Their license
headers stay in the files.

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
