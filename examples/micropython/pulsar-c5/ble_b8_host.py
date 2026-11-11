"""B8 Host: the roles swap. The Pulsar C5 is the central: it connects, explores, and hosts your BLE keyboard.

Needs ble_kit.py saved on the board. Type list, connect <n>, then host for a
keyboard or mouse. A keyboard pairs by typing the passkey printed here, then Enter.
Explore reads only the public basics (name, maker, battery), never vendor data.
BOOT: click = next device, double-click = connect or explore, long press = disconnect.
"""

import bluetooth
from time import sleep_ms, ticks_diff, ticks_ms

import ble_kit as kit

UUID_NAMES = {0x1800: "Generic Access", 0x1801: "Generic Attribute", 0x180A: "Device Information",
              0x180F: "Battery", 0x1812: "HID", 0x2A00: "Device Name", 0x2A01: "Appearance",
              0x2A19: "Battery Level", 0x2A24: "Model", 0x2A26: "Firmware", 0x2A29: "Manufacturer",
              0x2A4A: "HID Information", 0x2A4B: "Report Map", 0x2A4D: "Report", 0x2A4E: "Protocol Mode",
              0x2A04: "Preferred Conn Params", 0x2A05: "Service Changed", 0x2A22: "Boot Keyboard In",
              0x2A23: "System ID", 0x2A27: "Hardware Rev", 0x2A28: "Software Rev", 0x2A32: "Boot Keyboard Out",
              0x2A33: "Boot Mouse In", 0x2A4C: "HID Control Point", 0x2A50: "PnP ID", 0xFE59: "Nordic DFU"}
READ_BASICS = (0x2A00, 0x2A01, 0x2A19, 0x2A24, 0x2A26, 0x2A29)
TEXT_BASICS = (0x2A00, 0x2A24, 0x2A26, 0x2A29)
HID, REPORT, REPORT_MAP, CCCD = (bluetooth.UUID(n) for n in (0x1812, 0x2A4D, 0x2A4B, 0x2902))
PROP_READ, PROP_NOTIFY = 0x02, 0x10
US_KEYS = "abcdefghijklmnopqrstuvwxyz1234567890\n\x1b\b\t -=[]\\#;'`,./"

bonds = kit.BondStore("ble_host_bonds.json")
radio = kit.Radio("PulsarC5-B8", bonds=bonds, io=kit.IO_DISPLAY_YESNO, mitm=True)
radio.readvertise = False
console = kit.Console()
button = kit.Button()
devices = {}   # connectable devices: address -> (addr_type, name, rssi, seen)
picked = None
peer = {"connection": None, "services": [], "chars": [], "descriptors": []}
ops = []       # GATT client operations run one at a time: the stack allows one in flight
busy = False
typed = ""
held = set()  # keys already down in the previous keyboard report


def uuid_name(uuid):
    raw = bytes(uuid)
    if len(raw) == 2:
        number = raw[0] | raw[1] << 8
        return UUID_NAMES.get(number, "0x{:04X}".format(number))
    return str(uuid)[5:-1]


def uuid16(uuid):
    raw = bytes(uuid)
    return raw[0] | raw[1] << 8 if len(raw) == 2 else None


def queue(*operations):
    ops.extend(operations)
    next_op()


def next_op():
    global busy
    busy = False
    while ops and not busy and peer["connection"] is not None:
        operation = ops.pop(0)
        try:
            busy = operation() is not False
        except OSError as error:
            print("GATT operation refused ({}): skipped".format(error))


def done(_data=None):
    next_op()


# ---- Scan -------------------------------------------------------------------------------------

def on_scan(data):
    addr_type, address, adv_type, rssi, payload = data
    if adv_type not in (0, 1, 4):  # connectable advertisements (and their scan responses)
        return
    address = bytes(address)
    old = devices.get(address)
    name = kit.ad_name(payload) or (old[1] if old else "")
    if adv_type == 4 and not old:
        return
    devices[address] = (addr_type, name, rssi, ticks_ms())


def rows():
    now = ticks_ms()
    found = [(address, info) for address, info in devices.items() if ticks_diff(now, info[3]) < 30_000]
    found.sort(key=lambda row: -row[1][2])
    return found


def start_scan():
    radio.ble.gap_scan(0, 100_000, 100_000, True)
    radio.scanning = True
    print("Scanning for connectable devices: type list")


def stop_scan():
    radio.ble.gap_scan(None)
    radio.scanning = False


