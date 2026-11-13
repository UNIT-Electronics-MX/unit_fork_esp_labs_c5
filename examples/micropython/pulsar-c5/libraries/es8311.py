# IMPORTANT: Copy this entire file to your MicroPython board.
# Save it as "es8311.py" in the root directory or in /lib.

"""MicroPython driver for the Everest ES8311 mono audio codec.

Port of the UNIT DevLab Arduino codec library (DevLab_Codec_ES):
https://github.com/UNIT-Electronics-MX/unit_devlab_codec_library
The register sequence, clock table, volume curve and microphone gain steps
come from the ES8311 driver in the UNIT DevLab Audio Driver fork
(Espressif MIT licence, adapted by Phil Schatzmann).

License: GPL-3.0, the same as the Arduino codec library.

Two layers:

* ``ES8311``: register-level codec control over I2C (init, start, volume,
  mute, microphone gain, suspend). It does not touch I2S.
* ``ES8311Audio``: I2C + machine.I2S together, like ``I2SCodecStream`` and
  ``ES8311Microphone`` in Arduino. Use ``begin_tx()`` to play and
  ``begin_rx()`` to capture. MicroPython's I2S is half duplex, so one
  direction at a time.

The ESP32 is always the I2S master; the ES8311 runs as a slave.

Master clock: if the firmware's machine.I2S accepts ``mck=`` and a MCLK pin is
given, the codec is clocked from MCLK = 256 x fs (same as Arduino). Otherwise
the codec derives its clock from BCLK (MCLK_SEL = SCLK, x8 multiplier), which
needs 16-bit stereo frames (BCLK = 32 x fs). Both paths produce the same
internal 256 x fs clock.
"""

import machine
from machine import I2C, Pin
from micropython import const

ES8311_ADDRESS = const(0x18)

I2S_MISSING = ("This MicroPython firmware has no machine.I2S. The stock "
               "ESP32_GENERIC_C5 image disables it; flash a C5 build with "
               "MICROPY_PY_MACHINE_I2S enabled.")

# Codec modes (audio_driver::codec_mode_t)
MODE_ADC = const(1)   # encode: microphone -> ESP32
MODE_DAC = const(2)   # decode: ESP32 -> speaker/headphones
MODE_BOTH = const(3)

# Serial audio formats (registers 0x09 / 0x0A, bits 1:0)
FMT_I2S = const(0)
FMT_LEFT_J = const(1)
FMT_DSP_A = const(3)

# Microphone PGA gain steps (register 0x16), 6 dB per step.
MIC_GAIN_0DB = const(0)
MIC_GAIN_6DB = const(1)
MIC_GAIN_12DB = const(2)
MIC_GAIN_18DB = const(3)
MIC_GAIN_24DB = const(4)
MIC_GAIN_30DB = const(5)
MIC_GAIN_36DB = const(6)
MIC_GAIN_42DB = const(7)

_REG00_RESET = const(0x00)
_REG01_CLK = const(0x01)
_REG02_CLK = const(0x02)
_REG03_CLK = const(0x03)
_REG04_CLK = const(0x04)
_REG05_CLK = const(0x05)
_REG06_CLK = const(0x06)
_REG07_CLK = const(0x07)
_REG08_CLK = const(0x08)
_REG09_SDPIN = const(0x09)
_REG0A_SDPOUT = const(0x0A)
_REG0B_SYS = const(0x0B)
_REG0C_SYS = const(0x0C)
_REG0D_SYS = const(0x0D)
_REG0E_SYS = const(0x0E)
_REG10_SYS = const(0x10)
_REG11_SYS = const(0x11)
_REG12_SYS = const(0x12)
_REG13_SYS = const(0x13)
_REG14_SYS = const(0x14)
_REG15_ADC = const(0x15)
_REG16_ADC = const(0x16)
_REG17_ADC = const(0x17)
_REG1B_ADC = const(0x1B)
_REG1C_ADC = const(0x1C)
_REG31_DAC = const(0x31)
_REG32_DAC = const(0x32)
_REG37_DAC = const(0x37)
_REG45_GP = const(0x45)
_REGFD_CHD1 = const(0xFD)
_REGFE_CHD2 = const(0xFE)
_REGFF_CHVER = const(0xFF)

