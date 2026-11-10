"""Pulsar C5: read proximity and ambient light from the onboard VCNL4040 sensor."""

from machine import I2C, Pin
from time import sleep_ms

from vcnl4040 import VCNL4040


# Install libraries/vcnl4040.py as vcnl4040.py on the board first.
# The VCNL4040 is onboard: it sits on the sensor bus, GPIO2/GPIO3.
VCNL4040_ADDRESS = 0x60
I2C_SDA_PIN = 2
I2C_SCL_PIN = 3

i2c = I2C(0, sda=Pin(I2C_SDA_PIN), scl=Pin(I2C_SCL_PIN), freq=400000)
if VCNL4040_ADDRESS not in i2c.scan():
    raise OSError("VCNL4040 not found at 0x60")

sensor = VCNL4040(i2c)
sensor.light_integration_time = VCNL4040.ALS_640MS

while True:
    print("Proximity: {}".format(sensor.proximity))
    print("Ambient light (lux): {:.2f}".format(sensor.lux))
    print(16 * "_")
    sleep_ms(500)
