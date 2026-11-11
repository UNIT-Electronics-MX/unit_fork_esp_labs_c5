"""B7 Music: the Pulsar C5 as a Bluetooth MIDI instrument.

Needs ble_kit.py saved on the board. MIDI carries notes, not sound: connect
"Bench B7" from GarageBand (Mac, iPhone, iPad), an Android synth app or the
ble-app Music tab in Chrome, and that app plays them. Type help in the console.
BOOT: click = next note of the scale, double-click = a chord, long press = all notes off.
Notes played on the other side light the RGB LED.
"""

import bluetooth
from time import sleep_ms, ticks_add, ticks_diff, ticks_ms

import ble_kit as kit

MIDI_SERVICE = bluetooth.UUID("03B80E5A-EDE8-4B33-A751-6CE34EC4C700")
MIDI_IO = bluetooth.UUID("7772E5DB-3868-4112-A1A9-F2669D106BF3")
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
SCALES = {"pent": (0, 2, 4, 7, 9), "minor": (0, 3, 5, 7, 10), "major": (0, 2, 4, 5, 7, 9, 11),
          "blues": (0, 3, 5, 6, 7, 10)}
# Public-domain tunes as (note name, beats); "-" is a rest.
TUNES = {
    "twinkle": "C4 1 C4 1 G4 1 G4 1 A4 1 A4 1 G4 2 F4 1 F4 1 E4 1 E4 1 D4 1 D4 1 C4 2",
    "ode": "E4 1 E4 1 F4 1 G4 1 G4 1 F4 1 E4 1 D4 1 C4 1 C4 1 D4 1 E4 1 E4 1.5 D4 .5 D4 2",
    "frere": "C4 1 D4 1 E4 1 C4 1 C4 1 D4 1 E4 1 C4 1 E4 1 F4 1 G4 2 E4 1 F4 1 G4 2",
}
# 16-step drum patterns on channel 10: kick, snare, closed hat (General MIDI 36, 38, 42).
BEATS = {"rock": ("x...x...x...x...", "....x.......x...", "x.x.x.x.x.x.x.x."),
         "house": ("x...x...x...x...", "................", "..x...x...x...x."),
         "funk": ("x..x..x...x.....", "....x..x....x...", "xxxxxxxxxxxxxxxx")}
DRUMS = (36, 38, 42)

radio = kit.Radio("Bench B7")
((midi_handle,),) = radio.ble.gatts_register_services(
    ((MIDI_SERVICE, ((MIDI_IO, kit.FLAG_READ | kit.FLAG_WRITE_NO_RESPONSE | kit.FLAG_NOTIFY),)),))
radio.ble.gatts_set_buffer(midi_handle, 64)
radio.ble.gatts_write(midi_handle, b"")  # a read returns an empty packet (the spec says so)
console = kit.Console()
button = kit.Button()
state = {"scale": "pent", "octave": 4, "channel": 0, "walk": 0, "bpm": 100}
pending_offs = []  # (time, note, channel)
tune = []          # (time, note or None, length ms)
drums = None       # {"beat": name, "step": n, "at": time}


def note_number(name):
    """'C4' -> 60, 'F#3' -> 54. Middle C is C4."""
    name = name.upper()
    base = NOTE_NAMES.index(name[:-1]) if name[:-1] in NOTE_NAMES else -1
    if base < 0 or not name[-1].isdigit():
        return None
    return 12 * (int(name[-1]) + 1) + base


