"""Pulsar C5: show text on an SSD1306 display connected to QWIIC."""

from machine import I2C, Pin
from ssd1306 import SSD1306_I2C


# Copy ssd1306.py to the board before running this example.
QWIIC_SDA = 1
QWIIC_SCL = 6
WIDTH = 128
HEIGHT = 64  # Use 32 for a 128 x 32 display.

i2c = I2C(0, sda=Pin(QWIIC_SDA), scl=Pin(QWIIC_SCL), freq=400000)
if 0x3C not in i2c.scan():
    raise OSError("SSD1306 display not found at 0x3C")

display = SSD1306_I2C(WIDTH, HEIGHT, i2c, addr=0x3C)
display.fill(0)
display.text("Pulsar C5", 0, 0)
display.text("MicroPython", 0, 16)
display.show()
