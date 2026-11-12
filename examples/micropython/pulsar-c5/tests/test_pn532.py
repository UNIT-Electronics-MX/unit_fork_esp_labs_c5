"""Host tests for PN532 framing and the lab's write boundaries."""
import importlib.util
import contextlib
import io
import pathlib
import runpy
import sys
import time
import types
import unittest
from unittest.mock import patch

time.sleep_ms = lambda ms: None
time.ticks_ms = lambda: int(time.monotonic() * 1000)
time.ticks_diff = lambda a, b: a - b

source = pathlib.Path(__file__).resolve().parents[1] / "libraries" / "pn532.py"
spec = importlib.util.spec_from_file_location("pn532", source)
pn532 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pn532)


class FakeBus:
    def __init__(self, answer=b""):
        self.answer = answer
        self.reads = []
        self.writes = []
        self.corrupt = False

    def scan(self):
        return [0x24]

    def writeto(self, address, frame):
        self.writes.append(frame)
        code = frame[6]
        payload = self.answer if code in (0x4A, 0x40) else {
            0x02: b"\x32\x01\x06\x07",
            0x14: b"", 0x32: b"",
        }.get(code, b"")
        body = bytes((0xD5, code + 1)) + payload
        response = b"\x00\x00\xff" + bytes((len(body), (-len(body)) & 255))
        response += body + bytes(((-sum(body)) & 255, 0))
        if self.corrupt:
            response = response[:-2] + b"\x01\x00"
        self.reads = [pn532.ACK, response]

    def readfrom(self, address, count):
        if count == 1:
            return b"\x01"
        return b"\x01" + self.reads.pop(0).ljust(count - 1, b"\x00")


class Pn532Test(unittest.TestCase):
    def test_firmware_and_finite_passive_retry(self):
        bus = FakeBus()
        reader = pn532.PN532(bus)
        self.assertEqual(reader.firmware_version(), (0x32, 1, 6, 7))
        reader.prepare()
        self.assertEqual(bus.writes[-1][7:-2], b"\x05\xff\x01\x00")
        for frame in bus.writes:
            self.assertEqual((frame[3] + frame[4]) & 255, 0)
            self.assertEqual(sum(frame[5:-1]) & 255, 0)

    def test_passive_target_and_no_target(self):
        bus = FakeBus(b"\x01\x01\x00\x04\x08\x04\xde\xad\xbe\xef")
        reader = pn532.PN532(bus)
        self.assertEqual(reader.read_tag()["uid"], b"\xde\xad\xbe\xef")
        bus.answer = b"\x00"
        self.assertIsNone(reader.read_tag())

    def test_corrupt_response_is_rejected(self):
        bus = FakeBus()
        bus.corrupt = True
        with self.assertRaisesRegex(OSError, "checksum"):
            pn532.PN532(bus).firmware_version()

    def test_write_is_limited_to_user_data(self):
        reader = pn532.PN532(FakeBus(b"\x00"))
        for block in (0, 3, 7, 11):
            with self.assertRaises(ValueError):
                reader.write_classic_block(block, b"x" * 16)
        with self.assertRaises(ValueError):
            reader.write_type2_page(b"test", page=2)
        reader.write_classic_block(4, b"x" * 16)
        reader.write_type2_page(b"test")

    def test_classic_authentication_status_and_key_type(self):
        bus = FakeBus(b"\x14")
        reader = pn532.PN532(bus)
        tag = {"sak": 0x08, "uid": b"\x60\x5a\xcd\x5c"}
        with self.assertRaises(pn532.CardExchangeError) as caught:
            reader.authenticate_classic(tag, 4)
        self.assertEqual(caught.exception.status, 0x14)
        self.assertEqual(bus.writes[-1][5:-2],
                         b"\xd4\x40\x01\x60\x04" + b"\xff" * 6 + tag["uid"])
        bus.answer = b"\x00"
        reader.authenticate_classic(tag, 4, b"\x11" * 6, key_type=0x61)
        self.assertEqual(bus.writes[-1][5:-2],
                         b"\xd4\x40\x01\x61\x04" + b"\x11" * 6 + tag["uid"])
        with self.assertRaisesRegex(ValueError, "key type"):
            reader.authenticate_classic(tag, 4, key_type=0x62)

    def test_r4_reports_authentication_failure_without_reading(self):
        class Reader:
            def read_tag(self):
                return {"sak": 0x08, "uid": b"\x60\x5a\xcd\x5c"}

            def exchange(self, data):
                expected = b"\x60\x04" + b"\xff" * 6 + b"\x60\x5a\xcd\x5c"
                if data != expected:
                    raise AssertionError("R4 sent unexpected authentication bytes")
                raise OSError("Card exchange failed (status 0x14)")

            def read_classic_block(self, block):
                raise AssertionError("R4 must not read after failed authentication")

        fake_module = types.ModuleType("pn532")
        fake_module.hex_bytes = pn532.hex_bytes
        fake_module.open_reader = Reader
        output = io.StringIO()
        script = source.parents[1] / "rfid_r4_pn532.py"
        with patch.dict(sys.modules, {"pn532": fake_module}), contextlib.redirect_stdout(output):
            runpy.run_path(str(script))
        self.assertIn("PN532 0x14: MIFARE Classic authentication failed", output.getvalue())
        self.assertIn("CLASSIC_KEY_TYPE", output.getvalue())

    def test_r5_never_writes_after_old_driver_authentication_error(self):
        class Reader:
            def read_tag(self):
                return {"sak": 0x08, "uid": b"\x60\x5a\xcd\x5c"}

            def exchange(self, data):
                raise OSError("Card exchange failed (status 0x14)")

            def read_classic_block(self, block):
                raise AssertionError("R5 must not read after failed authentication")

            def write_classic_block(self, block, data):
                raise AssertionError("R5 must not write after failed authentication")

        fake_module = types.ModuleType("pn532")
        fake_module.hex_bytes = pn532.hex_bytes
        fake_module.open_reader = Reader
        output = io.StringIO()
        script = source.parents[1] / "rfid_r5_pn532.py"
        code = script.read_text().replace("ARM_WRITE = False\n", "ARM_WRITE = True\n", 1)
        with patch.dict(sys.modules, {"pn532": fake_module}), contextlib.redirect_stdout(output):
            exec(compile(code, str(script), "exec"), {})
        self.assertIn("No bytes changed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
