# IMPORTANT: Copy this entire code to your MicroPython microcontroller.
# Save it as a file named exactly "lps22hb.py" in the root directory or lib folder.

"""
MicroPython driver for the LPS22HB MEMS pressure sensor (barometer) over I2C.

Address 0x5C (SA0 low) or 0x5D (SA0 high). Pressure in hPa, temperature in degrees Celsius.
"""
import time

from micropython import const

LPS22HB_ADDRESS = const(0x5C)

_REG_WHO_AM_I = const(0x0F)
_REG_CTRL_REG1 = const(0x10)
_REG_CTRL_REG2 = const(0x11)
_REG_PRESS_OUT_XL = const(0x28)   # pressure (3 bytes) followed by temperature (2 bytes)

_WHO_AM_I_VALUE = const(0xB1)
_CTRL_REG2_SWRESET = const(0x04)  # bit 2
_CTRL_REG2_ONE_SHOT = const(0x01)  # bit 0
_CTRL_REG1_ODR_1HZ = const(0x10)   # ODR[2:0] = 001 in bits 6:4
_CTRL_REG1_BDU = const(0x02)       # block data update (bit 1)

_PRESSURE_LSB_PER_HPA = 4096.0
_TEMPERATURE_LSB_PER_C = 100.0


class LPS22HB:
    """Driver for the LPS22HB pressure sensor.

    :param machine.I2C i2c: the I2C bus the sensor is connected to.
    :param int address: the I2C address. Defaults to 0x5C.
    :param int odr: output data rate, 1 Hz (default) by continuous mode. Use 0 for power-down (one-shot mode).
    :raises ValueError: if the sensor is not found or the WHO_AM_I value is wrong.
    """

    def __init__(self, i2c, address: int = LPS22HB_ADDRESS):
        self.i2c = i2c
        self._address = address
        if self._address not in self.i2c.scan():
            raise ValueError("LPS22HB sensor not found on I2C bus.")
        if self._read(_REG_WHO_AM_I, 1)[0] != _WHO_AM_I_VALUE:
            raise ValueError("LPS22HB WHO_AM_I value is wrong.")
        # 1 Hz continuous measurement, block data update enabled
        self._write(_REG_CTRL_REG1, bytes([_CTRL_REG1_ODR_1HZ | _CTRL_REG1_BDU]))

    def soft_reset(self):
        """Resets the sensor registers to their default values."""
        self._write(_REG_CTRL_REG2, bytes([_CTRL_REG2_SWRESET]))
        time.sleep_ms(5)

    def _read(self, register: int, count: int) -> bytes:
        # bit 7 of the register address enables auto-increment for multi-byte reads
        if count > 1:
            register |= 0x80
        return self.i2c.readfrom_mem(self._address, register, count)

    def _write(self, register: int, buf: bytes):
        self.i2c.writeto_mem(self._address, register, buf)

    def _read_raw(self):
        buf = self._read(_REG_PRESS_OUT_XL, 5)
        press_raw = buf[0] | (buf[1] << 8) | (buf[2] << 16)
        temp_raw = buf[3] | (buf[4] << 8)
        if temp_raw & 0x8000:
            temp_raw -= 0x10000
        return press_raw, temp_raw

    @property
    def pressure(self) -> float:
        """Pressure in hPa."""
        press_raw, _ = self._read_raw()
        return press_raw / _PRESSURE_LSB_PER_HPA

    @property
    def temperature(self) -> float:
        """Temperature in degrees Celsius."""
        _, temp_raw = self._read_raw()
        return temp_raw / _TEMPERATURE_LSB_PER_C
