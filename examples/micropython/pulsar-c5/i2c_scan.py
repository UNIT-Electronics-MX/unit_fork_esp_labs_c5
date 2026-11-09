"""Pulsar C5: scan the onboard sensor I2C bus."""

from machine import I2C, Pin
from time import sleep


# The sensor bus uses GPIO2/GPIO3; QWIIC uses GPIO1/GPIO6.
i2c = I2C(0, sda=Pin(2), scl=Pin(3), freq=400000)
known = {
    0x30: "MMC5603 magnetometer",
    0x36: "MAX17048 battery",
    0x44: "FHT40 temperature/humidity",
    0x5C: "LPS22 pressure",
    0x60: "VCNL4040 proximity/light",
}

while True:
    addresses = i2c.scan()
    for address in addresses:
        print("0x{:02X}  {}".format(address, known.get(address, "other device")))
    print("{} device(s)\n".format(len(addresses)))
    sleep(5)
