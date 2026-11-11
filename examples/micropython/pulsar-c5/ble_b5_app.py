"""B5 Phone app: B3's Bench service plus sketch, live sensors and a speed test for esp-labs' ble-app.

Needs ble_kit.py saved on the board. Open the ble-app (Web Bluetooth in Chrome,
Bluefy on iPhone) and connect to "Bench B5". The Pulsar C5 has no screen: the
sketch's commands are counted here and the RGB LED takes the last colour drawn.
The touch line carries the A2 (GPIO4) reading instead: the ESP32-C5 has no touch sensor.
"""

import gc
import struct
from machine import ADC, Pin
from time import sleep_ms, ticks_diff, ticks_ms

import ble_kit as kit

SENSOR_PERIOD_MS = 200
CANVAS_OPS = {0x01: ("clear", 3), 0x02: ("stroke", 5), 0x03: ("rect", 10), 0x04: ("text", 8), 0x05: ("dot", 7)}
CANVAS, SENSORS, SPEED, LINK = range(4)

extras = (
    kit.characteristic(kit.bench_uuid(0x10), kit.FLAG_WRITE_NO_RESPONSE | kit.FLAG_WRITE, b"Canvas: sketch commands"),
    kit.characteristic(kit.bench_uuid(0x11), kit.FLAG_READ | kit.FLAG_NOTIFY, b"Sensors: temp, A2, heap, RSSI"),
    kit.characteristic(kit.bench_uuid(0x12), kit.FLAG_WRITE | kit.FLAG_NOTIFY, b"Speed test"),
    kit.characteristic(kit.bench_uuid(0x13), kit.FLAG_WRITE, b"Link: 1 fast 2 slow 3 2M 4 1M 5 DLE"),
)
radio = kit.Radio("Bench B5")
bench = kit.Bench(radio, extras=extras)
handles = bench.extra_handles
radio.ble.gatts_set_buffer(handles[CANVAS], 244)
radio.ble.gatts_set_buffer(handles[SPEED], 8)
console = kit.Console()
button = kit.Button()
analog = ADC(Pin(4), atten=ADC.ATTN_11DB)
canvas = {"writes": 0, "bytes": 0, "commands": 0}
speed = None  # the run in progress: {"connection", "target", "sent", "seq", "started", "retries"}
sequence = 0


def rgb565(colour):
    """The sketch's colours are RGB565: 5 bits red, 6 green, 5 blue."""
    return ((colour >> 11) & 0x1F) * 255 // 31, ((colour >> 5) & 0x3F) * 255 // 63, (colour & 0x1F) * 255 // 31


def on_canvas(value):
    """Walk one write. Each command starts with its opcode and a little-endian colour."""
    i, names, last = 0, [], None
    while i < len(value):
        op = CANVAS_OPS.get(value[i])
        size = op[1] if op else 0
        if op and i + size <= len(value):
            if value[i] == 0x02:
                size += value[i + 4] * 3  # stroke: n points of 3 bytes
            elif value[i] == 0x04:
                size += value[i + 7]  # text: len characters
        if op is None or i + size > len(value):
            print("< canvas: {} at byte {}, the rest is ignored".format(
                "unknown opcode" if op is None else "cut short", i))
            break
        last = value[i + 1] | value[i + 2] << 8
        names.append(op[0])
        i += size
    canvas["writes"] += 1
    canvas["bytes"] += len(value)
    canvas["commands"] += len(names)
    if last is not None:
        radio.led.claimed = rgb565(last)
    if canvas["writes"] % 20 == 1:
        print("< canvas write #{}: {} ({} bytes)".format(canvas["writes"], ", ".join(names), len(value)))


def on_speed(connection, value):
    global speed
    if value[:1] == b"\x00":
        speed = None
        print("Speed test stopped")
    elif len(value) == 5 and value[0] == 1:
        target = struct.unpack("<I", value[1:])[0]
        start_speed(connection, target)


def start_speed(connection, target):
    global speed
    speed = {"connection": connection, "target": target, "sent": 0, "seq": 0,
             "started": ticks_ms(), "retries": 0}
    print("Speed test: {} bytes".format(target))


