"""ES8311 codec on Pulsar C5: play short melodies through the speaker.

A friendlier output test than a constant tone. Each song has two voices: a
melody with a soft piano-like attack and a quiet bass line. Notes ring into
the next one instead of stopping abruptly. Plays a greeting arpeggio,
"Twinkle, Twinkle, Little Star" and the "Ode to Joy" theme (both public
domain), then stops.
Save libraries/es8311.py on the board as es8311.py first.

Wire with USB unplugged (ES8311 -> Pulsar C5), as in the Muse ESP32-C5
ES8311 board (voice_board_es8311_c5.c):
  J20: SCLK/BCLK -> GPIO27, LRCK/WS -> GPIO7, SDIN -> GPIO26, MCLK -> GPIO10
  SDA -> QWIIC GPIO1, SCL -> QWIIC GPIO6   (I2C control, address 0x18)
  3V3 -> 3V3, GND -> GND
"""

import array
import math
import time

import es8311

SDA_PIN = 1         # QWIIC SDA
SCL_PIN = 6         # QWIIC SCL
BCLK_PIN = 27       # J20: ES8311 SCLK/BCLK
WS_PIN = 7          # J20: ES8311 LRCK/WS
DIN_PIN = 26        # J20: Pulsar C5 output -> ES8311 SDIN (speaker)
MCLK_PIN = 10       # J20: ES8311 MCLK (256 x fs); keep it, the codec sounds best with it

SAMPLE_RATE = 22050
VOLUME = 0.5          # 0.0 .. 1.0 codec output volume
MELODY_LOUDNESS = 9000  # peak sample value of the melody (max 32767)
BASS_LOUDNESS = 5000    # the bass stays under the melody
BEAT_MS = 330         # length of one beat; lower plays faster

# Each song: title, melody and bass as (note, beats). "R" is a rest.
# Octave 4 contains middle C (C4 = 261.6 Hz). Both lists must add up to the
# same number of beats so the voices stay together.
GREETING = (
    "Greeting",
    [("C5", 0.5), ("E5", 0.5), ("G5", 0.5), ("C6", 2.5)],
    [("C3", 4)],
)
TWINKLE = (
    "Twinkle, Twinkle",
    [("C4", 1), ("C4", 1), ("G4", 1), ("G4", 1), ("A4", 1), ("A4", 1), ("G4", 2),
     ("F4", 1), ("F4", 1), ("E4", 1), ("E4", 1), ("D4", 1), ("D4", 1), ("C4", 2)],
    [("C3", 2), ("G2", 2), ("F2", 2), ("C3", 2),
     ("F2", 2), ("C3", 2), ("G2", 2), ("C3", 2)],
)
ODE_TO_JOY = (
    "Ode to Joy",
    [("E4", 1), ("E4", 1), ("F4", 1), ("G4", 1), ("G4", 1), ("F4", 1), ("E4", 1), ("D4", 1),
     ("C4", 1), ("C4", 1), ("D4", 1), ("E4", 1), ("E4", 1.5), ("D4", 0.5), ("D4", 2),
     ("E4", 1), ("E4", 1), ("F4", 1), ("G4", 1), ("G4", 1), ("F4", 1), ("E4", 1), ("D4", 1),
     ("C4", 1), ("C4", 1), ("D4", 1), ("E4", 1), ("D4", 1.5), ("C4", 0.5), ("C4", 2)],
    [("C3", 2), ("G2", 2), ("G2", 2), ("D3", 2), ("C3", 2), ("G2", 2), ("C3", 2), ("G2", 2),
     ("C3", 2), ("G2", 2), ("G2", 2), ("D3", 2), ("C3", 2), ("G2", 2), ("G2", 2), ("C3", 2)],
)
SONGS = [GREETING, TWINKLE, ODE_TO_JOY]

# Envelope times in seconds: how long until the level halves.
MELODY_HALF_LIFE = 0.45   # main tone while the note is held
BASS_HALF_LIFE = 0.9
BRIGHT_HALF_LIFE = 0.06   # the bright harmonics fade quickly, like a piano
RELEASE_S = 0.25          # ring-out after the note ends, overlapping the next

_NOTE_INDEX = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_TABLE_SIZE = 1024


def note_frequency(name):
    semitone = _NOTE_INDEX[name[0]]
    rest = name[1:]
    if rest.startswith("#"):
        semitone += 1
        rest = rest[1:]
    midi = 12 * (int(rest) + 1) + semitone
    return 440.0 * 2 ** ((midi - 69) / 12)


def make_table(harmonics):
    table = array.array("h", bytes(_TABLE_SIZE * 2))
    for i in range(_TABLE_SIZE):
        x = 2 * math.pi * i / _TABLE_SIZE
        table[i] = int(32767 * sum(a * math.sin(k * x) for k, a in harmonics))
    return table


