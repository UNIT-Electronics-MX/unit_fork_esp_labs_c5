# IMPORTANT: Copy this entire code to your MicroPython microcontroller.
# Save it as a file named exactly "fht40.py" in the root directory or lib folder.

"""
MicroPython driver for the FHT40 temperature and humidity sensor over I2C.

The FHT40 uses the same command set and measurement format as the Sensirion SHT4x
family (address 0x44, high-repeatability measurement command 0xFD, CRC-8 with
polynomial 0x31 and initial value 0xFF).
"""
import time

from micropython import const

FHT40_ADDRESS = const(0x44)

_CMD_MEASURE_HIGH = const(0xFD)   # high repeatability, T and RH
_CMD_SOFT_RESET = const(0x94)
_MEASURE_WAIT_MS = const(10)      # datasheet maximum is about 8.3 ms
_SOFT_RESET_WAIT_MS = const(2)


def _crc8(data: bytes) -> int:
    """CRC-8, polynomial 0x31, initial value 0xFF."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


class FHT40:
    """Driver for the FHT40 temperature and humidity sensor.

    :param machine.I2C i2c: the I2C bus the sensor is connected to.
    :param int address: the I2C address. Defaults to 0x44.
    :raises ValueError: if the sensor does not respond or a CRC check fails.
    """

    def __init__(self, i2c, address: int = FHT40_ADDRESS):
        self.i2c = i2c
        self._address = address
        self._temperature = None
        self._humidity = None
        if self._address not in self.i2c.scan():
            raise ValueError("FHT40 sensor not found on I2C bus.")

    def soft_reset(self):
        """Resets the sensor."""
        self.i2c.writeto(self._address, bytes([_CMD_SOFT_RESET]))
        time.sleep_ms(_SOFT_RESET_WAIT_MS)

    def measure(self):
        """Starts a measurement and stores the temperature and humidity.

        :raises ValueError: if a CRC check fails.
        """
        self.i2c.writeto(self._address, bytes([_CMD_MEASURE_HIGH]))
        time.sleep_ms(_MEASURE_WAIT_MS)
        buf = self.i2c.readfrom(self._address, 6)

        t_raw, t_crc = buf[0:2], buf[2]
        h_raw, h_crc = buf[3:5], buf[5]
        if _crc8(t_raw) != t_crc or _crc8(h_raw) != h_crc:
            raise ValueError("FHT40 CRC error.")

        t_val = (t_raw[0] << 8) | t_raw[1]
        h_val = (h_raw[0] << 8) | h_raw[1]
        self._temperature = -45.0 + 175.0 * t_val / 65535.0
        humidity = -6.0 + 125.0 * h_val / 65535.0
        self._humidity = min(100.0, max(0.0, humidity))

    @property
    def temperature(self) -> float:
        """Temperature in degrees Celsius from the last measure() call."""
        return self._temperature

    @property
    def humidity(self) -> float:
        """Relative humidity in percent from the last measure() call."""
        return self._humidity
