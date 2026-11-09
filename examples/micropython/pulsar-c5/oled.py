"""Pulsar C5: texto en una pantalla SSD1306 conectada al puerto QWIIC."""

from machine import I2C, Pin
from ssd1306 import SSD1306_I2C


# Copia ssd1306.py al dispositivo antes de ejecutar este ejemplo.
QWIIC_SDA = 1
QWIIC_SCL = 6
WIDTH = 128
HEIGHT = 64  # Cambia a 32 si tu pantalla es de 128 x 32.

i2c = I2C(0, sda=Pin(QWIIC_SDA), scl=Pin(QWIIC_SCL), freq=400000)
if 0x3C not in i2c.scan():
    raise OSError("No se encontro la pantalla SSD1306 en 0x3C")

display = SSD1306_I2C(WIDTH, HEIGHT, i2c, addr=0x3C)
display.fill(0)
display.text("Pulsar C5", 0, 0)
display.text("MicroPython", 0, 16)
display.show()