def half_life(seconds):
    """Per-sample Q15 multiplier that halves a level after `seconds`."""
    return int(32768 * 0.5 ** (1.0 / (seconds * SAMPLE_RATE)))


_ADD_SRC = """
@micropython.viper
def add_note(buf: ptr16, start: int, total: int, hold: int, length: int,
             pure: ptr16, bright: ptr16, step: int, attack: int, decay: int,
             bdecay: int, rdecay: int, peak: int):
    phase = 0
    env = 32767
    benv = 32767
    for i in range(length):
        n = start + i
        if n >= total:
            break
        p = (phase >> 16) & 1023
        phase += step
        a = int(pure[p])
        if a & 0x8000:
            a -= 0x10000
        b = int(bright[p])
        if b & 0x8000:
            b -= 0x10000
        s = (a * env >> 15) + ((b * benv >> 15) * env >> 15)
        if i < attack:
            s = s * i // attack
        s = s * peak >> 15
        o = int(buf[2 * n])
        if o & 0x8000:
            o -= 0x10000
        o += s
        if o > 32767:
            o = 32767
        elif o < -32768:
            o = -32768
        buf[2 * n] = o
        buf[2 * n + 1] = o
        if i < hold:
            env = env * decay >> 15
        else:
            env = env * rdecay >> 15
        benv = benv * bdecay >> 15
"""


def _add_note_py(buf, start, total, hold, length, pure, bright, step, attack,
                 decay, bdecay, rdecay, peak):
    phase = 0
    env = 32767
    benv = 32767
    for i in range(length):
        n = start + i
        if n >= total:
            break
        p = (phase >> 16) & (_TABLE_SIZE - 1)
        phase += step
        s = (pure[p] * env >> 15) + ((bright[p] * benv >> 15) * env >> 15)
        if i < attack:
            s = s * i // attack
        s = s * peak >> 15
        o = max(-32768, min(32767, buf[2 * n] + s))
        buf[2 * n] = o
        buf[2 * n + 1] = o
        env = env * (decay if i < hold else rdecay) >> 15
        benv = benv * bdecay >> 15


try:
    _ns = {}
    exec(_ADD_SRC, _ns)
    _add_note = _ns["add_note"]
except Exception:  # firmware without the native emitter
    _add_note = _add_note_py


class Voice:
    def __init__(self, pure, bright, half_life_s, loudness):
        self.pure = pure
        self.bright = bright
        self.decay = half_life(half_life_s)
        self.loudness = loudness


def render_song(melody, bass, voices):
    beat_frames = SAMPLE_RATE * BEAT_MS // 1000
    release = int(RELEASE_S * SAMPLE_RATE)
    beats = sum(b for _, b in melody)
    total = int(beats * beat_frames) + release
    buf = array.array("h", bytes(total * 4))
    attack = SAMPLE_RATE * 5 // 1000            # 5 ms: soft but clear onset
    bdecay = half_life(BRIGHT_HALF_LIFE)
    rdecay = int(32768 * 0.01 ** (1.0 / release))  # fade to 1 % over the release
    for notes, voice in zip((melody, bass), voices):
        position = 0.0
        for name, length in notes:
            if name != "R":
                start = int(position * beat_frames)
                hold = int(length * beat_frames)
                step = int(note_frequency(name) * _TABLE_SIZE * 65536 / SAMPLE_RATE)
                _add_note(buf, start, total, hold, hold + release, voice.pure,
                          voice.bright, step, attack, voice.decay, bdecay,
                          rdecay, voice.loudness)
            position += length
    return buf


def main():
    pure = make_table(((1, 1.0),))
    bright = make_table(((2, 0.35), (3, 0.2), (4, 0.1)))
    soft = make_table(((2, 0.15),))
    voices = (Voice(pure, bright, MELODY_HALF_LIFE, MELODY_LOUDNESS),
              Voice(pure, soft, BASS_HALF_LIFE, BASS_LOUDNESS))

    audio = es8311.ES8311Audio(sda=SDA_PIN, scl=SCL_PIN, mclk=MCLK_PIN,
                               bclk=BCLK_PIN, ws=WS_PIN, data_out=DIN_PIN)
    print(es8311.scan_report(audio.codec.i2c))
    audio.begin_tx(rate=SAMPLE_RATE, volume=VOLUME, ibuf=16384)
    silence = array.array("h", bytes(SAMPLE_RATE // 4 * 4))
    try:
        for title, melody, bass in SONGS:
            print("Preparing:", title)
            song = render_song(melody, bass, voices)
            print("Playing:", title)
            audio.write(song)
            song = None
            audio.write(silence)
        time.sleep_ms(300)
    finally:
        audio.end()
    print("Done.")


main()
