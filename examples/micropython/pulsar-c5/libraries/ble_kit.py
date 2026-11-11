"""ble_kit: shared helpers for the Pulsar C5 Bluetooth LE labs B1-B8 in MicroPython.

The MicroPython port of esp-labs' kit/lib/ble-kit. Save it on the board as
ble_kit.py before running a ble_b*.py example. The Pulsar C5 has no screen, so
the labs print to the serial console and use the on-board parts:

    BOOT button  GPIO28   click, double-click and long press
    WS2812 LED   GPIO0    the radio's state (blue: advertising, green: connected)
"""

import binascii
import bluetooth
import json
import select
import struct
import sys
from machine import Pin
from micropython import const
from neopixel import NeoPixel
from time import ticks_diff, ticks_ms

# ---- bluetooth.BLE constants (MicroPython does not name them) ---------------------------------

IRQ_CENTRAL_CONNECT = const(1)
IRQ_CENTRAL_DISCONNECT = const(2)
IRQ_GATTS_WRITE = const(3)
IRQ_SCAN_RESULT = const(5)
IRQ_SCAN_DONE = const(6)
IRQ_PERIPHERAL_CONNECT = const(7)
IRQ_PERIPHERAL_DISCONNECT = const(8)
IRQ_GATTC_SERVICE_RESULT = const(9)
IRQ_GATTC_SERVICE_DONE = const(10)
IRQ_GATTC_CHARACTERISTIC_RESULT = const(11)
IRQ_GATTC_CHARACTERISTIC_DONE = const(12)
IRQ_GATTC_DESCRIPTOR_RESULT = const(13)
IRQ_GATTC_DESCRIPTOR_DONE = const(14)
IRQ_GATTC_READ_RESULT = const(15)
IRQ_GATTC_READ_DONE = const(16)
IRQ_GATTC_WRITE_DONE = const(17)
IRQ_GATTC_NOTIFY = const(18)
IRQ_MTU_EXCHANGED = const(21)
IRQ_CONNECTION_UPDATE = const(27)
IRQ_ENCRYPTION_UPDATE = const(28)
IRQ_GET_SECRET = const(29)
IRQ_SET_SECRET = const(30)
IRQ_PASSKEY_ACTION = const(31)

FLAG_READ = const(0x0002)
FLAG_WRITE_NO_RESPONSE = const(0x0004)
FLAG_WRITE = const(0x0008)
FLAG_NOTIFY = const(0x0010)
FLAG_READ_ENCRYPTED = const(0x0200)
FLAG_READ_AUTHENTICATED = const(0x0400)
FLAG_WRITE_ENCRYPTED = const(0x1000)
FLAG_WRITE_AUTHENTICATED = const(0x2000)

IO_DISPLAY_ONLY = const(0)
IO_DISPLAY_YESNO = const(1)
IO_KEYBOARD_ONLY = const(2)
IO_NO_INPUT_OUTPUT = const(3)

PASSKEY_INPUT = const(2)
PASSKEY_DISPLAY = const(3)
PASSKEY_COMPARE = const(4)

# AD structure types: [length][type][data...] inside every advertisement.
AD_FLAGS = const(0x01)
AD_UUID16_ALL = const(0x03)
AD_UUID128_ALL = const(0x07)
AD_SHORT_NAME = const(0x08)
AD_NAME = const(0x09)
AD_TX_POWER = const(0x0A)
AD_SERVICE_DATA16 = const(0x16)
AD_APPEARANCE = const(0x19)
AD_MANUFACTURER = const(0xFF)

AD_NAMES = {
    0x01: "Flags", 0x02: "16-bit UUIDs (part)", 0x03: "16-bit UUIDs", 0x06: "128-bit UUIDs (part)",
    0x07: "128-bit UUIDs", 0x08: "Short name", 0x09: "Name", 0x0A: "TX power", 0x16: "Service data",
    0x19: "Appearance", 0xFF: "Manufacturer data",
}

COMPANIES = {0x004C: "Apple", 0x0006: "Microsoft", 0x0075: "Samsung", 0x00E0: "Google",
             0x0087: "Garmin", 0x0157: "Huami", 0x02E5: "Espressif", 0x0059: "Nordic", 0xFFFF: "test"}

PULSAR_BOOT_PIN = 28
PULSAR_RGB_PIN = 0  # Some UNIT illustrations label it GPIO8: try 8 if the LED stays dark.


