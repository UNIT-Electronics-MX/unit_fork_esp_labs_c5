"""Read the ICS-43434 microphone plugged into the Pulsar C5 J20 connector.

J20 pin 1..6 to microphone J1 pin 1..6:
SCK=GPIO27, SD=GPIO7, WS=GPIO26, CH=GPIO10, 3V3, GND.
"""

from machine import I2S, Pin
import struct
import time


# Select the microphone's left slot (CH has a pull-down on the module).
channel = Pin(10, Pin.OUT, value=0)

mic = I2S(
    0,
    sck=Pin(27),
    ws=Pin(26),
    sd=Pin(7),
    mode=I2S.RX,
    bits=32,
    format=I2S.MONO,
    rate=32000,
    ibuf=16384,
)

buffer = bytearray(4096)

try:
    while True:
        count = mic.readinto(buffer)
        peak = 0
        for offset in range(0, count - 3, 4):
            sample = struct.unpack_from("<i", buffer, offset)[0]
            # The 24-bit microphone sample is left-aligned in a 32-bit word.
            peak = max(peak, abs(sample) >> 8)
        print("Audio peak (24-bit):", peak)
        time.sleep_ms(50)
finally:
    mic.deinit()
