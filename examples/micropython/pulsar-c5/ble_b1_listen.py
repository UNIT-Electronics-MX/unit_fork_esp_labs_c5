"""B1 Listen: the Pulsar C5 hears every Bluetooth LE device around it.

Needs ble_kit.py saved on the board. Type help in the serial console.
BOOT: click = next device, double-click = bytes, long press = scan on/off.
The LED blinks blue while scanning.
"""

from time import sleep_ms, ticks_diff, ticks_ms

import ble_kit as kit

ACTIVE = True       # active scan: ask every scannable device for its scan response
INTERVAL_MS = 100   # how often the radio starts listening
WINDOW_MS = 100     # how long it listens each time (window = interval: all the time)
FORGET_MS = 30_000  # list shows devices heard in the last 30 s
MAX_DEVICES = 60    # keep memory use small: the oldest device makes room

radio = kit.Radio("PulsarC5-B1")
console = kit.Console()
button = kit.Button()
devices = {}  # address bytes -> dict
near = None   # hide devices weaker than this many dBm
picked = None
watching = False
packets = 0
stats_at = ticks_ms()


def kind_of(device):
    """A guess from what the device chose to say about itself."""
    for kind, data in kit.ad_fields(device["adv"] + device["rsp"]):
        if kind == kit.AD_MANUFACTURER and len(data) >= 4 and data[:4] == b"\x4c\x00\x02\x15":
            return "beacon"
        if kind == kit.AD_SERVICE_DATA16 and data[:2] == b"\xaa\xfe":
            return "beacon"
        if kind in (0x02, 0x03) and b"\x12\x18" in bytes(data):
            return "keyboard/mouse"
        if kind == kit.AD_APPEARANCE and len(data) == 2:
            category = (data[0] | data[1] << 8) >> 6
            return {1: "phone", 2: "computer", 3: "watch", 15: "keyboard/mouse", 33: "audio",
                    13: "sensor", 21: "sensor", 49: "light"}.get(category, "thing")
    company = maker_of(device)
    return "phone" if company in ("Apple", "Samsung", "Google") else "thing"


def maker_of(device):
    for kind, data in kit.ad_fields(device["adv"] + device["rsp"]):
        if kind == kit.AD_MANUFACTURER and len(data) >= 2:
            company = data[0] | data[1] << 8
            return kit.COMPANIES.get(company, "0x{:04X}".format(company))
    return ""


def distance(rssi, at_1m=-59):
    """Metres from signal strength: a factor-of-two guess (path loss exponent 2.5)."""
    return 10 ** ((at_1m - rssi) / 25)


def on_scan_result(data):
    global packets
    addr_type, address, adv_type, rssi, payload = data
    address = bytes(address)
    payload = bytes(payload)
    now = ticks_ms()
    packets += 1
    device = devices.get(address)
    if device is None:
        if len(devices) >= MAX_DEVICES:
            oldest = min(devices, key=lambda key: devices[key]["seen"])
            del devices[oldest]
        device = {"type": addr_type, "adv": b"", "rsp": b"", "count": 0, "seen": now, "rssi": rssi}
        devices[address] = device
    gap = ticks_diff(now, device["seen"])
    device["seen"], device["rssi"] = now, rssi
    device["count"] += 1
    if adv_type == 4:  # SCAN_RSP: the second packet, sent only to an active scanner
        device["rsp"] = payload
    else:
        device["adv"] = payload
    if watching and address == picked:
        print("< +{:5d} ms  {:4d} dBm  {:2d} B  {}".format(gap, rssi, len(payload),
                                                          "scan rsp" if adv_type == 4 else "adv"))


