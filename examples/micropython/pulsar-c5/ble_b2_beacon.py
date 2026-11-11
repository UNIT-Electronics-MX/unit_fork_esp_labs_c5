"""B2 Be seen: the Pulsar C5 as a beacon. Live values ride in the advertisement.

Needs ble_kit.py saved on the board. Type help in the serial console.
BOOT: click = next mode, double-click = press (the counter in the packet goes up),
long press = advertising on/off. Scan with nRF Connect or LightBlue on a phone.
"""

import struct
from time import sleep_ms, ticks_diff, ticks_ms

import ble_kit as kit

NAME = "Bench B2"
TEST_COMPANY = 0xFFFF  # reserved by the Bluetooth SIG for tests: ours to use
BENCH_VERSION = 1
# The iBeacon "fleet" UUID: the Bench service's UUID. Major and minor pick one beacon in it.
IBEACON_UUID = bytes(kit.bench_uuid(0))
MODES = ("bench", "ibeacon", "url", "name")
URL_SCHEMES = ("http://www.", "https://www.", "http://", "https://")
URL_ENDINGS = (".com/", ".org/", ".edu/", ".net/", ".info/", ".biz/", ".gov/",
               ".com", ".org", ".edu", ".net", ".info", ".biz", ".gov")

settings = {"mode": "bench", "url": "https://espressif.com", "major": 1, "minor": 2, "power": -59,
            "interval": 100, "connectable": False, "scanresp": True, "on": True}
presses = 0
radio = kit.Radio(NAME)
radio.readvertise = True
console = kit.Console()
button = kit.Button()


def eddystone_url(url, power):
    """Eddystone-URL frame: 0x10, TX power at 0 m, scheme byte, the rest with .com/ as one byte."""
    for code, scheme in sorted(enumerate(URL_SCHEMES), key=lambda item: -len(item[1])):
        if url.startswith(scheme):
            rest, out = url[len(scheme):], bytearray((0x10, power & 0xFF, code))
            while rest:
                for index, ending in enumerate(URL_ENDINGS):
                    if rest.startswith(ending):
                        out.append(index)
                        rest = rest[len(ending):]
                        break
                else:
                    out.append(ord(rest[0]))
                    rest = rest[1:]
            return bytes(out) if len(out) <= 20 else None
    return None