def cmd_list():
    found = rows()
    for n, (address, (addr_type, name, rssi, _seen)) in enumerate(found, 1):
        print("{:2d} {:4d} dBm  {}  {:14s}  {}".format(n, rssi, kit.mac(address),
                                                     kit.address_kind(addr_type, address), name or "?"))
    if not found:
        print("Nothing connectable heard yet. Put the keyboard in pairing mode.")


# ---- Connect and explore ------------------------------------------------------------------------

def connect(text):
    global picked
    found = rows()
    if text:
        try:
            picked = found[int(text) - 1][0]
        except (ValueError, IndexError):
            print("connect <n>: a row number from list")
            return
    if picked not in devices:
        print("list, then connect <n>")
        return
    stop_scan()
    addr_type, name, _rssi, _seen = devices[picked]
    print("Connecting to", name or kit.mac(picked))
    radio.ble.gap_connect(addr_type, picked)


def on_connect(data):
    peer["connection"] = data[0]
    explore()


def on_disconnect(_data):
    peer.update(connection=None, services=[], chars=[], descriptors=[])
    ops.clear()
    start_scan()


def explore():
    peer.update(services=[], chars=[], descriptors=[])
    ops.clear()
    queue(lambda: radio.ble.gattc_discover_services(peer["connection"]))


def on_service(data):
    _connection, start, end, uuid = data
    peer["services"].append((start, end, bluetooth.UUID(uuid)))


def on_services_done(_data):
    for start, end, _uuid in peer["services"]:
        ops.append(lambda start=start, end=end: radio.ble.gattc_discover_characteristics(
            peer["connection"], start, end))
    ops.append(read_basics)
    next_op()


def on_char(data):
    _connection, _end, value_handle, properties, uuid = data
    peer["chars"].append((value_handle, properties, bluetooth.UUID(uuid)))


def read_basics():
    print_tree()
    for value_handle, properties, uuid in peer["chars"]:
        if properties & PROP_READ and uuid16(uuid) in READ_BASICS:
            ops.append(lambda handle=value_handle: radio.ble.gattc_read(peer["connection"], handle))
    return False  # nothing in flight: go straight on


def print_tree():
    print("{} services, {} characteristics:".format(len(peer["services"]), len(peer["chars"])))
    for start, end, uuid in peer["services"]:
        print("  {} [{}-{}]".format(uuid_name(uuid), start, end))
        for value_handle, properties, char_uuid in peer["chars"]:
            if start <= value_handle <= end:
                flags = "".join(flag for bit, flag in ((0x02, "R"), (0x08, "W"), (0x04, "w"), (0x10, "N"),
                                                       (0x20, "I")) if properties & bit)
                print("    {:5d} {:4s} {}".format(value_handle, flags, uuid_name(char_uuid)))
    if any(uuid == HID for _s, _e, uuid in peer["services"]):
        print("It is a HID device: type host to use it")


def on_read(data):
    _connection, value_handle, value = data
    value = bytes(value)
    uuid = next((u for h, _p, u in peer["chars"] if h == value_handle), None)
    number = uuid16(uuid) if uuid else None
    if number in TEXT_BASICS:
        shown = value.decode("utf-8", "ignore")
    elif number == 0x2A19 and value:
        shown = "{} %".format(value[0])
    else:
        shown = kit.hex_bytes(value)
    print("  read {}: {}".format(uuid_name(uuid) if uuid else value_handle, shown))


# ---- Host a keyboard or mouse -----------------------------------------------------------------

def host():
    if peer["connection"] is None:
        print("connect to a keyboard or mouse first")
        return
    hid = [(start, end) for start, end, uuid in peer["services"] if uuid == HID]
    if not hid:
        print("No HID service here: not a keyboard or mouse")
        return
    link = radio.connections.get(peer["connection"], {})
    if not link.get("encrypted"):
        print("Pairing first: HID devices only talk over an encrypted link")
        radio.ble.gap_pair(peer["connection"])
        return  # on_encryption calls host() again
    start, end = hid[0]
    queue(lambda: radio.ble.gattc_discover_descriptors(peer["connection"], start, end), subscribe)


def on_descriptor(data):
    _connection, handle, uuid = data
    peer["descriptors"].append((handle, bluetooth.UUID(uuid)))


def subscribe():
    """Write 01 00 into the CCCD after each input report: the device may now notify."""
    reports = sorted(h for h, p, u in peer["chars"] if u == REPORT and p & PROP_NOTIFY)
    all_handles = sorted(h for h, _p, _u in peer["chars"])
    for value_handle in reports:
        following = [h for h in all_handles if h > value_handle]
        limit = following[0] if following else 0xFFFF
        cccd = next((h for h, u in sorted(peer["descriptors"]) if u == CCCD and value_handle < h < limit), None)
        if cccd:
            ops.append(lambda cccd=cccd: radio.ble.gattc_write(peer["connection"], cccd, b"\x01\x00", 1))
    print("Subscribed to {} input report(s): type or move the mouse".format(len(reports)))
    return False


