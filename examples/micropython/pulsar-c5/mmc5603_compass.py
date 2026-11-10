"""Pulsar C5: read the three magnetic axes from the onboard MMC5603 sensor at 20 Hz."""

from machine import I2C, Pin
from time import sleep_ms

from mmc5603 import MMC5603, I2cAdapter


# Install libraries/mmc5603.py as mmc5603.py on the board first.
# The MMC5603 is onboard: it sits on the sensor bus, GPIO2/GPIO3.
MMC5603_ADDRESS = 0x30
I2C_SDA_PIN = 2
I2C_SCL_PIN = 3

i2c = I2C(0, sda=Pin(I2C_SDA_PIN), scl=Pin(I2C_SCL_PIN), freq=400000)
if MMC5603_ADDRESS not in i2c.scan():
    raise OSError("MMC5603 not found at 0x30")

sensor = MMC5603(I2cAdapter(i2c))
print("Sensor id: {}".format(hex(sensor.get_id())))

# Continuous measurement at 20 Hz.
sensor.set_update_rate(20)
sensor.start_measure(continuous_mode=True, auto_set_reset=True)

while True:
    if sensor.is_data_ready():
        x, y, z = sensor.get_axis(-1)
        print("X: {}; Y: {}; Z: {}".format(x, y, z))
    sleep_ms(100)
