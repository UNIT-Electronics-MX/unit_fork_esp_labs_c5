# IMPORTANT: Copy this entire code to your MicroPython microcontroller.
# Save it as a file named exactly "vcnl4040.py" in the root directory or lib folder.

# SPDX-FileCopyrightText: 2019 Kattni Rembor for Adafruit Industries
#
# SPDX-License-Identifier: MIT

"""
`vcnl4040.py`
================================================================================

MicroPython driver for the VCNL4040 proximity and ambient light sensor over I2C.

Ported from the CircuitPython library `adafruit_vcnl4040`
(https://github.com/adafruit/Adafruit_CircuitPython_VCNL4040). The register-bit
accessors that Adafruit built with adafruit_register are implemented here with plain
MicroPython I2C calls, so the library has no dependencies besides `machine`.

* Author(s) of the original library: Kattni Rembor
"""

import time

from micropython import const

VCNL4040_ADDRESS = const(0x60)
_DEVICE_ID_VALUE = const(0x186)

# Registers (16 bits, little-endian on the bus)
_REG_ALS_CONF = const(0x00)
_REG_ALS_THDH = const(0x01)
_REG_ALS_THDL = const(0x02)
_REG_PS_CONF1_2 = const(0x03)
_REG_PS_CONF3_MS = const(0x04)
_REG_PS_THDL = const(0x06)
_REG_PS_THDH = const(0x07)
_REG_PS_DATA = const(0x08)
_REG_ALS_DATA = const(0x09)
_REG_WHITE_DATA = const(0x0A)
_REG_INT_FLAG = const(0x0B)
_REG_ID = const(0x0C)


