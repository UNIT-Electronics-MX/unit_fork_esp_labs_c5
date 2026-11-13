"""Stream the ES8311 codec microphone to the browser or pc_audio_pulsar.py.

Run on the Pulsar C5 after saving libraries/es8311.py as es8311.py. The
ESP32-C5 is the I2S clock master; the codec clocks from MCLK (GPIO10) or BCLK.

Wire with USB unplugged (ES8311 -> Pulsar C5):
  BCLK/SCLK -> J20 SCK/GPIO27, LRCK/WS -> J20 WS/GPIO26,
  DOUT -> J20 SD/GPIO7, DIN -> J20 CH/GPIO10, 3V3 -> J20 3V3, GND -> J20 GND
  SDA -> QWIIC GPIO1, SCL -> QWIIC GPIO6   (I2C control, address 0x18)

Same protocol as mic_ics43434_usb_master.py, so the same listening dialog
works: wait for 'R', send 'RAW\r\n', then repeat
'AU' + uint16 sample count + PCM samples + uint16 sum of samples.
'S' stops the stream; another 'R' starts it again.
USB Serial/JTAG carries 48 kHz, 16-bit mono PCM.
"""

import array
import select
import struct
import sys

import es8311

SDA_PIN = 1         # QWIIC SDA
SCL_PIN = 6         # QWIIC SCL
BCLK_PIN = 27       # J20 SCK
WS_PIN = 26        # J20 WS
DOUT_PIN = 7       # J20 SD: ES8311 DOUT -> Pulsar C5 I2S input
MCLK_PIN = -1       # no MCLK wire: the codec clocks from BCLK

SAMPLE_RATE = 48000  # must match the browser player and pc_audio_pulsar.py
INPUT_GAIN = 0.6    # 0.0 .. 1.0 -> 0 .. 42 dB; 0.6 = 24 dB
FRAME_SAMPLES = 512


def main():
    audio = es8311.ES8311Audio(sda=SDA_PIN, scl=SCL_PIN, mclk=MCLK_PIN,
                               bclk=BCLK_PIN, ws=WS_PIN, data_in=DOUT_PIN)
    try:
        audio.begin_rx(rate=SAMPLE_RATE, gain=INPUT_GAIN, ibuf=32768)
    except OSError as error:
        print("ES8311 microphone startup failed:", error)
        print(es8311.scan_report(audio.codec.i2c))
        return

    samples = array.array("h", bytes(FRAME_SAMPLES * 2))
    view = memoryview(samples)
    frame_header = b"AU" + struct.pack("<H", FRAME_SAMPLES)
    serial_in = sys.stdin.buffer
    serial_out = sys.stdout.buffer
    commands = select.poll()
    commands.register(serial_in, select.POLLIN)

    try:
        while True:
            if serial_in.read(1) != b"R":
                continue
            serial_out.write(b"RAW\r\n")

            while True:
                if commands.poll(0):
                    command = serial_in.read(1)
                    if command == b"S":
                        break
                    if command == b"R":
                        serial_out.write(b"RAW\r\n")

                filled = 0
                while filled < FRAME_SAMPLES:  # read_mono returns <= 256 samples
                    filled += audio.read_mono(view[filled:])

                serial_out.write(frame_header)
                serial_out.write(samples)
                serial_out.write(struct.pack("<H", sum(samples) & 0xFFFF))
    finally:
        audio.end()


main()
