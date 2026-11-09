"""Pulsar C5: busca los sensores integrados en el bus I2C."""

from machine import I2C, Pin
from time import sleep


# El bus de sensores usa GPIO2/GPIO3; QWIIC usa GPIO1/GPIO6.
i2c = I2C(0, sda=Pin(2), scl=Pin(3), freq=400000)
known = {
    0x30: "MMC5603 magnetometro",
    0x36: "MAX17048 bateria",
    0x44: "FHT40 temperatura/humedad",
    0x5C: "LPS22 presion",
    0x60: "VCNL4040 proximidad/luz",
}

while True:
    addresses = i2c.scan()
    for address in addresses:
        print("0x{:02X}  {}".format(address, known.get(address, "otro dispositivo")))
    print("{} dispositivo(s)\n".format(len(addresses)))
    sleep(5)