class VCNL4040:
    """Driver for the VCNL4040 proximity and ambient light sensor.

    :param machine.I2C i2c: the I2C bus the VCNL4040 is connected to.
    :param int address: the I2C address. Defaults to 0x60.
    :raises RuntimeError: if the sensor is not found.
    """

    # Ambient light sensor integration times
    ALS_80MS = const(0x0)
    ALS_160MS = const(0x1)
    ALS_320MS = const(0x2)
    ALS_640MS = const(0x3)

    # Proximity sensor integration times
    PS_1T = const(0x0)
    PS_1_5T = const(0x1)
    PS_2T = const(0x2)
    PS_2_5T = const(0x3)
    PS_3T = const(0x4)
    PS_3_5T = const(0x5)
    PS_4T = const(0x6)
    PS_8T = const(0x7)

    # LED current settings
    LED_50MA = const(0x0)
    LED_75MA = const(0x1)
    LED_100MA = const(0x2)
    LED_120MA = const(0x3)
    LED_140MA = const(0x4)
    LED_160MA = const(0x5)
    LED_180MA = const(0x6)
    LED_200MA = const(0x7)

    # LED duty cycle settings
    LED_1_40 = const(0x0)
    LED_1_80 = const(0x1)
    LED_1_160 = const(0x2)
    LED_1_320 = const(0x3)

    # Proximity sensor interrupt enable/disable options
    PS_INT_DISABLE = const(0x0)
    PS_INT_CLOSE = const(0x1)
    PS_INT_AWAY = const(0x2)
    PS_INT_CLOSE_AWAY = const(0x3)

    # Offsets into the interrupt status register for the different types
    ALS_IF_L = const(0x0D)
    ALS_IF_H = const(0x0C)
    PS_IF_CLOSE = const(0x09)
    PS_IF_AWAY = const(0x08)

    def __init__(self, i2c, address: int = VCNL4040_ADDRESS):
        self.i2c = i2c
        self._address = address
        if self._address not in self.i2c.scan():
            raise RuntimeError("VCNL4040 not found on I2C bus - check wiring!")
        if self._read16(_REG_ID) != _DEVICE_ID_VALUE:
            raise RuntimeError("Failed to find VCNL4040 - check wiring!")

        self._cached_interrupt_state = {
            self.ALS_IF_L: False,
            self.ALS_IF_H: False,
            self.PS_IF_CLOSE: False,
            self.PS_IF_AWAY: False,
        }

        self.proximity_shutdown = False
        self.light_shutdown = False
        self.white_shutdown = False

    # ------------------------------------------------------------------
    # Low-level register access
    # ------------------------------------------------------------------
    def _read16(self, reg: int) -> int:
        buf = self.i2c.readfrom_mem(self._address, reg, 2)
        return buf[0] | (buf[1] << 8)

    def _write16(self, reg: int, value: int):
        self.i2c.writeto_mem(self._address, reg, bytes((value & 0xFF, (value >> 8) & 0xFF)))

    def _get_bits(self, reg: int, shift: int, width: int) -> int:
        return (self._read16(reg) >> shift) & ((1 << width) - 1)

    def _set_bits(self, reg: int, shift: int, width: int, value: int):
        mask = ((1 << width) - 1) << shift
        current = self._read16(reg)
        self._write16(reg, (current & ~mask) | ((value << shift) & mask))

    def _get_bit(self, reg: int, bit: int) -> bool:
        return bool(self._get_bits(reg, bit, 1))

    def _set_bit(self, reg: int, bit: int, value: bool):
        self._set_bits(reg, bit, 1, 1 if value else 0)

    # ------------------------------------------------------------------
    # Proximity
    # ------------------------------------------------------------------
    @property
    def proximity(self) -> int:
        """Proximity data. Move your hand towards the sensor to see the value change."""
        return self._read16(_REG_PS_DATA)

    @property
    def proximity_shutdown(self) -> bool:
        """Proximity sensor shutdown. When True, proximity data is disabled."""
        return self._get_bit(_REG_PS_CONF1_2, 0)

    @proximity_shutdown.setter
    def proximity_shutdown(self, value: bool):
        self._set_bit(_REG_PS_CONF1_2, 0, value)

    @property
    def proximity_integration_time(self) -> int:
        """Proximity integration time. One of PS_1T ... PS_8T."""
        return self._get_bits(_REG_PS_CONF1_2, 1, 3)

    @proximity_integration_time.setter
    def proximity_integration_time(self, value: int):
        self._set_bits(_REG_PS_CONF1_2, 1, 3, value)

    @property
    def proximity_interrupt(self) -> int:
        """Proximity interrupt mode. One of PS_INT_DISABLE, PS_INT_CLOSE, PS_INT_AWAY, PS_INT_CLOSE_AWAY."""
        return self._get_bits(_REG_PS_CONF1_2, 8, 2)

    @proximity_interrupt.setter
    def proximity_interrupt(self, value: int):
        self._set_bits(_REG_PS_CONF1_2, 8, 2, value)

    @property
    def proximity_bits(self) -> bool:
        """False for 12-bit proximity output, True for 16-bit."""
        return self._get_bit(_REG_PS_CONF1_2, 11)

    @proximity_bits.setter
    def proximity_bits(self, value: bool):
        self._set_bit(_REG_PS_CONF1_2, 11, value)

    @property
    def proximity_low_threshold(self) -> int:
        """Proximity interrupt low threshold."""
        return self._read16(_REG_PS_THDL)

    @proximity_low_threshold.setter
    def proximity_low_threshold(self, value: int):
        self._write16(_REG_PS_THDL, value)

    @property
    def proximity_high_threshold(self) -> int:
        """Proximity interrupt high threshold."""
        return self._read16(_REG_PS_THDH)

    @proximity_high_threshold.setter
    def proximity_high_threshold(self, value: int):
        self._write16(_REG_PS_THDH, value)

    @property
    def led_current(self) -> int:
        """LED current. One of LED_50MA ... LED_200MA."""
        return self._get_bits(_REG_PS_CONF3_MS, 8, 3)

    @led_current.setter
    def led_current(self, value: int):
        self._set_bits(_REG_PS_CONF3_MS, 8, 3, value)

    @property
    def led_duty_cycle(self) -> int:
        """LED duty ratio. One of LED_1_40, LED_1_80, LED_1_160, LED_1_320."""
        return self._get_bits(_REG_PS_CONF1_2, 6, 2)

    @led_duty_cycle.setter
    def led_duty_cycle(self, value: int):
        self._set_bits(_REG_PS_CONF1_2, 6, 2, value)

    @property
    def proximity_high_interrupt(self) -> bool:
        """True once after the proximity rises above the high threshold (interrupt mode CLOSE or CLOSE_AWAY)."""
        return self._get_and_clear_cached_interrupt_state(self.PS_IF_CLOSE)

    @property
    def proximity_low_interrupt(self) -> bool:
        """True once after the proximity drops below the low threshold (interrupt mode AWAY or CLOSE_AWAY)."""
        return self._get_and_clear_cached_interrupt_state(self.PS_IF_AWAY)

    # ------------------------------------------------------------------
    # Ambient light
    # ------------------------------------------------------------------
    @property
    def light(self) -> int:
        """Raw ambient light data. Use `lux` for the scaled value."""
        return self._read16(_REG_ALS_DATA)

    @property
    def lux(self) -> float:
        """Ambient light in lux, scaled for the current integration time."""
        return self.light * (0.1 / (1 << self.light_integration_time))

    @property
    def light_shutdown(self) -> bool:
        """Ambient light sensor shutdown. When True, ambient light data is disabled."""
        return self._get_bit(_REG_ALS_CONF, 0)

    @light_shutdown.setter
    def light_shutdown(self, value: bool):
        self._set_bit(_REG_ALS_CONF, 0, value)

    @property
    def light_integration_time(self) -> int:
        """Ambient light integration time. One of ALS_80MS, ALS_160MS, ALS_320MS, ALS_640MS.

        Setting it waits for the sensor to settle before returning.
        """
        return self._get_bits(_REG_ALS_CONF, 6, 2)

    @light_integration_time.setter
    def light_integration_time(self, new_it: int):
        # IT values are 0-3 -> 80-640 ms
        old_it_ms = (8 << self.light_integration_time) * 10
        new_it_ms = (8 << new_it) * 10
        it_delay_seconds = (old_it_ms + new_it_ms + 1) * 0.001
        self._set_bits(_REG_ALS_CONF, 6, 2, new_it)
        time.sleep(it_delay_seconds)

    @property
    def light_interrupt(self) -> bool:
        """Ambient light interrupt enable."""
        return self._get_bit(_REG_ALS_CONF, 1)

    @light_interrupt.setter
    def light_interrupt(self, value: bool):
        self._set_bit(_REG_ALS_CONF, 1, value)

    @property
    def light_low_threshold(self) -> int:
        """Ambient light interrupt low threshold."""
        return self._read16(_REG_ALS_THDL)

    @light_low_threshold.setter
    def light_low_threshold(self, value: int):
        self._write16(_REG_ALS_THDL, value)

    @property
    def light_high_threshold(self) -> int:
        """Ambient light interrupt high threshold."""
        return self._read16(_REG_ALS_THDH)

    @light_high_threshold.setter
    def light_high_threshold(self, value: int):
        self._write16(_REG_ALS_THDH, value)

    @property
    def light_high_interrupt(self) -> bool:
        """True once after the ambient light exceeds the high threshold."""
        return self._get_and_clear_cached_interrupt_state(self.ALS_IF_H)

    @property
    def light_low_interrupt(self) -> bool:
        """True once after the ambient light drops below the low threshold."""
        return self._get_and_clear_cached_interrupt_state(self.ALS_IF_L)

    # ------------------------------------------------------------------
    # White channel
    # ------------------------------------------------------------------
    @property
    def white(self) -> float:
        """White light data, scaled for the current integration time."""
        return self._read16(_REG_WHITE_DATA) * (0.1 / (1 << self.light_integration_time))

    @property
    def white_shutdown(self) -> bool:
        """White light channel shutdown. When True, white light data is disabled."""
        return self._get_bit(_REG_PS_CONF3_MS, 15)

    @white_shutdown.setter
    def white_shutdown(self, value: bool):
        self._set_bit(_REG_PS_CONF3_MS, 15, value)

    # ------------------------------------------------------------------
    # Interrupt flags
    # ------------------------------------------------------------------
    @property
    def interrupt_state(self) -> int:
        """Raw interrupt flag register."""
        return self._read16(_REG_INT_FLAG)

    def _update_interrupt_state(self):
        new_interrupt_state = self.interrupt_state
        for interrupt in (self.PS_IF_AWAY, self.PS_IF_CLOSE, self.ALS_IF_H, self.ALS_IF_L):
            if new_interrupt_state & (1 << interrupt):
                self._cached_interrupt_state[interrupt] = True

    def _get_and_clear_cached_interrupt_state(self, interrupt_offset: int) -> bool:
        self._update_interrupt_state()
        state = self._cached_interrupt_state[interrupt_offset]
        self._cached_interrupt_state[interrupt_offset] = False
        return state