# ---- Advertising payloads ----------------------------------------------------------------------

def ad_field(kind, data):
    """One AD structure: length (type + data), type, data."""
    return bytes((len(data) + 1, kind)) + bytes(data)


def ad_fields(payload):
    """Yield (type, data) for each AD structure in a received advertisement."""
    payload = bytes(payload)
    i = 0
    while i + 1 < len(payload):
        length = payload[i]
        if length == 0 or i + 1 + length > len(payload):
            return
        yield payload[i + 1], payload[i + 2:i + 1 + length]
        i += 1 + length


def ad_name(payload):
    for kind, data in ad_fields(payload):
        if kind in (AD_NAME, AD_SHORT_NAME):
            return data.decode("utf-8", "ignore")
    return ""


def advertise_flags():
    """LE General Discoverable, BR/EDR (Classic) not supported."""
    return ad_field(AD_FLAGS, b"\x06")


def uuid_field(uuid):
    """The service UUID list: 16-bit or 128-bit, as the UUID itself is."""
    raw = bytes(uuid)
    return ad_field(AD_UUID16_ALL if len(raw) == 2 else AD_UUID128_ALL, raw)


def hex_bytes(data, sep=" "):
    return sep.join("{:02X}".format(b) for b in bytes(data))


def mac(address):
    return hex_bytes(address, ":")


def address_kind(addr_type, address):
    """Public, or which kind of random address (the top two bits of the first byte say)."""
    if addr_type == 0:
        return "public"
    top = bytes(address)[0] >> 6
    return {3: "random static", 1: "private (RPA)", 0: "private (NRPA)"}.get(top, "random")


def chip_temperature():
    """The chip's own sensor in degrees Celsius, or None when this firmware lacks it."""
    try:
        import esp32
        return float(esp32.mcu_temperature())
    except (ImportError, AttributeError, OSError):
        return None


# ---- The serial console, without blocking the radio -------------------------------------------

class Console:
    """Collects typed characters and returns one command line at a time."""

    def __init__(self):
        self._poll = select.poll()
        self._poll.register(sys.stdin, select.POLLIN)
        self._buffer = ""

    def line(self):
        while self._poll.poll(0):
            char = sys.stdin.read(1)
            if char in ("\r", "\n"):
                if self._buffer.strip():
                    text, self._buffer = self._buffer.strip(), ""
                    print(">", text)
                    return text
                self._buffer = ""
            elif char in ("\x08", "\x7f"):
                self._buffer = self._buffer[:-1]
            elif char:
                self._buffer += char
        return None


def split_command(text):
    """'Message hello there' -> ('message', 'hello there')."""
    head, _, rest = text.partition(" ")
    return head.lower(), rest.strip()


# ---- The BOOT button: click, double-click, long press ------------------------------------------

CLICK = const(1)
DOUBLE = const(2)
LONG = const(3)
GESTURE_NAMES = ("", "click", "double", "long")


class Button:
    """Polled from the main loop: returns CLICK, DOUBLE, LONG or 0."""

    DEBOUNCE_MS = 25
    DOUBLE_MS = 300
    LONG_MS = 700

    def __init__(self, pin=PULSAR_BOOT_PIN):
        self._pin = Pin(pin, Pin.IN, Pin.PULL_UP)
        self._down = False
        self._changed_at = ticks_ms()
        self._pressed_at = 0
        self._released_at = 0
        self._clicks = 0
        self._long_sent = False

    def poll(self):
        now = ticks_ms()
        down = self._pin.value() == 0
        if down != self._down and ticks_diff(now, self._changed_at) >= self.DEBOUNCE_MS:
            self._down, self._changed_at = down, now
            if down:
                self._pressed_at, self._long_sent = now, False
            elif not self._long_sent:
                self._clicks += 1
                self._released_at = now
                if self._clicks == 2:
                    self._clicks = 0
                    return DOUBLE
        if self._down and not self._long_sent and ticks_diff(now, self._pressed_at) >= self.LONG_MS:
            self._long_sent, self._clicks = True, 0
            return LONG
        if self._clicks == 1 and not self._down and ticks_diff(now, self._released_at) >= self.DOUBLE_MS:
            self._clicks = 0
            return CLICK
        return 0


