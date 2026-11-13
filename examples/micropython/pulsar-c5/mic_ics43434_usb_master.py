"""Stream the J20 ICS-43434 microphone to pc_audio_pulsar.py.

Run as main.py on the Pulsar C5. The ESP32-C5 is the I2S clock master:
J20 pin 1/SCK=GPIO27, pin 2/SD=GPIO7, pin 3/WS=GPIO26,
pin 4/CH=GPIO10. USB Serial/JTAG carries 48 kHz, 16-bit mono PCM.

Protocol: wait for 'R', send 'RAW\r\n', then repeat
'AU' + uint16 sample count + PCM samples + uint16 sum of samples.
'S' stops the stream; another 'R' starts it again.
"""

import array
import select
import struct
import sys

from machine import I2S, Pin


SAMPLE_RATE = 48000
FRAME_SAMPLES = 512


def main():
    # CH low selects the left I2S slot. No MCLK is needed by the ICS-43434.
    channel = Pin(10, Pin.OUT, value=0)
    samples = array.array("h", [0] * FRAME_SAMPLES)
    frame_header = b"AU" + struct.pack("<H", FRAME_SAMPLES)
    serial_in = sys.stdin.buffer
    serial_out = sys.stdout.buffer
    commands = select.poll()
    commands.register(serial_in, select.POLLIN)

    mic = I2S(
        0,
        sck=Pin(27),
        ws=Pin(26),
        sd=Pin(7),
        mode=I2S.RX,
        bits=16,
        format=I2S.MONO,
        rate=SAMPLE_RATE,
        ibuf=32768,
    )

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

                count = mic.readinto(samples)
                if count != FRAME_SAMPLES * 2:
                    continue

                serial_out.write(frame_header)
                serial_out.write(samples)
                serial_out.write(struct.pack("<H", sum(samples) & 0xFFFF))
    finally:
        mic.deinit()


main()
