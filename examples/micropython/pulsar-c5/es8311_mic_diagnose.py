"""ES8311 codec on Pulsar C5: find out why the microphone is silent.

Save libraries/es8311.py on the board as es8311.py, run this script and make
noise (clap, talk loudly) near the codec microphone during the sweep.
It prints:
  - the chip ID (83 11 means a genuine ES8311) and the key registers;
  - for each microphone gain, the raw left and right I2S slots: peak and mean.
Send the whole output when asking for help.
"""

import array
import time

import es8311
import machine

SDA_PIN = 1         # QWIIC SDA
SCL_PIN = 6         # QWIIC SCL
BCLK_PIN = 27       # J20 SCK
WS_PIN = 26        # J20 WS
DOUT_PIN = 7       # J20 SD: ES8311 DOUT -> Pulsar C5 I2S input
MCLK_PIN = -1       # no MCLK wire: the codec clocks from BCLK

SAMPLE_RATE = 16000
SECONDS_PER_GAIN = 2
REGISTERS = (0x01, 0x02, 0x03, 0x09, 0x0A, 0x0D, 0x0E, 0x12, 0x14, 0x15,
             0x16, 0x17, 0x1B, 0x1C, 0xFD, 0xFE, 0xFF)


def slot_stats(stereo, frames):
    peaks = [0, 0]
    sums = [0, 0]
    for i in range(frames):
        for slot in (0, 1):
            value = stereo[i * 2 + slot]
            if abs(value) > peaks[slot]:
                peaks[slot] = abs(value)
            sums[slot] += value
    return peaks, [sums[0] // max(1, frames), sums[1] // max(1, frames)]


def main():
    audio = es8311.ES8311Audio(sda=SDA_PIN, scl=SCL_PIN, mclk=MCLK_PIN,
                               bclk=BCLK_PIN, ws=WS_PIN, data_in=DOUT_PIN)
    print(es8311.scan_report(audio.codec.i2c))
    audio.begin_rx(rate=SAMPLE_RATE, gain=0.5)
    print("Chip ID:", "%02X %02X %02X" % audio.codec.chip_id())
    print("Registers:", audio.codec.dump(REGISTERS))
    print("Make noise near the microphone now.")

    stereo = array.array("h", bytes(audio.BLOCK_FRAMES * 4))
    view = memoryview(stereo)
    try:
        for step in range(8):
            audio.codec.set_mic_gain(step)
            time.sleep_ms(100)
            best = [0, 0]
            mean = [0, 0]
            end = time.ticks_add(time.ticks_ms(), SECONDS_PER_GAIN * 1000)
            while time.ticks_diff(end, time.ticks_ms()) > 0:
                frames = audio.i2s.readinto(view) // 4
                peaks, means = slot_stats(stereo, frames)
                for slot in (0, 1):
                    best[slot] = max(best[slot], peaks[slot])
                mean = means
            print("gain step %d (%2d dB): left peak %5d mean %6d | right peak %5d mean %6d"
                  % (step, step * 6, best[0], mean[0], best[1], mean[1]))
        print("Reg 14 (input select/PGA): %02X, reg 16 (ADC gain): %02X, reg 17 (ADC volume): %02X"
              % (audio.codec.read_reg(0x14), audio.codec.read_reg(0x16), audio.codec.read_reg(0x17)))
    finally:
        audio.end()
    print("Done.")


main()