def start_scan():
    radio.ble.gap_scan(0, INTERVAL_MS * 1000, WINDOW_MS * 1000, ACTIVE)
    radio.scanning = True
    print("+ {} scan, {}/{} ms ({}% listening)".format(
        "active" if ACTIVE else "passive", WINDOW_MS, INTERVAL_MS, WINDOW_MS * 100 // INTERVAL_MS))


def stop_scan():
    radio.ble.gap_scan(None)
    radio.scanning = False
    print("Scan paused")


def table():
    now = ticks_ms()
    rows = [(address, device) for address, device in devices.items()
            if ticks_diff(now, device["seen"]) < FORGET_MS and (near is None or device["rssi"] >= near)]
    rows.sort(key=lambda row: -row[1]["rssi"])
    return rows


def cmd_list():
    rows = table()
    print(" #  dBm     m  kind            address            type            pkts  name / maker")
    for n, (address, device) in enumerate(rows, 1):
        name = kit.ad_name(device["adv"] + device["rsp"]) or maker_of(device)
        print("{:2d} {:4d} {:5.1f}  {:14s}  {}  {:14s} {:5d}  {}".format(
            n, device["rssi"], distance(device["rssi"]), kind_of(device), kit.mac(address),
            kit.address_kind(device["type"], address), device["count"], name))
    if not rows:
        print("Nothing heard yet. Phones advertise more with the screen on.")


def cmd_pick(text):
    global picked
    rows = table()
    try:
        picked = rows[int(text) - 1][0]
    except (ValueError, IndexError):
        print("pick <n>: a row number from list")
        return
    print("Picked", kit.mac(picked))


def cmd_next():
    global picked
    rows = [address for address, _device in table()]
    if not rows:
        print("Nothing heard yet")
        return
    picked = rows[(rows.index(picked) + 1) % len(rows)] if picked in rows else rows[0]
    print("Picked", kit.mac(picked), kit.ad_name(devices[picked]["adv"] + devices[picked]["rsp"]))


def print_packet(title, payload):
    if not payload:
        print(title + ": none (passive mode, or the device does not send one)")
        return
    print("{}: {} bytes".format(title, len(payload)))
    for kind, data in kit.ad_fields(payload):
        print("  [{:02X} {:02X} | {}] {}".format(len(data) + 1, kind, kit.hex_bytes(data),
                                                 kit.AD_NAMES.get(kind, "type 0x{:02X}".format(kind))))


def cmd_bytes():
    device = devices.get(picked)
    if device is None:
        print("pick a device first: list, then pick <n>")
        return
    print(kit.mac(picked), kit.address_kind(device["type"], picked))
    print_packet("advertisement", device["adv"])
    print_packet("scan response", device["rsp"])


def cmd_stats():
    global packets, stats_at
    seconds = max(1, ticks_diff(ticks_ms(), stats_at)) / 1000
    print("{:.0f} packets/s from {} devices ({} scan, {}/{} ms)".format(
        packets / seconds, len(table()), "active" if ACTIVE else "passive", WINDOW_MS, INTERVAL_MS))
    packets, stats_at = 0, ticks_ms()


def run(text):
    global ACTIVE, INTERVAL_MS, WINDOW_MS, near, watching
    command, rest = kit.split_command(text)
    if command == "list":
        cmd_list()
    elif command == "pick":
        cmd_pick(rest)
    elif command == "next":
        cmd_next()
    elif command == "bytes":
        cmd_bytes()
    elif command == "watch":
        watching = rest != "off" and picked is not None
        print("Watching" if watching else "Watch off (pick a device to watch it)")
    elif command == "mode" and rest in ("active", "passive"):
        ACTIVE = rest == "active"
        stop_scan()
        start_scan()
    elif command == "timing":
        try:
            interval, window = (int(part) for part in rest.split())
        except ValueError:
            interval = window = 0
        if not 3 <= window <= interval <= 10_000:
            print("timing <interval> <window> in ms, window <= interval")
            return
        INTERVAL_MS, WINDOW_MS = interval, window
        stop_scan()
        start_scan()
    elif command == "near":
        try:
            near = None if rest == "off" else int(rest)
        except ValueError:
            print("near <dBm> (e.g. near -70) or near off")
            return
        print("Showing", "every device" if near is None else "devices at {} dBm or stronger".format(near))
    elif command == "scan":
        stop_scan() if radio.scanning else start_scan()
    elif command == "stats":
        cmd_stats()
    elif command == "clear":
        devices.clear()
        print("Forgot every device")
    else:
        print("list | pick <n> | next | bytes | watch [off] | mode active|passive |"
              " timing <interval> <window> | near <dBm>|off | scan | stats | clear")


radio.on(kit.IRQ_SCAN_RESULT, on_scan_result)
print("B1 Listen on", radio.address())
start_scan()
print("Type list, or help")
try:
    while True:
        text = console.line()
        if text:
            run(text)
        gesture = button.poll()
        if gesture == kit.CLICK:
            cmd_next()
        elif gesture == kit.DOUBLE:
            cmd_bytes()
        elif gesture == kit.LONG:
            stop_scan() if radio.scanning else start_scan()
        radio.tick()
        sleep_ms(10)
finally:
    # Leave the radio off after Stop, so the next script starts clean.
    radio.ble.gap_scan(None)
    radio.ble.active(False)
    radio.led.off()
