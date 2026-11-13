"""ES8311 codec on Pulsar C5: capture the microphone and print its level.

MicroPython version of the Arduino example es8311_mic_capture.ino from
unit_devlab_codec_library. Save libraries/es8311.py on the board as es8311.py.

Wire with USB unplugged (ES8311 -> Pulsar C5):
  BCLK/SCLK -> J20 SCK/GPIO27, LRCK/WS -> J20 WS/GPIO26,
  DOUT -> J20 SD/GPIO7, DIN -> J20 CH/GPIO10, 3V3 -> J20 3V3, GND -> J20 GND
  SDA -> QWIIC GPIO1, SCL -> QWIIC GPIO6   (I2C control, address 0x18)
"""

import array
import time

import es8311

SDA_PIN = 1         # QWIIC SDA
SCL_PIN = 6         # QWIIC SCL
BCLK_PIN = 27       # J20 SCK
WS_PIN = 26        # J20 WS
DOUT_PIN = 7       # J20 SD: ES8311 DOUT -> Pulsar C5 I2S input
MCLK_PIN = -1       # no MCLK wire: the codec clocks from BCLK

SAMPLE_RATE = 16000
INPUT_GAIN = 0.6    # 0.0 .. 1.0 -> 0 .. 42 dB; 0.6 = 24 dB
SAMPLES_PER_BLOCK = 256


def main():
    audio = es8311.ES8311Audio(sda=SDA_PIN, scl=SCL_PIN, mclk=MCLK_PIN,
                               bclk=BCLK_PIN, ws=WS_PIN, data_in=DOUT_PIN)
    try:
        audio.begin_rx(rate=SAMPLE_RATE, gain=INPUT_GAIN)
    except OSError as error:
        print("ES8311 microphone startup failed:", error)
        print(es8311.scan_report(audio.codec.i2c))
        return
    print("ES8311 microphone ready: %d Hz, 16-bit mono PCM, GPIO%d data input"
          % (SAMPLE_RATE, DOUT_PIN))

    pcm = array.array("h", bytes(SAMPLES_PER_BLOCK * 2))
    captured = 0
    peak = 0
    last = time.ticks_ms()
    try:
        while True:
            frames = audio.read_mono(pcm)
            captured += frames
            for i in range(frames):
                level = abs(pcm[i])
                if level > peak:
                    peak = level
            # pcm[0:frames] is little-endian PCM16 for a network, file or DSP.
            now = time.ticks_ms()
            if time.ticks_diff(now, last) >= 1000:
                print("Captured %d frames/s, peak %d" % (captured, peak))
                captured = 0
                peak = 0
                last = now
    finally:
        audio.end()


main()
