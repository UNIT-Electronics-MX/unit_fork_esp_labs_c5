"""Pulsar C5: read pressure and temperature from the onboard LPS22HB sensor."""

from machine import I2C, Pin
from time import sleep_ms

from lps22hb import LPS22HB


# Install libraries/lps22hb.py as lps22hb.py on the board first.
# The LPS22HB is onboard: it sits on the sensor bus, GPIO2/GPIO3.
LPS22HB_ADDRESS = 0x5C
I2C_SDA_PIN = 2
I2C_SCL_PIN = 3

i2c = I2C(0, sda=Pin(I2C_SDA_PIN), scl=Pin(I2C_SCL_PIN), freq=400000)
if LPS22HB_ADDRESS not in i2c.scan():
    raise OSError("LPS22HB not found at 0x5C")

sensor = LPS22HB(i2c)

while True:
    print("Pressure (hPa): {:.2f}".format(sensor.pressure))
    print("Temperature (C): {:.2f}".format(sensor.temperature))
    print(16 * "_")
    sleep_ms(2000)
