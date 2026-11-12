"""Pulsar C5: read acceleration and angular velocity from a BMI270 on QWIIC.

Install bmi270.py on the board before running this example.
"""

from machine import I2C, Pin
from time import sleep
from bmi270 import BMI270


# QWIIC uses GPIO1/GPIO6. The sensor board must provide I2C pull-ups.
i2c = I2C(0, sda=Pin(1), scl=Pin(6), freq=100_000)
found = i2c.scan()
if 0x68 in found:
    address = 0x68
elif 0x69 in found:
    address = 0x69
else:
    raise OSError("No BMI270 at 0x68 or 0x69 on QWIIC")

sensor = BMI270(i2c, address=address)
print("BMI270 ready at 0x{:02X}".format(address))

while True:
    ax, ay, az = sensor.acceleration  # m/s²
    gx, gy, gz = sensor.gyro          # degrees/s
    print("acc {:.2f} {:.2f} {:.2f} m/s² | gyro {:.2f} {:.2f} {:.2f} °/s".format(
        ax, ay, az, gx, gy, gz
    ))
    sleep(0.2)
