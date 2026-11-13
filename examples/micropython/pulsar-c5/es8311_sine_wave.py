"""ES8311 codec on Pulsar C5: play a test tone.

MicroPython version of the Arduino example es8311_sine_wave.ino from
unit_devlab_codec_library. Save libraries/es8311.py on the board as es8311.py.

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

SAMPLE_RATE = 44100
FREQUENCY = 493.88  # B4, like the Arduino sketch
AMPLITUDE = 14000
VOLUME = 0.7        # 0.0 .. 1.0
BUFFER_FRAMES = 4410  # 0.1 s; the tone is rounded to whole cycles


def make_tone(rate, frequency, amplitude, frames):
    cycles = max(1, round(frequency * frames / rate))
    samples = array.array("h", bytes(frames * 4))
    step = 2 * math.pi * cycles / frames
    for i in range(frames):
        value = int(amplitude * math.sin(step * i))
        samples[2 * i] = value       # left
        samples[2 * i + 1] = value   # right
    return samples, cycles * rate / frames


def main():
    print("Starting ES8311 test...")
    audio = es8311.ES8311Audio(sda=SDA_PIN, scl=SCL_PIN, mclk=MCLK_PIN,
                               bclk=BCLK_PIN, ws=WS_PIN, data_out=DIN_PIN)
    print(es8311.scan_report(audio.codec.i2c))

    audio.begin_tx(rate=SAMPLE_RATE, volume=VOLUME)
    print("Codec and I2S startup: OK (clock from %s)"
          % ("MCLK pin" if audio.uses_mclk_pin else "BCLK"))
    print("ES8311 registers:", audio.codec.dump())

    tone, actual = make_tone(SAMPLE_RATE, FREQUENCY, AMPLITUDE, BUFFER_FRAMES)
    print("Sine wave generator: OK, %.1f Hz" % actual)

    sent = 0
    last = time.ticks_ms()
    try:
        while True:
            sent += audio.write(tone)
            now = time.ticks_ms()
            if time.ticks_diff(now, last) >= 1000:
                print("Data sent to I2S: %d bytes/s" % sent)
                sent = 0
                last = now
    finally:
        audio.end()


main()
