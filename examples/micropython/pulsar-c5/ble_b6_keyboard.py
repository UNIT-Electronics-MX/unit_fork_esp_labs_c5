"""B6 Keyboard & media: the Pulsar C5 as a Bluetooth LE keyboard with media keys (HID over GATT).

Needs ble_kit.py saved on the board. Pair "Bench B6" from the phone's or the
computer's Bluetooth settings (not from nRF Connect). Then type in the serial
console: type hello, key cmd+space, media up. Type help for the list.
BOOT: click = play/pause, double-click = next track, long press = mute.
"""

import bluetooth
import struct
from time import sleep_ms

import ble_kit as kit

NAME = "Bench B6"
KEYBOARD_ID, MEDIA_ID = 1, 2
MEDIA = {"play": 0x00CD, "next": 0x00B5, "prev": 0x00B6, "up": 0x00E9, "down": 0x00EA, "mute": 0x00E2}
MODIFIERS = {"ctrl": 0x01, "shift": 0x02, "alt": 0x04, "option": 0x04, "cmd": 0x08, "gui": 0x08, "win": 0x08}
KEYS = {"enter": 0x28, "esc": 0x29, "backspace": 0x2A, "tab": 0x2B, "space": 0x2C, "delete": 0x4C,
        "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52, "home": 0x4A, "end": 0x4D,
        "pageup": 0x4B, "pagedown": 0x4E}
KEYS.update({"f{}".format(n): 0x39 + n for n in range(1, 13)})
# US layout: the character a key types, unshifted and shifted, from usage 0x2D on.
PUNCTUATION = {0x2D: "-_", 0x2E: "=+", 0x2F: "[{", 0x30: "]}", 0x31: "\\|", 0x33: ";:",
               0x34: "'\"", 0x35: "`~", 0x36: ",<", 0x37: ".>", 0x38: "/?"}
SHIFTED_DIGITS = "!@#$%^&*()"

# The Report Map: a small program in the HID descriptor language that tells the computer
# what our two reports mean. Report 1 = keyboard (8 bytes), report 2 = one consumer usage (2 bytes).
REPORT_MAP = bytes((
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x85, KEYBOARD_ID,  # Keyboard, Report ID 1
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
    0x75, 0x01, 0x95, 0x08, 0x81, 0x02,                    # byte 0: one bit per modifier
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01,                    # byte 1: reserved
    0x95, 0x05, 0x75, 0x01, 0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x91, 0x02,  # LEDs out
    0x95, 0x01, 0x75, 0x03, 0x91, 0x01,                    # pad the LEDs to a byte
    0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0x65,
    0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00,        # bytes 2-7: up to six keys held
    0xC0,
    0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01, 0x85, MEDIA_ID,     # Consumer Control, Report ID 2
    0x15, 0x00, 0x26, 0xFF, 0x03, 0x19, 0x00, 0x2A, 0xFF, 0x03,
    0x75, 0x10, 0x95, 0x01, 0x81, 0x00,                    # one 16-bit usage, 0 = released
    0xC0,
))

REPORT_REF = bluetooth.UUID(0x2908)  # which report a characteristic carries: (ID, 1 input / 2 output)
ENC_READ = kit.FLAG_READ | kit.FLAG_READ_ENCRYPTED

bonds = kit.BondStore()
radio = kit.Radio(NAME, bonds=bonds, io=kit.IO_NO_INPUT_OUTPUT)  # Just Works: a keyboard has no screen
standard, standard_values = kit.device_information()
hid_service = (bluetooth.UUID(0x1812), (
    (bluetooth.UUID(0x2A4A), kit.FLAG_READ, ()),                                      # HID Information
    (bluetooth.UUID(0x2A4B), ENC_READ, ()),                                           # Report Map
    (bluetooth.UUID(0x2A4C), kit.FLAG_WRITE_NO_RESPONSE, ()),                         # Control Point
    (bluetooth.UUID(0x2A4E), kit.FLAG_READ | kit.FLAG_WRITE_NO_RESPONSE, ()),         # Protocol Mode
    (bluetooth.UUID(0x2A4D), ENC_READ | kit.FLAG_NOTIFY,
     ((REPORT_REF, kit.FLAG_READ, bytes((KEYBOARD_ID, 1))),)),                       # keyboard in
    (bluetooth.UUID(0x2A4D), ENC_READ | kit.FLAG_NOTIFY,
     ((REPORT_REF, kit.FLAG_READ, bytes((MEDIA_ID, 1))),)),                          # media in
    (bluetooth.UUID(0x2A4D), ENC_READ | kit.FLAG_WRITE | kit.FLAG_WRITE_NO_RESPONSE | kit.FLAG_WRITE_ENCRYPTED,
     ((REPORT_REF, kit.FLAG_READ, bytes((KEYBOARD_ID, 2))),)),                       # Caps/Num Lock out
))
device_info = (standard[0][0], standard[0][1] + ((bluetooth.UUID(0x2A50), kit.FLAG_READ, ()),))
rows = kit.register(radio.ble, [hid_service, device_info, standard[1]])
info, report_map, _control, protocol, keyboard_in, media_in, leds_out = (row[0] for row in rows[0])
for handle, value in zip([row[0] for row in rows[1]] + [rows[2][0][0]],
                         standard_values[0] + (struct.pack("<BHHH", 2, 0x303A, 0x8001, 0x0100),)
                         + standard_values[1]):
    radio.ble.gatts_write(handle, value)