def build():
    """Both packets for the current settings: (advertisement, scan response) or None if too long."""
    name = NAME.encode()
    adv, rsp = kit.advertise_flags(), b""
    mode = settings["mode"]
    if mode == "bench":
        celsius = kit.chip_temperature() or 0
        live = struct.pack("<HBHhH", TEST_COMPANY, BENCH_VERSION, presses, int(celsius * 100),
                           (ticks_ms() // 1000) & 0xFFFF)
        adv += kit.ad_field(kit.AD_NAME, name) + kit.ad_field(kit.AD_MANUFACTURER, live)
        rsp = kit.ad_field(kit.AD_TX_POWER, b"\x09") + kit.ad_field(kit.AD_APPEARANCE, b"\x40\x05")
    elif mode == "ibeacon":
        # Apple's layout. Major and minor are big-endian: the one exception in a little-endian packet.
        data = b"\x4c\x00\x02\x15" + IBEACON_UUID + struct.pack(">HHb", settings["major"], settings["minor"],
                                                                settings["power"])
        adv += kit.ad_field(kit.AD_MANUFACTURER, data)
        rsp = kit.ad_field(kit.AD_NAME, name)
    elif mode == "url":
        # Eddystone wants the power at 0 m: by convention the 1 m reference + 41 dB.
        frame = eddystone_url(settings["url"], settings["power"] + 41)
        if frame is None:
            return None
        adv += kit.ad_field(kit.AD_UUID16_ALL, b"\xaa\xfe") + kit.ad_field(kit.AD_SERVICE_DATA16, b"\xaa\xfe" + frame)
        rsp = kit.ad_field(kit.AD_NAME, name)
    else:
        adv += kit.ad_field(kit.AD_NAME, name) + kit.ad_field(kit.AD_TX_POWER, b"\x09")
    if len(adv) > 31:
        return None
    return adv, rsp if settings["scanresp"] else b""


def apply(announce=False):
    packets = build()
    if packets is None:
        print("That does not fit in 31 bytes: http(s)://, then at most 17 bytes (.com/ counts as 1)")
        return False
    if not settings["on"]:
        return True
    # Below 100 ms only connectable advertising is allowed.
    interval = settings["interval"] if settings["connectable"] else max(100, settings["interval"])
    radio.advertise(packets[0], packets[1], interval, settings["connectable"])
    if announce:
        print("+ {} {} every {} ms, {}+{} B".format(
            settings["mode"], "ADV_IND" if settings["connectable"] else "ADV_NONCONN/SCAN_IND",
            interval, len(packets[0]), len(packets[1])))
    return True


def cmd_bytes():
    packets = build()
    if packets is None:
        print("These settings do not fit")
        return
    for title, payload in zip(("advertisement", "scan response"), packets):
        print("{}: {} of 31 bytes".format(title, len(payload)))
        for kind, data in kit.ad_fields(payload):
            print("  [{:02X} {:02X} | {}] {}".format(len(data) + 1, kind, kit.hex_bytes(data),
                                                     kit.AD_NAMES.get(kind, "type 0x{:02X}".format(kind))))


def press():
    global presses
    presses = (presses + 1) & 0xFFFF
    print("press #{}: {}".format(presses, "in the bench packet" if settings["mode"] == "bench"
                                 else "counted (switch to mode bench to see it)"))
    apply()


def set_on(on):
    settings["on"] = on
    if on:
        apply(True)
    else:
        radio.stop_advertising()
        print("Advertising off: silent")


def run(text):
    command, rest = kit.split_command(text)
    lower = rest.lower()
    before = dict(settings)
    if command == "mode" and lower in MODES:
        settings["mode"] = lower
    elif command == "url" and rest:
        settings["url"], settings["mode"] = rest, "url"
    elif command == "beacon":
        try:
            major, minor = (int(part) for part in rest.split())
        except ValueError:
            major = minor = -1
        if not (0 <= major <= 65535 and 0 <= minor <= 65535):
            print("beacon <major> <minor>, each 0-65535")
            return
        settings.update(major=major, minor=minor, mode="ibeacon")
    elif command == "interval":
        try:
            ms = int(rest)
        except ValueError:
            ms = 0
        if not 20 <= ms <= 10_240:
            print("interval <ms>, 20-10240 (100 at least when not connectable)")
            return
        settings["interval"] = ms
    elif command in ("connectable", "scanresp") and lower in ("on", "off"):
        settings[command] = lower == "on"
    elif command == "adv":
        set_on(lower == "on" if lower in ("on", "off") else not settings["on"])
        return
    elif command == "press":
        press()
        return
    elif command == "bytes":
        cmd_bytes()
        return
    else:
        print("mode bench|ibeacon|url|name | url <https://...> | beacon <major> <minor> | interval <ms> |"
              " connectable on|off | scanresp on|off | adv [on|off] | press | bytes")
        return
    if not apply(True):
        settings.update(before)
        apply()


print("B2 Be seen on", radio.address())
apply(True)
refreshed = ticks_ms()
try:
    while True:
        text = console.line()
        if text:
            run(text)
        gesture = button.poll()
        if gesture == kit.CLICK:
            settings["mode"] = MODES[(MODES.index(settings["mode"]) + 1) % len(MODES)]
            apply(True)
        elif gesture == kit.DOUBLE:
            press()
        elif gesture == kit.LONG:
            set_on(not settings["on"])
        # The bench packet carries live values: refresh them every two seconds.
        if settings["on"] and settings["mode"] == "bench" and ticks_diff(ticks_ms(), refreshed) >= 2000:
            refreshed = ticks_ms()
            if not radio.connections:
                apply()
        radio.tick()
        sleep_ms(10)
finally:
    radio.stop_advertising()
    radio.ble.active(False)
    radio.led.off()