def note_name(number):
    return "{}{}".format(NOTE_NAMES[number % 12], number // 12 - 1)


def send(messages):
    """One BLE-MIDI packet: a header with the top 6 bits of a 13-bit ms clock, then a
    timestamp byte (the low 7 bits) before each message."""
    now = ticks_ms() & 0x1FFF
    packet = bytearray((0x80 | (now >> 7),))
    for message in messages:
        packet.append(0x80 | (now & 0x7F))
        packet.extend(message)
    for connection in radio.connections:
        try:
            radio.ble.gatts_notify(connection, midi_handle, packet)
        except OSError:
            pass


def play(note, velocity=100, length_ms=300, channel=None):
    channel = state["channel"] if channel is None else channel
    send([bytes((0x90 | channel, note & 127, velocity & 127))])
    pending_offs.append((ticks_add(ticks_ms(), length_ms), note, channel))


def all_off():
    global drums
    tune.clear()
    drums = None
    pending_offs.clear()
    send([bytes((0xB0 | state["channel"], 123, 0)), bytes((0xB9, 123, 0))])  # CC 123: all notes off
    print("All notes off")


def degree(step):
    """Step 0, 1, 2... of the current scale, climbing octaves."""
    scale = SCALES[state["scale"]]
    octave, index = divmod(step, len(scale))
    return 12 * (state["octave"] + 1 + octave) + scale[index]


def next_note():
    state["walk"] = (state["walk"] + 1) % (len(SCALES[state["scale"]]) * 2)
    note = degree(state["walk"])
    play(note)
    print("note", note_name(note))


def chord():
    root = state["walk"]
    notes = [degree(root + k * 2) for k in range(3)]
    send([bytes((0x90 | state["channel"], note, 96)) for note in notes])
    for note in notes:
        pending_offs.append((ticks_add(ticks_ms(), 600), note, state["channel"]))
    print("chord", " ".join(note_name(note) for note in notes))


def start_tune(name):
    words = TUNES[name].split()
    beat = 60_000 // state["bpm"]
    at = ticks_ms()
    tune.clear()
    for word, beats in zip(words[::2], words[1::2]):
        length = int(float(beats) * beat)
        tune.append((at, None if word == "-" else note_number(word), length * 9 // 10))
        at = ticks_add(at, length)
    print("Playing", name)


def on_midi_in(data):
    """Notes the other side plays come in as writes. Walk the packet: skip the header and
    each timestamp, keep running status (a message may leave out its status byte)."""
    _connection, handle = data
    if handle != midi_handle:
        return
    packet = bytes(radio.ble.gatts_read(handle))
    i, status, expect_timestamp = 1, 0, True
    while i < len(packet):
        if packet[i] & 0x80:
            if expect_timestamp:
                i, expect_timestamp = i + 1, False
                continue
            status = packet[i]
            i += 1
        if not 0x80 <= status < 0xF0:
            break  # system messages (SysEx, clock) are not handled here
        size = 1 if status & 0xF0 in (0xC0, 0xD0) else 2
        message = packet[i:i + size]
        i, expect_timestamp = i + size, True
        if len(message) == 2 and status & 0xF0 == 0x90 and message[1] > 0:
            print("< note on {} velocity {}".format(note_name(message[0]), message[1]))
            radio.led.claimed = (255, 60 + message[0] % 12 * 16, 160)
        elif len(message) == 2 and status & 0xF0 in (0x80, 0x90):
            radio.led.claimed = None


def run(text):
    global drums
    command, rest = kit.split_command(text)
    lower = rest.lower()
    if command == "play" and rest:
        for word in rest.split():
            notes = [note_number(part) for part in word.split("+")] if word != "-" else []
            if None in notes:
                print("Unknown note", word, "(write C4, F#3, chords as C4+E4+G4, rests as -)")
                return
            if notes:
                send([bytes((0x90 | state["channel"], note, 100)) for note in notes])
            sleep_ms(250)
            if notes:
                send([bytes((0x80 | state["channel"], note, 0)) for note in notes])
    elif command == "tune" and lower in TUNES:
        start_tune(lower)
    elif command == "seq":
        if lower in BEATS or (lower in ("", "on") and drums is None):
            drums = {"beat": lower if lower in BEATS else "rock", "step": 0, "at": ticks_ms()}
            print("Drums:", drums["beat"], state["bpm"], "bpm")
        else:
            drums = None
            print("Drums off")
    elif command == "bpm" and rest.isdigit() and 40 <= int(rest) <= 220:
        state["bpm"] = int(rest)
    elif command == "scale" and lower in SCALES:
        state["scale"], state["walk"] = lower, 0
    elif command == "octave" and rest.isdigit() and 1 <= int(rest) <= 7:
        state["octave"] = int(rest)
    elif command == "channel" and rest.isdigit() and 1 <= int(rest) <= 16:
        state["channel"] = int(rest) - 1
    elif command == "chord":
        chord()
    elif command == "stop":
        all_off()
    else:
        print("play C4 E4 G4 | play C4+E4+G4 | tune twinkle|ode|frere | seq [rock|house|funk|off] |"
              " bpm <40-220> | scale pent|minor|major|blues | octave <1-7> | channel <1-16> | chord | stop")


def tick():
    """Note offs, the tune and the drum machine, all from the main loop."""
    global drums
    now = ticks_ms()
    for item in [item for item in pending_offs if ticks_diff(now, item[0]) >= 0]:
        pending_offs.remove(item)
        send([bytes((0x80 | item[2], item[1], 0))])
    while tune and ticks_diff(now, tune[0][0]) >= 0:
        _at, note, length = tune.pop(0)
        if note is not None:
            play(note, 100, length)
    if drums and ticks_diff(now, drums["at"]) >= 0:
        pattern = BEATS[drums["beat"]]
        hits = [bytes((0x99, DRUMS[row], 110)) for row in range(3) if pattern[row][drums["step"]] == "x"]
        if hits:
            send(hits)
        drums["step"] = (drums["step"] + 1) % 16
        drums["at"] = ticks_add(drums["at"], 15_000 // state["bpm"])  # a sixteenth note


radio.on(kit.IRQ_GATTS_WRITE, on_midi_in)
print("B7 Music on", radio.address())
# GarageBand and Audio MIDI Setup look for the MIDI service UUID in the advertisement.
radio.advertise(kit.advertise_flags() + kit.uuid_field(MIDI_SERVICE), kit.ad_field(kit.AD_NAME, b"Bench B7"))
print("Advertising as MIDI instrument Bench B7")
try:
    while True:
        text = console.line()
        if text:
            run(text)
        gesture = button.poll()
        if gesture == kit.CLICK:
            next_note()
        elif gesture == kit.DOUBLE:
            chord()
        elif gesture == kit.LONG:
            all_off()
        tick()
        radio.tick()
        sleep_ms(5)
finally:
    all_off()
    radio.stop_advertising()
    radio.ble.active(False)
    radio.led.off()