radio.ble.gatts_write(info, b"\x11\x01\x00\x02")  # HID 1.11, no country, normally connectable
radio.ble.gatts_write(report_map, REPORT_MAP)
radio.ble.gatts_write(protocol, b"\x01")  # report protocol
radio.ble.gatts_write(keyboard_in, bytes(8))
radio.ble.gatts_write(media_in, bytes(2))
console = kit.Console()
button = kit.Button()


def ready():
    """HID hosts only listen over an encrypted link."""
    secure = [c for c, link in radio.connections.items() if link["encrypted"]]
    if not secure:
        print("Not paired yet: pair Bench B6 in the phone's Bluetooth settings")
    return secure


def send(handle, report):
    for connection in ready():
        try:
            radio.ble.gatts_notify(connection, handle, report)
        except OSError:
            pass


def press(modifiers, usage):
    send(keyboard_in, bytes((modifiers, 0, usage, 0, 0, 0, 0, 0)))
    sleep_ms(12)
    send(keyboard_in, bytes(8))  # release: the next press of the same key counts again
    sleep_ms(12)


def usage_of(char):
    """(modifiers, usage) that types this character on a US layout, or None."""
    if "a" <= char <= "z":
        return 0, 0x04 + ord(char) - ord("a")
    if "A" <= char <= "Z":
        return 0x02, 0x04 + ord(char) - ord("A")
    if "1" <= char <= "9":
        return 0, 0x1E + ord(char) - ord("1")
    if char == "0":
        return 0, 0x27
    if char in SHIFTED_DIGITS:
        return 0x02, 0x1E + SHIFTED_DIGITS.index(char)
    if char == " ":
        return 0, 0x2C
    for usage, pair in PUNCTUATION.items():
        if char in pair:
            return (0x02 if pair.index(char) else 0), usage
    return None


def type_text(text):
    if not ready():
        return
    for char in text:
        key = usage_of(char)
        if key:
            press(*key)
        else:
            print("Skipped", repr(char), "(not on a US keyboard)")
    print("Typed {} characters (the computer's layout should be US)".format(len(text)))


def key_combo(text):
    modifiers, usage = 0, None
    for part in text.lower().split("+"):
        if part in MODIFIERS:
            modifiers |= MODIFIERS[part]
        elif part in KEYS:
            usage = KEYS[part]
        elif len(part) == 1 and usage_of(part):
            usage = usage_of(part)[1]
        else:
            print("Unknown key", part)
            return
    if ready():
        press(modifiers, usage or 0)
        print("Key", text)


def media(name):
    if name not in MEDIA:
        print("media play|next|prev|up|down|mute")
        return
    if ready():
        send(media_in, struct.pack("<H", MEDIA[name]))
        sleep_ms(12)
        send(media_in, bytes(2))
        print("Media", name)


def on_write(data):
    _connection, handle = data
    if handle == leds_out:
        lights = radio.ble.gatts_read(handle)[0]
        print("< lock lights: Num {} Caps {}".format("on" if lights & 1 else "off", "on" if lights & 2 else "off"))


def run(text):
    command, rest = kit.split_command(text)
    if command == "type" and rest:
        type_text(rest)
    elif command == "key" and rest:
        key_combo(rest)
    elif command == "media":
        media(rest.lower())
    elif command == "forget":
        bonds.forget()
        print("Bond deleted. Forget Bench B6 on the phone or computer too")
    else:
        print("type <text> | key <name|combo, e.g. enter, f5, cmd+space> | media play|next|prev|up|down|mute | forget")


radio.on(kit.IRQ_GATTS_WRITE, on_write)
print("B6 Keyboard & media on", radio.address())
radio.advertise(kit.advertise_flags() + kit.ad_field(kit.AD_APPEARANCE, b"\xc1\x03")  # 0x03C1 keyboard
                + kit.uuid_field(bluetooth.UUID(0x1812)), kit.ad_field(kit.AD_NAME, NAME.encode()))
print("Advertising as", NAME, "-", bonds.count(), "bonded device(s)")
try:
    while True:
        text = console.line()
        if text:
            run(text)
        gesture = button.poll()
        if gesture:
            media({kit.CLICK: "play", kit.DOUBLE: "next", kit.LONG: "mute"}[gesture])
        radio.tick()
        sleep_ms(10)
finally:
    radio.stop_advertising()
    radio.ble.active(False)
    radio.led.off()