def on_extra(connection, handle, value):
    if handle == handles[CANVAS]:
        on_canvas(value)
    elif handle == handles[SPEED]:
        on_speed(connection, value)
    elif handle == handles[LINK] and value:
        # The peripheral may only ask; MicroPython's bluetooth module has no call for it.
        print("< link op {}: connection interval, PHY and packet length are the phone's choice"
              " in MicroPython".format(value[0]))


bench.on_extra = on_extra


def speed_step():
    """Push notifications until the stack's buffers are full, then come back next loop."""
    global speed
    link = radio.connections.get(speed["connection"])
    if link is None:
        speed = None
        return
    size = link["mtu"] - 3
    packet = bytearray(size)
    while speed["sent"] < speed["target"]:
        struct.pack_into("<H", packet, 0, speed["seq"] % 0xFFFF)  # FFFF is the summary
        chunk = min(size, speed["target"] - speed["sent"])
        try:
            radio.ble.gatts_notify(speed["connection"], handles[SPEED], packet[:max(2, chunk)])
        except OSError:
            speed["retries"] += 1
            return
        speed["sent"] += chunk
        speed["seq"] += 1
    ms = max(1, ticks_diff(ticks_ms(), speed["started"]))
    # FF FF, bytes, ms, MTU, interval (unknown here: 0), PHY (1M)
    summary = struct.pack("<HIIHHB", 0xFFFF, speed["sent"], ms, link["mtu"], 0, 1)
    try:
        radio.ble.gatts_notify(speed["connection"], handles[SPEED], summary)
    except OSError:
        return
    print("Speed: {} bytes in {} ms = {:.1f} kB/s (MTU {}, {} waits)".format(
        speed["sent"], ms, speed["sent"] / ms, link["mtu"], speed["retries"]))
    speed = None


def send_sensors():
    global sequence
    celsius = kit.chip_temperature() or 0
    packet = struct.pack("<hIIbB", int(celsius * 100), analog.read_u16(), gc.mem_free(), 0, sequence)
    sequence = (sequence + 1) & 0xFF
    radio.ble.gatts_write(handles[SENSORS], packet)
    for connection in radio.connections:
        try:
            radio.ble.gatts_notify(connection, handles[SENSORS])
        except OSError:
            pass


def run(text):
    command, rest = kit.split_command(text)
    if command == "adv" and rest in ("on", "off"):
        bench.advertise() if rest == "on" else radio.stop_advertising()
    elif command == "clear":
        radio.led.claimed = None
        canvas.update(writes=0, bytes=0, commands=0)
        print("Sketch counters cleared")
    elif command == "canvas":
        print("{writes} writes, {bytes} bytes, {commands} commands".format(**canvas))
    elif command == "speed":
        if rest == "stop":
            on_speed(None, b"\x00")
            return
        try:
            target = int(rest)
        except ValueError:
            target = 0
        if not 1000 <= target <= 1_000_000 or not radio.connections:
            print("speed <bytes> (1000-1000000) with the app connected | speed stop")
            return
        start_speed(next(iter(radio.connections)), target)
    elif command == "press":
        bench.press(kit.CLICK)
    elif command == "message" and rest:
        bench.set_message(rest)
    elif command == "disconnect":
        radio.disconnect_all()
    else:
        print("adv on|off | clear | canvas | speed <bytes>|stop | press | message <text> | disconnect")


print("B5 Phone app on", radio.address())
bench.advertise()
sensors_at = ticks_ms()
try:
    while True:
        text = console.line()
        if text:
            run(text)
        gesture = button.poll()
        if gesture:
            bench.press(gesture)
        if speed:
            speed_step()
        elif radio.connections and ticks_diff(ticks_ms(), sensors_at) >= SENSOR_PERIOD_MS:
            sensors_at = ticks_ms()
            send_sensors()
        bench.tick()
        radio.tick()
        sleep_ms(1 if speed else 10)
finally:
    radio.stop_advertising()
    radio.ble.active(False)
    radio.led.off()