# ---- The WS2812 as a status light ---------------------------------------------------------------

class StatusLed:
    """Blue blink while advertising or scanning, green when connected, or a colour set on purpose."""

    LEVEL = 40  # 0-255: the LED is very bright at full power

    def __init__(self, pin=PULSAR_RGB_PIN):
        self._pixel = NeoPixel(Pin(pin, Pin.OUT), 1)
        self._shown = None
        self.claimed = None  # a colour a phone or a command chose: wins over the radio state

    def show(self, rgb):
        rgb = tuple(c * self.LEVEL // 255 for c in rgb)
        if rgb != self._shown:
            self._shown = rgb
            self._pixel[0] = rgb
            self._pixel.write()

    def radio(self, state):
        """state: 'connected', 'advertising', 'scanning' or 'off'."""
        if self.claimed is not None:
            return self.show(self.claimed)
        blink = (ticks_ms() // 500) % 2 == 0
        if state == "connected":
            self.show((0, 255, 0))
        elif state in ("advertising", "scanning"):
            self.show((0, 0, 255) if blink else (0, 0, 40))
        else:
            self.show((0, 0, 0))

    def off(self):
        self.claimed = None
        self.show((0, 0, 0))


# ---- Bonds: the keys a pairing leaves behind, kept in a file -----------------------------------

class BondStore:
    """Answers the stack's _IRQ_GET_SECRET / _IRQ_SET_SECRET so bonds survive a reset."""

    PEER_KEYS = const(2)  # NimBLE's "peer security" entries: one per bonded device

    def __init__(self, path="ble_bonds.json"):
        self.path = path
        self._secrets = {}
        try:
            with open(path) as file:
                for kind, key, value in json.load(file):
                    self._secrets[(kind, binascii.unhexlify(key))] = binascii.unhexlify(value)
        except (OSError, ValueError):
            pass

    def _save(self):
        rows = [[kind, binascii.hexlify(key).decode(), binascii.hexlify(value).decode()]
                for (kind, key), value in self._secrets.items()]
        with open(self.path, "w") as file:
            json.dump(rows, file)

    def get(self, kind, index, key):
        if key is not None:
            return self._secrets.get((kind, bytes(key)))
        i = 0
        for (entry_kind, _key), value in self._secrets.items():
            if entry_kind == kind:
                if i == index:
                    return value
                i += 1
        return None

    def set(self, kind, key, value):
        entry = (kind, bytes(key))
        if value is None:
            if entry not in self._secrets:
                return False
            del self._secrets[entry]
        else:
            self._secrets[entry] = bytes(value)
        self._save()
        return True

    def count(self):
        return sum(1 for kind, _key in self._secrets if kind == self.PEER_KEYS)

    def forget(self):
        self._secrets = {}
        self._save()


# ---- The radio: connections, advertising, pairing ---------------------------------------------

class Radio:
    """One bluetooth.BLE with the chores every lab shares.

    Labs add their own event handlers with on(event, handler). A handler's return
    value goes back to the stack, which matters for the secret events only.
    """

    def __init__(self, name, bonds=None, io=IO_NO_INPUT_OUTPUT, mitm=False):
        self.name = name
        self.bonds = bonds
        self.ble = bluetooth.BLE()
        self.led = StatusLed()
        self.connections = {}  # handle -> {"mtu": 23, "encrypted": False, "bonded": False}
        self.handlers = {}
        self.advertising = False
        self.scanning = False
        self.readvertise = True
        self.pending_compare = None  # (connection, passkey) waiting for yes or no
        self.pending_input = None  # connection waiting for a passkey typed on the console
        self._adv = None
        self.ble.irq(self._irq)
        if bonds is not None:
            self.ble.config(bond=True, le_secure=True, mitm=mitm, io=io)
        self.ble.active(True)
        self.ble.config(gap_name=name, mtu=247)

    def on(self, event, handler):
        self.handlers[event] = handler

    def address(self):
        addr_type, address = self.ble.config("mac")
        return "{} ({})".format(mac(address), address_kind(addr_type, address))

    def state(self):
        if self.connections:
            return "connected"
        if self.advertising:
            return "advertising"
        return "scanning" if self.scanning else "off"

    def tick(self):
        self.led.radio(self.state())

    # Advertising ---------------------------------------------------------------------------

    def advertise(self, adv_data, resp_data=b"", interval_ms=100, connectable=True):
        self._adv = (adv_data, resp_data, interval_ms, connectable)
        self.ble.gap_advertise(interval_ms * 1000, adv_data=adv_data, resp_data=resp_data or None,
                               connectable=connectable)
        self.advertising = True

    def stop_advertising(self):
        self.ble.gap_advertise(None)
        self.advertising = False

    def resume_advertising(self):
        if self._adv:
            self.advertise(*self._adv)

    # Pairing -------------------------------------------------------------------------------

    def answer(self, accept):
        """Numeric comparison: the six digits match (True) or not (False)."""
        if not self.pending_compare:
            print("Nothing to confirm right now")
            return
        connection, _passkey = self.pending_compare
        self.pending_compare = None
        self.ble.gap_passkey(connection, PASSKEY_COMPARE, 1 if accept else 0)
        print("You answered", "yes: the numbers match" if accept else "no: pairing refused")

    def enter_passkey(self, text):
        """Passkey entry: the six digits the other device shows."""
        if self.pending_input is None:
            print("Nobody asked for a passkey")
            return
        try:
            passkey = int(text)
        except ValueError:
            print("passkey wants six digits, e.g. passkey 123456")
            return
        self.ble.gap_passkey(self.pending_input, PASSKEY_INPUT, passkey)
        self.pending_input = None

    def disconnect_all(self):
        for connection in list(self.connections):
            self.ble.gap_disconnect(connection)

    # Events --------------------------------------------------------------------------------

    def _irq(self, event, data):
        if event in (IRQ_CENTRAL_CONNECT, IRQ_PERIPHERAL_CONNECT):
            connection, addr_type, address = data
            self.connections[connection] = {"mtu": 23, "encrypted": False, "bonded": False, "at": ticks_ms()}
            self.advertising = False
            print("+ connected: {} ({})".format(mac(address), address_kind(addr_type, address)))
        elif event in (IRQ_CENTRAL_DISCONNECT, IRQ_PERIPHERAL_DISCONNECT):
            connection = data[0]
            link = self.connections.pop(connection, None)
            if self.pending_compare and self.pending_compare[0] == connection:
                self.pending_compare = None
            if self.pending_input == connection:
                self.pending_input = None
            print("- disconnected")
            if link and not link["encrypted"] and ticks_diff(ticks_ms(), link["at"]) < 3000:
                # The board never hangs up on its own here: the phone (or its stack) did. The usual
                # reason is a phone that still holds keys for this address from an earlier firmware
                # (the Arduino lab), which this script has never seen.
                print("  The phone hung up within 3 s without encrypting. If this repeats, forget this"
                      " board in the phone's Bluetooth settings (and in nRF Connect) and connect again.")
            if event == IRQ_CENTRAL_DISCONNECT and self.readvertise:
                self.resume_advertising()
        elif event == IRQ_MTU_EXCHANGED:
            connection, mtu = data
            if connection in self.connections:
                self.connections[connection]["mtu"] = mtu
            print("  MTU {}: up to {} bytes per packet".format(mtu, mtu - 3))
        elif event == IRQ_ENCRYPTION_UPDATE:
            connection, encrypted, authenticated, bonded, key_size = data
            if connection in self.connections:
                self.connections[connection].update(encrypted=encrypted, bonded=bonded)
            print("  link {}, {}, {}, {}-byte key".format(
                "encrypted" if encrypted else "NOT encrypted",
                "authenticated (MITM-safe)" if authenticated else "Just Works",
                "bonded" if bonded else "not bonded", key_size))
        elif event == IRQ_GET_SECRET and self.bonds is not None:
            return self.bonds.get(*data)
        elif event == IRQ_SET_SECRET and self.bonds is not None:
            return self.bonds.set(*data)
        elif event == IRQ_PASSKEY_ACTION:
            self._passkey(*data)
        handler = self.handlers.get(event)
        if handler:
            return handler(data)
        return None

    def _passkey(self, connection, action, passkey):
        if action == PASSKEY_DISPLAY:
            from random import randint
            passkey = randint(0, 999_999)
            self.ble.gap_passkey(connection, action, passkey)
            print("* PASSKEY {:06d}: type it on the other device".format(passkey))
        elif action == PASSKEY_COMPARE:
            self.pending_compare = (connection, passkey)
            print("* COMPARE {:06d}: same number on the other screen? click BOOT or type yes;"
                  " long press or no refuses".format(passkey))
        elif action == PASSKEY_INPUT:
            print("* Type the passkey shown on the other device: passkey 123456")
            self.pending_input = connection


def bench_uuid(number):
    """ba5e0001-..., ba5e0002-...: the Bench service's own 128-bit UUIDs."""
    return bluetooth.UUID("ba5e{:04x}-b1e5-4ab1-9e5c-be7cbe7cb0a7".format(number))


# ---- GATT registration with descriptors --------------------------------------------------------

DESC_USER = bluetooth.UUID(0x2901)    # User Description: the name apps show
DESC_FORMAT = bluetooth.UUID(0x2904)  # Presentation Format: type, exponent, unit


def characteristic(uuid, flags, description=None, presentation=None):
    """One characteristic for gatts_register_services, with its optional descriptors."""
    descriptors = []
    if description is not None:
        descriptors.append((DESC_USER, FLAG_READ, description))
    if presentation is not None:
        descriptors.append((DESC_FORMAT, FLAG_READ, presentation))
    return (uuid, flags, tuple(descriptors))


def register(ble, services):
    """Register services. Returns, per service, one (value handle, descriptor handles) per characteristic.

    A service is (uuid, characteristics); a descriptor may carry an initial value as a third item.
    """
    plain = tuple((uuid, tuple((c[0], c[1], tuple((d[0], d[1]) for d in c[2])) for c in chars))
                  for uuid, chars in services)
    result = []
    for (_uuid, chars), flat in zip(services, ble.gatts_register_services(plain)):
        i, rows = 0, []
        for char in chars:
            count = len(char[2])
            descriptors = flat[i + 1:i + 1 + count]
            for descriptor, handle in zip(char[2], descriptors):
                if len(descriptor) > 2:
                    ble.gatts_write(handle, descriptor[2])
            rows.append((flat[i], descriptors))
            i += 1 + count
        result.append(rows)
    return result


def device_information(model="ESP32-C5"):
    """The standard Device Information (180A) and Battery (180F) services."""
    return [
        (bluetooth.UUID(0x180A), (
            characteristic(bluetooth.UUID(0x2A29), FLAG_READ),
            characteristic(bluetooth.UUID(0x2A24), FLAG_READ),
            characteristic(bluetooth.UUID(0x2A26), FLAG_READ),
        )),
        (bluetooth.UUID(0x180F), (characteristic(bluetooth.UUID(0x2A19), FLAG_READ | FLAG_NOTIFY),)),
    ], ((b"UNIT Electronics", model.encode(), b"ble_kit 0.1"), (b"\x64",))


# ---- The Bench service: B3's GATT table, reused by B4 and B5 ----------------------------------

BENCH_SERVICE = bench_uuid(0)
LED, MESSAGE, BUTTON, TEMPERATURE, UPTIME, VAULT = range(6)
BENCH_NAMES = ("LED", "Message", "Button", "Temperature", "Uptime", "Vault")


class Bench:
    """LED, Message, Button, Temperature, Uptime and (B4) Vault, plus a lab's own extras.

    Every number is little-endian, like everything else in Bluetooth LE.
    """

    def __init__(self, radio, vault=False, lock=False, extras=()):
        self.radio = radio
        ble = radio.ble
        lock_flags = (FLAG_READ_ENCRYPTED | FLAG_WRITE_ENCRYPTED) if lock else 0
        chars = [
            characteristic(bench_uuid(1), FLAG_READ | FLAG_WRITE | lock_flags, b"LED colour: red, green, blue"),
            characteristic(bench_uuid(2), FLAG_READ | FLAG_WRITE | FLAG_WRITE_NO_RESPONSE | lock_flags,
                           b"Message for the board"),
            characteristic(bench_uuid(3), FLAG_READ | FLAG_NOTIFY, b"Button: gesture, count"),
            # sint16 (0x0E), x 10^-2, degree Celsius (0x272F)
            characteristic(bench_uuid(4), FLAG_READ | FLAG_NOTIFY, b"Chip temperature",
                           struct.pack("<BbHBH", 0x0E, -2, 0x272F, 1, 0)),
            # uint32 (0x08), second (0x2703)
            characteristic(bench_uuid(5), FLAG_READ, b"Uptime in seconds",
                           struct.pack("<BbHBH", 0x08, 0, 0x2703, 1, 0)),
        ]
        if vault:
            chars.append(characteristic(
                bench_uuid(6),
                FLAG_READ | FLAG_WRITE | FLAG_READ_ENCRYPTED | FLAG_READ_AUTHENTICATED
                | FLAG_WRITE_ENCRYPTED | FLAG_WRITE_AUTHENTICATED,
                b"Vault (pair first)"))
        chars.extend(extras)
        standard, values = device_information()
        rows = register(ble, [(BENCH_SERVICE, tuple(chars))] + standard)
        self.handles = [row[0] for row in rows[0]]
        self.extra_handles = self.handles[len(chars) - len(extras):]
        for handle, value in zip([row[0] for row in rows[1]] + [row[0] for row in rows[2]],
                                 values[0] + values[1]):
            ble.gatts_write(handle, value)
        ble.gatts_set_buffer(self.handles[MESSAGE], 40)
        ble.gatts_write(self.handles[LED], b"\x00\x00\x00")
        ble.gatts_write(self.handles[BUTTON], b"\x00\x00\x00")
        self.vault = vault
        if vault:
            ble.gatts_set_buffer(self.handles[VAULT], 40)
            ble.gatts_write(self.handles[VAULT], b"nothing here yet")
        self.button_count = 0
        self.message = ""
        self._seconds_at = 0
        radio.on(IRQ_GATTS_WRITE, self._written)

    def advertise(self):
        """Flags and the 128-bit service UUID (21 of 31 bytes); the name in the scan response."""
        resp = ad_field(AD_NAME, self.radio.name.encode()) + ad_field(AD_APPEARANCE, b"\x40\x05")
        self.radio.advertise(advertise_flags() + uuid_field(BENCH_SERVICE), resp)
        print("Advertising as", self.radio.name)

    def notify(self, index, value):
        """Store a value and notify every connected device (MicroPython does not report subscriptions)."""
        handle = self.handles[index]
        self.radio.ble.gatts_write(handle, value)
        sent = 0
        for connection in self.radio.connections:
            try:
                self.radio.ble.gatts_notify(connection, handle)
                sent += 1
            except OSError:
                pass  # the stack's buffers are full or the link just dropped
        return sent

    def press(self, gesture):
        """Button notification: gesture (1 click, 2 double, 3 long), count (uint16)."""
        self.button_count = (self.button_count + 1) & 0xFFFF
        sent = self.notify(BUTTON, struct.pack("<BH", gesture, self.button_count))
        print("{} Button: {} #{}".format("notify" if sent else "(not connected)",
                                         GESTURE_NAMES[gesture], self.button_count))

    def set_message(self, text):
        self.message = text[:40]
        self.radio.ble.gatts_write(self.handles[MESSAGE], self.message.encode())
        print("Message set here: a phone that reads it now gets it")

    def set_vault(self, text):
        self.radio.ble.gatts_write(self.handles[VAULT], text[:40].encode())
        print("Vault set here")

    def tick(self):
        """From the main loop: temperature every 2 s, uptime every second."""
        now = ticks_ms()
        if ticks_diff(now, self._seconds_at) < 1000:
            return
        self._seconds_at = now
        seconds = now // 1000
        self.radio.ble.gatts_write(self.handles[UPTIME], struct.pack("<I", seconds))
        if seconds % 2 == 0:
            celsius = chip_temperature()
            if celsius is not None:
                self.notify(TEMPERATURE, struct.pack("<h", int(celsius * 100)))

    def _written(self, data):
        connection, handle = data
        value = bytes(self.radio.ble.gatts_read(handle))
        if handle == self.handles[LED]:
            if len(value) != 3:
                print("< write LED: wants 3 bytes, got", len(value))
                return
            self.radio.led.claimed = (value[0], value[1], value[2])
            print("< write LED {:02X} {:02X} {:02X}".format(*value))
        elif handle == self.handles[MESSAGE]:
            self.message = value.decode("utf-8", "ignore")
            print('< write Message "{}"'.format(self.message))
        elif self.vault and handle == self.handles[VAULT]:
            print("< write Vault ({} bytes, over an encrypted link)".format(len(value)))
        elif self.on_extra:
            self.on_extra(connection, handle, value)

    on_extra = None  # B5 sets this for its own characteristics
