# MicroPython examples for UNIT Pulsar ESP32-C5

These editable scripts adapt MicroPython topics from the Pulsar C6 guide: GPIO,
ADC, I²C, NeoPixel, an external LED circuit, OLED, SPI/microSD, Wi-Fi, and
Bluetooth LE. Pin assignments target Pulsar C5 V1.2.3 and match the C5 Arduino
examples in `unit_fork_esp_c5_labs_web/sketches/pulsar-c5/`.

Install the [ESP32-C5 MicroPython firmware](https://micropython.org/download/ESP32_GENERIC_C5/)
before running these scripts. Flash the complete `.bin` image at **`0x2000`**,
as specified on the download page. A `.py` file is a script: run it through the
REPL or save it to the MicroPython filesystem. Do not flash it as firmware.

Open an example in the browser editor, change its parameters, and connect the
serial console. You can run or stop the script, save it to the board, or download
it. You can also open your own `.py` file. Save a script as `main.py` on the board
to run it at startup. Use `Ctrl+C` to stop a running script.

| File | C6 guide topic | C5 adaptation |
| --- | --- | --- |
| `gpio.py` | GPIO | LED on GPIO27; BOOT button on GPIO28 |
| `adc.py` | ADC | A2 / GPIO4 |
| `i2c_scan.py` | I²C | Onboard sensor bus on GPIO2/3 |
| `rgb_led.py` | NeoPixel | WS2812 on GPIO0 (see note below) |
| `external_led.py` | First circuit | External LED on GPIO9 (D4) with PWM |
| `oled.py` | I²C / OLED | QWIIC on GPIO1/6 |
| `microsd.py` | SPI / SD | SCK27, MOSI7, MISO26, CS23 |
| `wifi.py` | Communication / Wi-Fi | Editable network credentials |
| `ble_scan.py` | Communication / Bluetooth | BLE advertisement scan |

`oled.py` requires `ssd1306.py`, and `microsd.py` requires `sdcard.py`, on the
board. The libraries are linked from the [C6 guide](https://github.com/UNIT-Electronics-MX/unit_devlab_esp32_examples/tree/main/docs/pulsar-c6-getting-started).
The ADC example uses 11 dB attenuation. Its millivolt reading is approximate
and may saturate near 3.3 V. Never exceed 3.3 V on A2.

The C5 schematic connects the WS2812 to GPIO0, while one UNIT illustration
labels it GPIO8. If `rgb_led.py` does not light the LED, try `RGB_PIN = 8` and
check your board revision.

These examples passed a Python syntax check. Their hardware behavior still
needs testing on a Pulsar C5 and its peripherals.
