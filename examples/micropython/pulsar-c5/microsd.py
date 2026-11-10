"""P5: use UNIT's SPI SD card driver to append a line on the Pulsar C5."""

import os
from machine import Pin, SPI
from sdcard import SDCard
from time import ticks_ms

# First install libraries/sdcard.py as sdcard.py on the board.
# SCK shares GPIO27 with the user LED. SPI(1) is the ESP32-C5's SPI2 bus.
spi = SPI(1, baudrate=100_000, polarity=0, phase=0,
          sck=Pin(27), mosi=Pin(7), miso=Pin(26))
try:
    card = SDCard(spi, Pin(23, Pin.OUT, value=1), baudrate=1_320_000)
    os.mount(card, "/sd")
    try:
        with open("/sd/pulsar.txt", "a") as log:
            log.write("run at {} ms\n".format(ticks_ms()))
        with open("/sd/pulsar.txt") as log:
            print(log.read())
        print("Files:", os.listdir("/sd"))
    finally:
        os.umount("/sd")
finally:
    spi.deinit()
