"""Pulsar C5: monta la microSD integrada y escribe una linea."""

import os
from machine import Pin, SPI
from sdcard import SDCard


# Copia sdcard.py al dispositivo antes de ejecutar este ejemplo.
# SCK comparte GPIO27 con el LED de usuario.
spi = SPI(1, baudrate=1_000_000, polarity=0, phase=0,
          sck=Pin(27), mosi=Pin(7), miso=Pin(26))
card = SDCard(spi, Pin(23, Pin.OUT))

os.mount(card, "/sd")
try:
    with open("/sd/pulsar.txt", "a") as log:
        log.write("Pulsar C5 con MicroPython\n")
    with open("/sd/pulsar.txt") as log:
        print(log.read())
    print("Archivos:", os.listdir("/sd"))
finally:
    os.umount("/sd")
