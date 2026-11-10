"""Pulsar C5: scan the QWIIC bus and read the BMI270 chip ID, like the D1 Arduino sketch."""

from machine import I2C, Pin


# QWIIC: SDA on GPIO1 (A1), SCL on GPIO6 (A6). The Pulsar C5 has no pull-ups on QWIIC:
# the sensor board must carry its own.
QWIIC_SDA = 1
QWIIC_SCL = 6
BMI270_ADDRESSES = (0x68, 0x69)  # 0x68 by default, 0x69 when SDO is tied high
CHIP_ID_REG = 0x00
BMI270_CHIP_ID = 0x24

i2c = I2C(0, sda=Pin(QWIIC_SDA), scl=Pin(QWIIC_SCL), freq=100_000)

found = i2c.scan()
for address in found:
    print("found 0x{:02X}".format(address))
if not found:
    print("Nothing answered on QWIIC: check the cable and the pull-ups")

for address in BMI270_ADDRESSES:
    if address not in found:
        continue
    # readfrom_mem writes the register number, then reads with a repeated start.
    chip_id = i2c.readfrom_mem(address, CHIP_ID_REG, 1)[0]
    verdict = "(BMI270)" if chip_id == BMI270_CHIP_ID else "(not a BMI270)"
    print("0x{:02X} chip ID 0x{:02X} {}".format(address, chip_id, verdict))
    break
else:
    print("No BMI270 at 0x68 or 0x69: check the QWIIC cable and the pull-ups")
