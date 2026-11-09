"""Pulsar C5: mount the onboard microSD card and write a line."""

import os
from machine import Pin, SPI
from sdcard import SDCard


# Copy sdcard.py to the board before running this example.
# SCK shares GPIO27 with the user LED.
spi = SPI(1, baudrate=1_000_000, polarity=0, phase=0,
          sck=Pin(27), mosi=Pin(7), miso=Pin(26))
card = SDCard(spi, Pin(23, Pin.OUT))

os.mount(card, "/sd")
try:
    with open("/sd/pulsar.txt", "a") as log:
        log.write("Pulsar C5 with MicroPython\n")
    with open("/sd/pulsar.txt") as log:
        print(log.read())
    print("Files:", os.listdir("/sd"))
finally:
    os.umount("/sd")
