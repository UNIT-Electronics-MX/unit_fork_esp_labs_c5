"""Pulsar C5: stream BMI270 orientation for a 3D cube (the web IMU cube or a Processing sketch).

Install bmi270.py on the board before running this example. Older copies of the
driver work too, but the updated driver reads all six axes in one I2C transfer.

Every 20 ms the board prints one comma-separated line:

    IMU,roll,pitch,yaw,ax,ay,az,gx,gy,gz

roll, pitch and yaw in degrees; acceleration in m/s²; angular velocity in °/s.
Send one character back over the same serial port:

    c  keep the sensor still: measure the gyroscope bias again  -> ACK,c,bx,by,bz
    z  make the current heading yaw 0                           -> ACK,z

Roll and pitch mix the gyroscope with gravity (a complementary filter), so they do not drift.
Yaw only integrates the gyroscope, so it drifts slowly: press z to zero it.
"""

import math
import select
import sys
from machine import I2C, Pin
from time import sleep_ms, ticks_diff, ticks_ms
from bmi270 import BMI270

PERIOD_MS = 20      # 50 lines per second
ALPHA = 0.99        # same gravity correction per second as 0.98 at 25 Hz
CAL_SAMPLES = 100


# QWIIC uses GPIO1/GPIO6. The sensor board must provide I2C pull-ups.
i2c = I2C(0, sda=Pin(1), scl=Pin(6), freq=400_000)
found = i2c.scan()
if 0x68 in found:
    address = 0x68
elif 0x69 in found:
    address = 0x69
else:
    raise OSError("No BMI270 at 0x68 or 0x69 on QWIIC")
sensor = BMI270(i2c, address=address)
fast_read_motion = getattr(sensor, "read_motion", None)


def read_motion():
    """Use the burst read when installed, or the older driver's two properties."""
    if fast_read_motion is not None:
        return fast_read_motion()
    return sensor.acceleration, sensor.gyro


def tilt(ax, ay, az):
    """Roll and pitch (degrees) from gravity alone."""
    roll = math.degrees(math.atan2(ay, az))
    pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
    return roll, pitch


def wrap(angle):
    """Keep an angle in -180..180 degrees."""
    return (angle + 180.0) % 360.0 - 180.0


def gyro_bias():
    """Average the gyroscope while the sensor is still."""
    sx = sy = sz = 0.0
    for _ in range(CAL_SAMPLES):
        _, (gx, gy, gz) = read_motion()
        sx += gx
        sy += gy
        sz += gz
        sleep_ms(5)
    return sx / CAL_SAMPLES, sy / CAL_SAMPLES, sz / CAL_SAMPLES


def report_bias(bias):
    print("ACK,c,{:.2f},{:.2f},{:.2f}".format(*bias))


# Characters sent by the web page or Processing arrive on stdin while this loop runs.
commands = select.poll()
commands.register(sys.stdin, select.POLLIN)

print("BMI270 ready at 0x{:02X}: keep it still".format(address))
bias = gyro_bias()
report_bias(bias)
initial_accel, _ = read_motion()
roll, pitch = tilt(*initial_accel)
yaw = 0.0
last = ticks_ms()

while True:
    while commands.poll(0):
        command = sys.stdin.read(1)
        if command == "c":
            bias = gyro_bias()
            report_bias(bias)
        elif command == "z":
            yaw = 0.0
            print("ACK,z")

    now = ticks_ms()
    dt = ticks_diff(now, last) / 1000
    last = now

    (ax, ay, az), (gx, gy, gz) = read_motion()
    gx -= bias[0]
    gy -= bias[1]
    gz -= bias[2]

    accel_roll, accel_pitch = tilt(ax, ay, az)
    # Near ±180° the gravity roll jumps sides: follow it by the short way round.
    roll = wrap(ALPHA * (roll + gx * dt) + (1 - ALPHA) * (roll + wrap(accel_roll - roll)))
    pitch = ALPHA * (pitch + gy * dt) + (1 - ALPHA) * accel_pitch
    yaw = wrap(yaw + gz * dt)

    print("IMU,{:.1f},{:.1f},{:.1f},{:.2f},{:.2f},{:.2f},{:.1f},{:.1f},{:.1f}".format(
        roll, pitch, yaw, ax, ay, az, gx, gy, gz
    ))
    sleep_ms(max(0, PERIOD_MS - ticks_diff(ticks_ms(), now)))
