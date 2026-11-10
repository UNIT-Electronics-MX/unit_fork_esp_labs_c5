"""Pulsar C5: read temperature and humidity from the onboard FHT40 sensor."""

from machine import I2C, Pin
from time import sleep_ms

from fht40 import FHT40


# Install libraries/fht40.py as fht40.py on the board first.
# The FHT40 is onboard: it sits on the sensor bus, GPIO2/GPIO3.
FHT40_ADDRESS = 0x44
I2C_SDA_PIN = 2
I2C_SCL_PIN = 3

i2c = I2C(0, sda=Pin(I2C_SDA_PIN), scl=Pin(I2C_SCL_PIN), freq=400000)
if FHT40_ADDRESS not in i2c.scan():
    raise OSError("FHT40 not found at 0x44")

sensor = FHT40(i2c)

while True:
    sensor.measure()
    print("Temperature (C): {:.2f}".format(sensor.temperature))
    print("Humidity (%): {:.2f}".format(sensor.humidity))
    print(16 * "_")
    sleep_ms(2000)