def on_encryption(data):
    connection, encrypted = data[0], data[1]
    if encrypted and connection == peer["connection"]:
        host()


def on_notify(data):
    global typed, held
    _connection, value_handle, report = data
    report = bytes(report)
    if len(report) == 8 and report[1] == 0:  # a keyboard: modifiers, reserved, six keys
        shift = report[0] & 0x22
        keys = set(code for code in report[2:] if code)
        for code in keys - held:  # only keys that just went down
            if 0x04 <= code <= 0x38 and code - 0x04 < len(US_KEYS):
                char = US_KEYS[code - 0x04]
                char = char.upper() if shift else char
                if char == "\n":
                    print("< typed:", typed)
                    typed = ""
                elif char == "\b":
                    typed = typed[:-1]
                elif char.isalpha() or char.isdigit() or char in " -=[]\\#;'`,./":
                    typed += char
        held = keys
        print("< key report", kit.hex_bytes(report), "|", typed)
    elif 3 <= len(report) <= 7:  # most mice: buttons, x, y, wheel (signed)
        x = report[1] - 256 if report[1] > 127 else report[1]
        y = report[2] - 256 if report[2] > 127 else report[2]
        print("< mouse buttons {:03b} x {:+d} y {:+d}  ({})".format(report[0] & 7, x, y, kit.hex_bytes(report)))
    else:
        print("< report {}: {}".format(value_handle, kit.hex_bytes(report)))


def run(text):
    command, rest = kit.split_command(text)
    if command == "list":
        cmd_list()
    elif command == "scan":
        stop_scan() if radio.scanning else start_scan()
    elif command == "connect":
        connect(rest)
    elif command == "explore" and peer["connection"] is not None:
        explore()
    elif command == "pair" and peer["connection"] is not None:
        radio.ble.gap_pair(peer["connection"])
    elif command == "host":
        host()
    elif command == "passkey":
        radio.enter_passkey(rest)
    elif command in ("yes", "no"):
        radio.answer(command == "yes")
    elif command == "disconnect" and peer["connection"] is not None:
        radio.ble.gap_disconnect(peer["connection"])
    elif command == "forget":
        bonds.forget()
        print("Host bonds deleted")
    else:
        print("list | scan | connect <n> | explore | pair | host | passkey <digits> | yes | no | disconnect | forget")


for event, handler in ((kit.IRQ_SCAN_RESULT, on_scan), (kit.IRQ_PERIPHERAL_CONNECT, on_connect),
                       (kit.IRQ_PERIPHERAL_DISCONNECT, on_disconnect), (kit.IRQ_GATTC_SERVICE_RESULT, on_service),
                       (kit.IRQ_GATTC_SERVICE_DONE, on_services_done), (kit.IRQ_GATTC_CHARACTERISTIC_RESULT, on_char),
                       (kit.IRQ_GATTC_CHARACTERISTIC_DONE, done), (kit.IRQ_GATTC_DESCRIPTOR_RESULT, on_descriptor),
                       (kit.IRQ_GATTC_DESCRIPTOR_DONE, done), (kit.IRQ_GATTC_READ_RESULT, on_read),
                       (kit.IRQ_GATTC_READ_DONE, done), (kit.IRQ_GATTC_WRITE_DONE, done),
                       (kit.IRQ_GATTC_NOTIFY, on_notify), (kit.IRQ_ENCRYPTION_UPDATE, on_encryption)):
    radio.on(event, handler)
print("B8 Host on", radio.address())
start_scan()
try:
    while True:
        text = console.line()
        if text:
            run(text)
        gesture = button.poll()
        found = rows()
        if gesture == kit.CLICK and found:
            addresses = [address for address, _info in found]
            picked = addresses[(addresses.index(picked) + 1) % len(addresses)] if picked in addresses else addresses[0]
            print("Picked", devices[picked][1] or kit.mac(picked))
        elif gesture == kit.DOUBLE:
            explore() if peer["connection"] is not None else connect("")
        elif gesture == kit.LONG and peer["connection"] is not None:
            radio.ble.gap_disconnect(peer["connection"])
        radio.tick()
        sleep_ms(10)
finally:
    radio.ble.gap_scan(None)
    if peer["connection"] is not None:
        radio.ble.gap_disconnect(peer["connection"])
    radio.ble.active(False)
    radio.led.off()
