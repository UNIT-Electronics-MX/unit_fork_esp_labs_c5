"""Pulsar C5: read LiPo cell voltage and charge from the onboard MAX17048 gauge."""

from machine import I2C, Pin
from time import sleep_ms

from max1704x import max1704x


# Install libraries/max1704x.py as max1704x.py on the board first.
# The MAX17048 is onboard: it sits on the sensor bus, GPIO2/GPIO3.
MAX17048_ADDRESS = 0x36
I2C_SDA_PIN = 2
I2C_SCL_PIN = 3

i2c = I2C(0, sda=Pin(I2C_SDA_PIN), scl=Pin(I2C_SCL_PIN), freq=400000)
if MAX17048_ADDRESS not in i2c.scan():
    raise OSError("MAX17048 not found at 0x36")

battery = max1704x(i2c, is_max17048=True)
print("I2C address: 0x{:02X}".format(battery.address()))
print("Version: 0x{:04X}".format(battery.getVersion()))

while True:
    print("Cell voltage (V): {:.3f}".format(battery.getVCell()))
    print("State of charge (%): {:.1f}".format(battery.getSoc()))
    print(16 * "_")
    sleep_ms(2000)
