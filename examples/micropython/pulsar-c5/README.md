# Ejemplos MicroPython para UNIT Pulsar ESP32-C5

Estos archivos son versiones editables de los temas MicroPython de la guía
Pulsar C6: GPIO, ADC, I²C, NeoPixel, primer circuito con LED externo, OLED, SPI/microSD, Wi-Fi y Bluetooth LE.
Los pines se adaptaron a la Pulsar C5 V1.2.3 y coinciden con los ejemplos
Arduino C5 de `unit_fork_esp_c5_labs_web/sketches/pulsar-c5/`.

La [versión oficial de MicroPython para ESP32-C5](https://micropython.org/download/ESP32_GENERIC_C5/)
es necesaria antes de ejecutar estos scripts. Su archivo `.bin` completo se
graba en **`0x2000`**, según las instrucciones de la página de descarga. Un
archivo `.py` se copia al sistema de archivos de MicroPython o se ejecuta en
su REPL; no se graba como imagen de firmware.

Abre un archivo en el editor de la página, cambia los parámetros y conecta la
consola serie. Puedes ejecutarlo por el REPL, detenerlo, guardarlo en la placa
o descargarlo. También puedes abrir tu propio `.py`. Para que arranque
automáticamente, guárdalo como `main.py` en la placa. Detén un programa con
`Ctrl+C`.

| Archivo | Tema de la guía C6 | Adaptación C5 |
| --- | --- | --- |
| `gpio.py` | GPIO | LED GPIO27 y BOOT GPIO28 |
| `adc.py` | ADC | A2 / GPIO4 |
| `i2c_scan.py` | I²C | bus de sensores GPIO2/3 |
| `rgb_led.py` | NeoPixel | WS2812 GPIO0 (ver nota) |
| `external_led.py` | primer circuito | LED externo en GPIO9 (D4) con PWM |
| `oled.py` | I²C / OLED | QWIIC GPIO1/6 |
| `microsd.py` | SPI / SD | SCK27, MOSI7, MISO26, CS23 |
| `wifi.py` | comunicación / Wi-Fi | credenciales editables |
| `ble_scan.py` | comunicación / Bluetooth | escaneo BLE |

`oled.py` necesita `ssd1306.py` y `microsd.py` necesita `sdcard.py` en la placa.
Las librerías están enlazadas en la [guía C6](https://github.com/UNIT-Electronics-MX/unit_devlab_esp32_examples/tree/main/docs/pulsar-c6-getting-started).
La lectura ADC usa 11 dB de atenuación; cerca de 3.3 V puede saturarse y la
medida en milivoltios es aproximada. Nunca excedas 3.3 V en A2.

El esquema C5 conecta el WS2812 a GPIO0, mientras que una ilustración de UNIT
lo marca GPIO8. Si `rgb_led.py` no ilumina el LED, prueba `RGB_PIN = 8` y
verifica qué revisión de placa tienes.

Estos ejemplos se verificaron sintácticamente. Las funciones de hardware
requieren una prueba con la Pulsar C5 y sus periféricos.