# Clock coefficients for MCLK = 256 x fs, the only ratio the Arduino library
# requests (MCLK_DIV_FRE = 256). Rows from the original es8311_coeff_div[]:
# rate: (mclk, pre_div, pre_multi, adc_div, dac_div, fs_mode,
#        lrck_h, lrck_l, bclk_div, adc_osr, dac_osr)
_COEFF = {
    8000: (2048000, 1, 2, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    11025: (2822400, 1, 1, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    16000: (4096000, 1, 1, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    22050: (5644800, 1, 1, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    24000: (6144000, 1, 1, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    32000: (8192000, 1, 1, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    44100: (11289600, 1, 1, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    48000: (12288000, 1, 1, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
}
_MULTI_BITS = {1: 0, 2: 1, 4: 2, 8: 3}

# Pin presets. "pulsar-c5" is the wiring used by the Pulsar C5 examples;
# "esp32" and "esp32c6" are the DualMCU pins of the Arduino examples.
# data_out: ESP32 -> ES8311 DIN (playback); data_in: ES8311 DOUT -> ESP32.
PINOUTS = {
    "pulsar-c5": dict(sda=1, scl=6, mclk=10, bclk=27, ws=7, data_out=26, data_in=-1),
    "esp32": dict(sda=21, scl=22, mclk=0, bclk=4, ws=16, data_out=15, data_in=5),
    "esp32c6": dict(sda=22, scl=23, mclk=21, bclk=3, ws=15, data_out=20, data_in=5),
}


class ES8311:
    """Register-level ES8311 control over I2C."""

    def __init__(self, i2c, address=ES8311_ADDRESS):
        self.i2c = i2c
        self.address = address
        self._buf1 = bytearray(1)
        self._buf2 = bytearray(2)

    # --- I2C helpers -------------------------------------------------------
    def write_reg(self, reg, value):
        self._buf2[0] = reg
        self._buf2[1] = value & 0xFF
        self.i2c.writeto(self.address, self._buf2)

    def read_reg(self, reg):
        self._buf1[0] = reg
        self.i2c.writeto(self.address, self._buf1, False)
        self.i2c.readfrom_into(self.address, self._buf1)
        return self._buf1[0]

    def probe(self):
        """True if the codec acknowledges its I2C address."""
        try:
            self.i2c.writeto(self.address, b"")
            return True
        except OSError:
            return False

    def chip_id(self):
        """(0xFD, 0xFE, 0xFF) = (0x83, 0x11, version) on a genuine ES8311."""
        return (self.read_reg(_REGFD_CHD1), self.read_reg(_REGFE_CHD2),
                self.read_reg(_REGFF_CHVER))

    def dump(self, registers=(0x09, 0x12, 0x31, 0x32, 0xFD, 0xFE)):
        """Return 'XX=YY' pairs for the registers printed by the Arduino demo."""
        return " ".join("%02X=%02X" % (r, self.read_reg(r)) for r in registers)

    # --- initialisation (ES8311::init + configI2S) -------------------------
    def init(self, sample_rate, bits=16, fmt=FMT_I2S, mclk_from_sclk=False,
             codec_master=False):
        coeff = _COEFF.get(sample_rate)
        if coeff is None:
            raise ValueError("unsupported sample rate: %d" % sample_rate)
        (_, pre_div, pre_multi, adc_div, dac_div, fs_mode,
         lrck_h, lrck_l, bclk_div, adc_osr, dac_osr) = coeff

        w = self.write_reg
        r = self.read_reg
        w(_REG01_CLK, 0x30)
        w(_REG02_CLK, 0x00)
        w(_REG03_CLK, 0x10)
        w(_REG16_ADC, 0x24)
        w(_REG04_CLK, 0x10)
        w(_REG05_CLK, 0x00)
        w(_REG0B_SYS, 0x00)
        w(_REG0C_SYS, 0x00)
        w(_REG10_SYS, 0x1F)
        w(_REG11_SYS, 0x7F)
        w(_REG00_RESET, 0x80)

        regv = r(_REG00_RESET)
        regv = (regv | 0x40) if codec_master else (regv & 0xBF)
        w(_REG00_RESET, regv)
        w(_REG01_CLK, 0x3F)

        # Internal MCLK source: MCLK pin (bit7 = 0) or BCLK/SCLK (bit7 = 1).
        regv = r(_REG01_CLK)
        regv = (regv | 0x80) if mclk_from_sclk else (regv & 0x7F)
        w(_REG01_CLK, regv)

        regv = r(_REG02_CLK) & 0x07
        regv |= (pre_div - 1) << 5
        multi = 3 if mclk_from_sclk else _MULTI_BITS[pre_multi]  # BCLK x 8
        regv |= multi << 3
        w(_REG02_CLK, regv)

        w(_REG05_CLK, ((adc_div - 1) << 4) | (dac_div - 1))
        w(_REG03_CLK, (r(_REG03_CLK) & 0x80) | (fs_mode << 6) | adc_osr)
        w(_REG04_CLK, (r(_REG04_CLK) & 0x80) | dac_osr)
        w(_REG07_CLK, (r(_REG07_CLK) & 0xC0) | lrck_h)
        w(_REG08_CLK, lrck_l)
        regv = r(_REG06_CLK) & 0xE0
        regv |= (bclk_div - 1) if bclk_div < 19 else bclk_div
        w(_REG06_CLK, regv)

        w(_REG01_CLK, r(_REG01_CLK) & ~0x40)  # MCLK not inverted
        w(_REG06_CLK, r(_REG06_CLK) & ~0x20)  # SCLK not inverted

        w(_REG13_SYS, 0x10)
        w(_REG1B_ADC, 0x0A)
        w(_REG1C_ADC, 0x6A)

        self.set_bits_per_sample(bits)
        self.set_format(fmt)

    def set_bits_per_sample(self, bits):
        dac = self.read_reg(_REG09_SDPIN)
        adc = self.read_reg(_REG0A_SDPOUT)
        if bits == 32:
            dac |= 0x10
            adc |= 0x10
        elif bits != 24:  # 16 bits (default)
            dac |= 0x0C
            adc |= 0x0C
        self.write_reg(_REG09_SDPIN, dac)
        self.write_reg(_REG0A_SDPOUT, adc)

    def set_format(self, fmt=FMT_I2S):
        dac = self.read_reg(_REG09_SDPIN)
        adc = self.read_reg(_REG0A_SDPOUT)
        if fmt == FMT_DSP_A:
            dac = (dac & 0xDC) | 0x03
            adc = (adc & 0xDC) | 0x03
        else:
            dac = (dac & 0xFC) | (fmt & 0x01)
            adc = (adc & 0xFC) | (fmt & 0x01)
        self.write_reg(_REG09_SDPIN, dac)
        self.write_reg(_REG0A_SDPOUT, adc)

    # --- run state (ES8311::start / suspend) -------------------------------
    def start(self, mode=MODE_DAC, dmic=False):
        dac = (self.read_reg(_REG09_SDPIN) & 0xBF) | 0x40
        adc = (self.read_reg(_REG0A_SDPOUT) & 0xBF) | 0x40
        if mode & MODE_ADC:
            adc &= ~0x40
        if mode & MODE_DAC:
            dac &= ~0x40
        w = self.write_reg
        w(_REG09_SDPIN, dac)
        w(_REG0A_SDPOUT, adc)
        w(_REG17_ADC, 0xBF)
        w(_REG0E_SYS, 0x02)
        w(_REG12_SYS, 0x00)
        w(_REG14_SYS, 0x1A)
        regv = self.read_reg(_REG14_SYS)
        regv = (regv | 0x40) if dmic else (regv & ~0x40)
        w(_REG14_SYS, regv)
        w(_REG0D_SYS, 0x01)
        w(_REG15_ADC, 0x40)
        w(_REG37_DAC, 0x48)
        w(_REG45_GP, 0x00)

    def suspend(self):
        w = self.write_reg
        w(_REG32_DAC, 0x00)
        w(_REG17_ADC, 0x00)
        w(_REG0E_SYS, 0xFF)
        w(_REG12_SYS, 0x02)
        w(_REG14_SYS, 0x00)
        w(_REG0D_SYS, 0xFA)
        w(_REG15_ADC, 0x00)
        w(_REG37_DAC, 0x08)
        w(_REG45_GP, 0x01)

    stop = suspend

    # --- output volume / mute ----------------------------------------------
    def set_volume(self, percent):
        """Output volume 0..100 with the library's logarithmic curve."""
        percent = max(0, min(100, int(percent)))
        if percent == 0:
            reg = 0
        else:
            from math import log10
            reg = int(255.0 * log10(9.0 * percent / 100.0 + 1.0))
        self.write_reg(_REG32_DAC, reg)

    def volume(self):
        reg = self.read_reg(_REG32_DAC)
        if reg == 0:
            return 0
        return min(100, int((10 ** (reg / 255.0) - 1.0) / 9.0 * 100.0 + 0.5))

    def mute(self, enable=True):
        regv = self.read_reg(_REG31_DAC) & 0x9F
        if enable:
            self.write_reg(_REG12_SYS, 0x02)
            self.write_reg(_REG31_DAC, regv | 0x60)
            self.write_reg(_REG32_DAC, 0x00)
            self.write_reg(_REG37_DAC, 0x08)
        else:
            self.write_reg(_REG31_DAC, regv)
            self.write_reg(_REG12_SYS, 0x00)

    def is_muted(self):
        return bool(self.read_reg(_REG31_DAC) & 0x20)

    # --- microphone gain ---------------------------------------------------
    def set_mic_gain(self, step):
        """PGA gain step 0..7 (0 dB .. 42 dB in 6 dB steps)."""
        if not 0 <= step <= 7:
            raise ValueError("mic gain step must be 0..7")
        self.write_reg(_REG16_ADC, step)

    def set_input_gain(self, gain):
        """Arduino-compatible gain 0.0..1.0, mapped to the 8 PGA steps."""
        if not 0.0 <= gain <= 1.0:
            raise ValueError("gain must be 0.0..1.0")
        self.set_mic_gain(int(int(gain * 100) * 7 // 100))


# --- fast helpers (viper when available, plain Python otherwise) -----------
_FAST_SRC = """
@micropython.viper
def pick_mono(src: ptr16, dst: ptr16, frames: int) -> int:
    e0 = 0
    e1 = 0
    for i in range(frames):
        a = int(src[2 * i])
        b = int(src[2 * i + 1])
        if a & 0x8000:
            a = 0x10000 - a
        if b & 0x8000:
            b = 0x10000 - b
        e0 += a
        e1 += b
    ch = 1 if e1 > e0 else 0
    for i in range(frames):
        dst[i] = src[2 * i + ch]
    return ch
"""


def _pick_mono_py(src, dst, frames):
    e0 = e1 = 0
    for i in range(frames):
        e0 += abs(src[2 * i])
        e1 += abs(src[2 * i + 1])
    ch = 1 if e1 > e0 else 0
    for i in range(frames):
        dst[i] = src[2 * i + ch]
    return ch


try:
    _ns = {}
    exec(_FAST_SRC, _ns)
    pick_mono = _ns["pick_mono"]
except Exception:  # firmware without the native emitter
    pick_mono = _pick_mono_py


class ES8311Audio:
    """ES8311 + machine.I2S, the MicroPython counterpart of the Arduino
    I2SCodecStream (playback) and ES8311Microphone (capture) classes."""

    BLOCK_FRAMES = 256

    def __init__(self, sda, scl, bclk, ws, data_out=-1, data_in=-1, mclk=-1,
                 i2c_id=0, i2s_id=0, i2c_address=ES8311_ADDRESS,
                 i2c_frequency=100000, i2c=None, pa=-1):
        pins = [p for p in (sda, scl, bclk, ws, data_out, data_in, mclk) if p >= 0]
        if sda == scl or bclk == ws or len(set(pins)) != len(pins):
            raise ValueError("ES8311 pins overlap")
        if not 1 <= i2c_address <= 0x7F or i2c_frequency <= 0:
            raise ValueError("invalid I2C settings")
        # Pass an existing I2C object to share the bus with an OLED etc.
        if i2c is None:
            i2c = I2C(i2c_id, sda=Pin(sda), scl=Pin(scl), freq=i2c_frequency)
        self.codec = ES8311(i2c, i2c_address)
        self.bclk, self.ws, self.mclk = bclk, ws, mclk
        self.data_out, self.data_in = data_out, data_in
        self.i2s_id = i2s_id
        self.pa = Pin(pa, Pin.OUT, value=0) if pa >= 0 else None
        self.i2s = None
        self.mode = None
        self.sample_rate = 0
        self.uses_mclk_pin = False
        self._stereo = None
        self._mono = None

    # --- shared start-up -------------------------------------------------
    def _open_i2s(self, mode, sd, rate, ibuf):
        kwargs = dict(sck=Pin(self.bclk), ws=Pin(self.ws), sd=Pin(sd),
                      mode=mode, bits=16, format=machine.I2S.STEREO, rate=rate,
                      ibuf=ibuf)
        I2S = machine.I2S
        if self.mclk >= 0:
            try:
                i2s = I2S(self.i2s_id, mck=Pin(self.mclk), **kwargs)
                self.uses_mclk_pin = True
                return i2s
            except (TypeError, ValueError):
                pass  # this firmware has no mck= support: clock from BCLK
        self.uses_mclk_pin = False
        return I2S(self.i2s_id, **kwargs)

    def _begin(self, i2s_mode, codec_mode, sd, rate, ibuf):
        if not hasattr(machine, "I2S"):
            raise RuntimeError(I2S_MISSING)
        if self.i2s is not None:
            raise OSError("already started; call end() first")
        if rate not in _COEFF:
            raise ValueError("rate must be one of %s" % sorted(_COEFF))
        if not self.codec.probe():
            raise OSError("ES8311 not found at 0x%02X" % self.codec.address)
        # Start the clocks first so the codec locks as soon as it is enabled.
        self.i2s = self._open_i2s(i2s_mode, sd, rate, ibuf)
        try:
            self.codec.init(rate, bits=16, fmt=FMT_I2S,
                            mclk_from_sclk=not self.uses_mclk_pin)
            self.codec.start(codec_mode)
        except Exception:
            self.end()
            raise
        self.mode = codec_mode
        self.sample_rate = rate

    def begin_tx(self, rate=44100, volume=0.7, ibuf=8192):
        """Start playback: write 16-bit stereo little-endian PCM with write()."""
        if self.data_out < 0:
            raise ValueError("data_out (ES8311 DIN) pin required for playback")
        self._begin(machine.I2S.TX, MODE_DAC, self.data_out, rate, ibuf)
        self.set_volume(volume)
        self.codec.mute(False)
        if self.pa:
            self.pa.value(1)

    def begin_rx(self, rate=16000, gain=0.5, ibuf=8192):
        """Start microphone capture. gain 0.0..1.0 (0..42 dB)."""
        if self.data_in < 0:
            raise ValueError("data_in (ES8311 DOUT) pin required for capture")
        if not 0.0 <= gain <= 1.0:
            raise ValueError("gain must be 0.0..1.0")
        self._begin(machine.I2S.RX, MODE_ADC, self.data_in, rate, ibuf)
        self.codec.set_input_gain(gain)
        import array
        self._stereo = array.array("h", bytes(self.BLOCK_FRAMES * 4))

    def end(self):
        if self.pa:
            self.pa.value(0)
        if self.i2s is not None:
            try:
                self.codec.suspend()
            except OSError:
                pass
            self.i2s.deinit()
        self.i2s = None
        self.mode = None
        self._stereo = None

    def is_active(self):
        return self.i2s is not None

    # --- playback --------------------------------------------------------
    def set_volume(self, volume):
        """volume 0.0..1.0 like the Arduino setVolume()."""
        self.codec.set_volume(volume * 100)

    def set_mute(self, enable):
        self.codec.mute(enable)

    def write(self, buf):
        return self.i2s.write(buf)

    # --- capture ---------------------------------------------------------
    def set_input_gain(self, gain):
        self.codec.set_input_gain(gain)

    def read_mono(self, out, max_frames=None):
        """Fill ``out`` (array('h')) with up to 256 mono samples.

        Like Arduino readMono(): reads stereo frames and keeps the more active
        slot, because the mono ADC can appear on either channel. Returns the
        number of samples written.
        """
        if self.mode != MODE_ADC:
            return 0
        frames = len(out) if max_frames is None else max_frames
        frames = min(frames, len(out), self.BLOCK_FRAMES)
        if frames <= 0:
            return 0
        view = memoryview(self._stereo)[: frames * 2]
        got = self.i2s.readinto(view) // 4
        if got:
            pick_mono(self._stereo, out, got)
        return got


def scan_report(i2c, address=ES8311_ADDRESS):
    """Text like the Arduino checkCodecI2C(): status plus other addresses."""
    found = i2c.scan()
    if address in found:
        return "I2C ES8311 (0x%02X): OK" % address
    others = " ".join("0x%02X" % a for a in found) or "none"
    return "I2C ES8311 (0x%02X): no response. Other addresses: %s" % (address, others)
