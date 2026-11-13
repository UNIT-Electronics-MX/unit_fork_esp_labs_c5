"""Host tests for the ES8311 driver: register sequence, gain and volume
mapping, mono channel selection and the DLM1 packet CRC."""
import array
import binascii
import importlib.util
import pathlib
import sys
import types
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parents[1]


class FakeI2C:
    def __init__(self, *args, **kwargs):
        self.regs = bytearray(256)
        self.pointer = 0

    def writeto(self, address, buf, stop=True):
        if len(buf) == 2:
            self.regs[buf[0]] = buf[1]
        elif len(buf) == 1:
            self.pointer = buf[0]

    def readfrom_into(self, address, buf):
        buf[0] = self.regs[self.pointer]

    def scan(self):
        return [0x18]


class FakeI2S:
    TX, RX, STEREO, MONO = 0, 1, 1, 0
    accepts_mck = False

    def __init__(self, i2s_id, **kwargs):
        if "mck" in kwargs and not FakeI2S.accepts_mck:
            raise TypeError("unexpected keyword argument 'mck'")
        self.kwargs = kwargs

    def readinto(self, buf):
        # Left slot quiet, right slot loud: read_mono must pick the right.
        for i in range(len(buf)):
            buf[i] = 20 if i % 2 == 0 else -300
        return len(buf) * 2

    def write(self, buf):
        return len(buf) * 2

    def deinit(self):
        pass


class FakePin:
    OUT = 1

    def __init__(self, number, *args, **kwargs):
        self.number = number

    def value(self, *args):
        pass


machine = types.ModuleType("machine")
machine.I2C, machine.I2S, machine.Pin = FakeI2C, FakeI2S, FakePin
micropython = types.ModuleType("micropython")
micropython.const = lambda value: value
sys.modules["machine"] = machine
sys.modules["micropython"] = micropython


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


es8311 = load("es8311", ROOT / "libraries" / "es8311.py")
sys.modules["es8311"] = es8311


def make_audio():
    # Muse pin map with a microphone line; the preset leaves data_in open.
    return es8311.ES8311Audio(**dict(es8311.PINOUTS["pulsar-c5"], sda=2, scl=3, data_in=1))


class RegisterTests(unittest.TestCase):
    def test_rx_from_bclk(self):
        audio = make_audio()
        audio.begin_rx(16000, gain=1.0)
        regs = audio.codec.i2c.regs
        self.assertFalse(audio.uses_mclk_pin)
        self.assertEqual(regs[0x01], 0xBF)  # MCLK from SCLK, clocks on
        self.assertEqual(regs[0x02], 0x18)  # pre_div 1, x8 multiplier
        self.assertEqual(regs[0x09], 0x4C)  # DAC serial input muted, 16-bit
        self.assertEqual(regs[0x0A], 0x0C)  # ADC serial output on, 16-bit
        self.assertEqual(regs[0x16], 7)     # gain 1.0 -> 42 dB
        self.assertEqual(regs[0x0D], 0x01)

    def test_tx_from_mclk_pin(self):
        FakeI2S.accepts_mck = True
        try:
            audio = es8311.ES8311Audio(sda=1, scl=6, mclk=10, bclk=4, ws=5,
                                       data_out=9)
            audio.begin_tx(44100, volume=0.7)
        finally:
            FakeI2S.accepts_mck = False
        regs = audio.codec.i2c.regs
        self.assertTrue(audio.uses_mclk_pin)
        self.assertEqual(regs[0x01], 0x3F)
        self.assertEqual(regs[0x02], 0x00)
        self.assertEqual(regs[0x09], 0x0C)
        self.assertEqual(regs[0x32], 220)   # Arduino setVolume(0.7)
        self.assertEqual(audio.codec.volume(), 70)

    def test_gain_steps_match_arduino_map(self):
        codec = make_audio().codec
        for gain, step in ((0.0, 0), (0.15, 1), (0.5, 3), (0.99, 6), (1.0, 7)):
            codec.set_input_gain(gain)
            self.assertEqual(codec.i2c.regs[0x16], step, gain)

    def test_rejects_bad_settings(self):
        with self.assertRaises(ValueError):
            es8311.ES8311Audio(sda=1, scl=1, bclk=4, ws=5)
        audio = make_audio()
        with self.assertRaises(ValueError):
            audio.begin_rx(12345)


class CaptureTests(unittest.TestCase):
    def test_read_mono_picks_louder_slot(self):
        audio = make_audio()
        audio.begin_rx(16000)
        out = array.array("h", bytes(512))
        self.assertEqual(audio.read_mono(out), 256)
        self.assertEqual(out[0], -300)

    def test_packet_crc_matches_receiver(self):
        source = (ROOT / "es8311_mic_serial_pcm.py").read_text()
        namespace = {}
        exec(source.split("def main")[0], namespace)
        pcm = array.array("h", range(-128, 128))
        self.assertEqual(namespace["crc16"](pcm),
                         binascii.crc_hqx(bytes(pcm), 0xFFFF))


class FakeSerialIn:
    """Sends the browser's commands, then stops the script like Ctrl+C."""

    def __init__(self, commands):
        self.commands = list(commands)

    def read(self, count):
        if not self.commands:
            raise KeyboardInterrupt
        return self.commands.pop(0)


class FakePoll:
    def __init__(self, serial_in, frames_before_stop):
        self.serial_in = serial_in
        self.left = frames_before_stop

    def register(self, *args):
        pass

    def poll(self, timeout):
        self.left -= 1
        return self.left < 0


class WebAudioTests(unittest.TestCase):
    def test_stream_matches_browser_protocol(self):
        import io
        serial_in = FakeSerialIn([b"R", b"S"])
        out = io.BytesIO()
        select_module = types.ModuleType("select")
        select_module.POLLIN = 1
        select_module.poll = lambda: FakePoll(serial_in, 2)
        fake_stdin = types.SimpleNamespace(buffer=serial_in)
        fake_stdout = types.SimpleNamespace(buffer=out)
        with unittest.mock.patch.dict(sys.modules, {"select": select_module}), \
                unittest.mock.patch.object(sys, "stdin", fake_stdin), \
                unittest.mock.patch.object(sys, "stdout", fake_stdout):
            with self.assertRaises(KeyboardInterrupt):
                load("es8311_mic_usb_audio", ROOT / "es8311_mic_usb_audio.py")
        data = out.getvalue()
        self.assertTrue(data.startswith(b"RAW\r\n"))
        frame = data[5:]
        self.assertEqual(frame[:2], b"AU")
        count = int.from_bytes(frame[2:4], "little")
        self.assertEqual(count, 512)
        payload = frame[4:4 + count * 2]
        words = array.array("H", payload)
        checksum = int.from_bytes(frame[4 + count * 2:6 + count * 2], "little")
        self.assertEqual(sum(words) & 0xFFFF, checksum)
        self.assertEqual(array.array("h", payload)[0], -300)  # louder slot
        self.assertEqual(len(data), 5 + 2 * (6 + count * 2))  # two frames


if __name__ == "__main__":
    unittest.main()
