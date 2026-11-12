"""Small PN532 I2C driver for the Pulsar C5 MicroPython RFID lessons.

The PN532 uses 7-bit address 0x24 in machine.I2C (0x48 in NXP's
8-bit write-address notation). This is an original implementation of
the normal PN532 command frame described in NXP UM0701-02.
"""

from time import sleep_ms, ticks_diff, ticks_ms


ADDRESS = 0x24
ACK = b"\x00\x00\xff\x00\xff\x00"
DEFAULT_KEY = b"\xff" * 6


def hex_bytes(data):
    return " ".join("{:02X}".format(byte) for byte in data)


class CardExchangeError(OSError):
    """A status byte returned by the PN532 for InDataExchange."""

    def __init__(self, status):
        self.status = status
        super().__init__("Card exchange failed (PN532 status 0x{:02X})".format(status))


class PN532:
    def __init__(self, i2c, address=ADDRESS):
        self.i2c = i2c
        self.address = address
        if address not in i2c.scan():
            raise OSError("PN532 absent at 0x24; check I2C switches, wiring and 3V3")

    def _wait_ready(self, timeout_ms):
        start = ticks_ms()
        while ticks_diff(ticks_ms(), start) < timeout_ms:
            try:
                if self.i2c.readfrom(self.address, 1)[0] & 1:
                    return True
            except OSError:
                pass  # Some boards NACK briefly after an exchange.
            sleep_ms(5)
        return False

    def _read(self, count):
        packet = self.i2c.readfrom(self.address, count + 1)
        if len(packet) != count + 1 or not packet[0] & 1:
            raise OSError("PN532 frame not ready")
        return packet[1:]

    def command(self, code, payload=b"", timeout_ms=1000, max_payload=0):
        data = bytes((0xD4, code)) + bytes(payload)
        if len(data) > 255:
            raise ValueError("PN532 normal frame is limited to 255 data bytes")
        size = len(data)
        frame = bytes((0, 0, 0xFF, size, (-size) & 0xFF)) + data
        frame += bytes(((-sum(data)) & 0xFF, 0))
        self.i2c.writeto(self.address, frame)
        if not self._wait_ready(200) or self._read(6) != ACK:
            raise OSError("PN532 did not acknowledge command 0x{:02X}".format(code))
        if not self._wait_ready(timeout_ms):
            raise OSError("PN532 response timeout for 0x{:02X}".format(code))
        response = self._read(9 + max_payload)
        if response[:3] != b"\x00\x00\xff":
            raise OSError("PN532 response has invalid preamble")
        length = response[3]
        if length < 2 or length + 7 > len(response) or (length + response[4]) & 0xFF:
            raise OSError("PN532 response has invalid length")
        body = response[5:5 + length]
        if (sum(body) + response[5 + length]) & 0xFF:
            raise OSError("PN532 response has invalid checksum")
        if body[:2] != bytes((0xD5, code + 1)):
            raise OSError("PN532 returned a different command")
        return body[2:]

    def firmware_version(self):
        version = self.command(0x02, max_payload=4)
        if len(version) != 4 or version[0] != 0x32:
            raise OSError("Unexpected PN532 firmware response")
        return tuple(version)

    def prepare(self):
        self.command(0x14, b"\x01\x14\x01")  # SAM normal mode.
        # One passive activation attempt, rather than the chip's infinite default.
        self.command(0x32, b"\x05\xff\x01\x00")

    def read_tag(self):
        """Return {uid, atqa, sak} for one ISO14443-A target or None."""
        answer = self.command(0x4A, b"\x01\x00", max_payload=55)
        if not answer or answer[0] == 0:
            return None
        if len(answer) < 6 or answer[0] != 1 or answer[1] != 1:
            raise OSError("Invalid passive target response")
        uid_length = answer[5]
        if uid_length not in (4, 7, 10) or len(answer) < 6 + uid_length:
            raise OSError("Invalid ISO14443-A UID length")
        return {"uid": bytes(answer[6:6 + uid_length]),
                "atqa": bytes(answer[2:4]), "sak": answer[4]}

    def exchange(self, data):
        answer = self.command(0x40, b"\x01" + bytes(data), max_payload=17)
        if not answer or answer[0] != 0:
            raise CardExchangeError(answer[0] if answer else 0xFF)
        return bytes(answer[1:])

    def authenticate_classic(self, tag, block, key=DEFAULT_KEY, key_type=0x60):
        if tag["sak"] not in (0x08, 0x09, 0x18):
            raise ValueError("This card is not MIFARE Classic")
        if len(key) != 6:
            raise ValueError("Classic key must be six bytes")
        if key_type not in (0x60, 0x61):
            raise ValueError("Classic key type must be 0x60 (A) or 0x61 (B)")
        if not 0 <= block <= 255:
            raise ValueError("Classic block must be between 0 and 255")
        # MIFARE Classic authentication uses the last four cascade UID bytes.
        self.exchange(bytes((key_type, block)) + bytes(key) + tag["uid"][-4:])

    def read_classic_block(self, block):
        data = self.exchange(bytes((0x30, block)))
        if len(data) != 16:
            raise OSError("MIFARE Classic read did not return 16 bytes")
        return data

    def write_classic_block(self, block, data):
        if block not in (4, 5, 6, 8, 9, 10) or len(data) != 16:
            raise ValueError("Use only lab data blocks 4-6 or 8-10, with 16 bytes")
        self.exchange(bytes((0xA0, block)) + bytes(data))

    def read_type2_pages(self, page=8):
        if page != 8:
            raise ValueError("These lessons use only Type 2 page 8")
        data = self.exchange(bytes((0x30, page)))
        if len(data) != 16:
            raise OSError("Type 2 read did not return four pages")
        return data

    def write_type2_page(self, data, page=8):
        if page != 8 or len(data) != 4:
            raise ValueError("These lessons write only four bytes to Type 2 page 8")
        self.exchange(bytes((0xA2, page)) + bytes(data))


def open_reader():
    from machine import I2C, Pin
    bus = I2C(0, sda=Pin(1), scl=Pin(6), freq=100_000)
    reader = PN532(bus)
    reader.prepare()
    return reader
