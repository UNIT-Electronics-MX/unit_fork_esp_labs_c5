"""R1: verify the PN532 V3 I2C connection, without a card.

Install pn532.py first. Switch 1 ON, switch 2 OFF; power from 3V3.
PN532 SDA -> QWIIC GPIO1, SCL -> QWIIC GPIO6, GND -> GND.
"""
from machine import I2C, Pin
from time import sleep_ms
from pn532 import ADDRESS, PN532

bus = I2C(0, sda=Pin(1), scl=Pin(6), freq=100_000)
print("I2C devices:", ["0x{:02X}".format(x) for x in bus.scan()])
reader = PN532(bus)
for count in range(3):
    ic, major, minor, support = reader.firmware_version()
    print("PN532 IC=0x{:02X} firmware={}.{} support=0x{:02X} ({}/3)".format(
        ic, major, minor, support, count + 1))
    sleep_ms(200)
reader.prepare()
print("I2C 0x{:02X} ready; open R2 to detect a card.".format(ADDRESS))
