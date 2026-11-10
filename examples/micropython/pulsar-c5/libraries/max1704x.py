# IMPORTANT: Copy this entire code to your MicroPython microcontroller.
# Save it as a file named exactly "max1704x.py" in the root directory or lib folder.

"""
MicroPython driver for the MAX17043 / MAX17044 / MAX17048 LiPo fuel gauge over I2C.

Original author: Andre Peeters (2017/10/31) - https://github.com/andrethemac/max17043.py/tree/master
Modified by: Cesar (@Cesar)

Changes in this version:
- The I2C bus is passed in by the user (like the other drivers in this collection).
- Module-level register constants (the original used undefined names inside methods).
- VCELL conversion for the MAX17048 (16-bit, 78.125 uV per count) and the MAX17043 (12-bit, 1.25 mV per count).

MIT License - see the original repository for the license terms.
"""
import binascii

from micropython import const

MAX1704X_ADDRESS = const(0x36)

REGISTER_VCELL = const(0x02)
REGISTER_SOC = const(0x04)
REGISTER_MODE = const(0x06)
REGISTER_VERSION = const(0x08)
REGISTER_CONFIG = const(0x0C)
REGISTER_COMMAND = const(0xFE)


class max1704x:
    """Driver for the MAX17043/MAX17044/MAX17048 fuel gauge.

    :param machine.I2C i2c: the I2C bus the sensor is connected to.
    :param int address: the I2C address. Defaults to 0x36.
    :param bool is_max17048: True for the MAX17048 (default), False for the MAX17043/MAX17044.
    :raises ValueError: if the sensor does not respond on the bus.
    """

    def __init__(self, i2c, address: int = MAX1704X_ADDRESS, is_max17048: bool = True):
        self.i2c = i2c
        self._address = address
        self.is_max17048 = is_max17048

        if not self.sensor_exists():
            raise ValueError("MAX1704X sensor not found on I2C bus.")

    def __str__(self):
        """String representation of the values."""
        rs = "The I2C address is {}\n".format(self._address)
        rs += "The version is {}\n".format(self.getVersion())
        rs += "VCell is {} V\n".format(self.getVCell())
        rs += "State of charge is {} %\n".format(self.getSoc())
        rs += "Compensate value is {}\n".format(self.getCompensateValue())
        rs += "The alert threshold is {} %\n".format(self.getAlertThreshold())
        rs += "Is it in alert? {}\n".format(self.inAlert())
        return rs

    def sensor_exists(self) -> bool:
        """Returns True if the sensor answers at its I2C address, False otherwise."""
        try:
            self.i2c.readfrom_mem(self._address, REGISTER_VERSION, 2)
            return True
        except OSError:
            return False

    def address(self) -> int:
        """Returns the I2C address."""
        return self._address

    def reset(self):
        """Resets the sensor."""
        self.__writeRegister(REGISTER_COMMAND, binascii.unhexlify('0054'))

    def getVCell(self) -> float:
        """Returns the cell voltage in volts."""
        buf = self.__readRegister(REGISTER_VCELL)
        if self.is_max17048:
            raw = (buf[0] << 8) | buf[1]            # 16 bits
            return raw * 78.125e-6                  # 78.125 uV per count
        raw = (buf[0] << 4) | (buf[1] >> 4)         # 12 bits
        return raw * 1.25e-3                        # 1.25 mV per count

    def getSoc(self) -> float:
        """Returns the state of charge in percent."""
        buf = self.__readRegister(REGISTER_SOC)
        return buf[0] + (buf[1] / 256.0)

    def getVersion(self) -> int:
        """Returns the version of the fuel gauge chip."""
        buf = self.__readRegister(REGISTER_VERSION)
        return (buf[0] << 8) | buf[1]

    def getCompensateValue(self) -> int:
        """Returns the compensation value."""
        return self.__readConfigRegister()[0]

    def getAlertThreshold(self) -> int:
        """Returns the alert threshold in percent."""
        return 32 - (self.__readConfigRegister()[1] & 0x1F)

    def setAlertThreshold(self, threshold: int):
        """Sets the alert threshold in percent (1 to 32)."""
        value = 32 - threshold if threshold < 32 else 32
        buf = bytearray(self.__readConfigRegister())
        buf[1] = (buf[1] & 0xE0) | value
        self.__writeConfigRegister(buf)

    def inAlert(self) -> bool:
        """Returns True if the sensor is in alert."""
        return bool((self.__readConfigRegister()[1]) & 0x20)

    def clearAlert(self):
        """Clears the alert flag."""
        self.__readConfigRegister()

    def quickStart(self):
        """Performs a quick start (restarts the SOC estimation)."""
        self.__writeRegister(REGISTER_MODE, binascii.unhexlify('4000'))

    def __readRegister(self, register: int) -> bytes:
        """Reads a 2-byte register."""
        return self.i2c.readfrom_mem(self._address, register, 2)

    def __readConfigRegister(self) -> bytes:
        """Reads the configuration register."""
        return self.__readRegister(REGISTER_CONFIG)

    def __writeRegister(self, register: int, buf: bytes):
        """Writes buf to a register."""
        self.i2c.writeto_mem(self._address, register, buf)

    def __writeConfigRegister(self, buf: bytes):
        """Writes buf to the configuration register."""
        self.__writeRegister(REGISTER_CONFIG, buf)
