"""ICS-43434 on Pulsar C5: I2S level over the browser serial console.

Wire with USB unplugged: VSYS -> 3V3, GND -> GND, CH -> GND,
SCK -> A2/GPIO4, WS -> A3/GPIO5, SD -> D4/GPIO9.
Use the released module pinout to confirm the power rail on your board revision.
The 6-pin JST-SH connector is NOT the C5's 4-pin QWIIC/I2C connector.
"""

import math
import struct
import machine


if not hasattr(machine, "I2S"):
    raise RuntimeError(
        "This ESP32-C5 MicroPython firmware has no machine.I2S. "
        "Flash a C5 build with I2S enabled; the stock ESP32_GENERIC_C5 v1.29.0 disables it."
    )

from machine import Pin


RATE = 16_000
SCK_PIN = 4
WS_PIN = 5
SD_PIN = 9
BLOCK_BYTES = 1024  # 256 signed 32-bit samples from the left slot.

mic = machine.I2S(
    0,
    sck=Pin(SCK_PIN),
    ws=Pin(WS_PIN),
    sd=Pin(SD_PIN),
    mode=machine.I2S.RX,
    bits=32,
    format=machine.I2S.MONO,
    rate=RATE,
    ibuf=8192,
)
block = bytearray(BLOCK_BYTES)
samples = 0
sum_squares = 0
peak = 0

print("ICS-43434 ready: 16 kHz, left channel, 32-bit I2S slots")
print("MIC,dBFS,peak16")
try:
    while True:
        received = mic.readinto(block)
        if not received:
            continue
        # The microphone places a signed 24-bit sample in a 32-bit slot.
        # Shift to signed 16-bit scale before calculating the level.
        for offset in range(0, received - received % 4, 4):
            value = struct.unpack_from("<i", block, offset)[0] >> 16
            magnitude = abs(value)
            if magnitude > peak:
                peak = magnitude
            sum_squares += value * value
            samples += 1
        if samples >= RATE // 10:
            rms = math.sqrt(sum_squares / samples)
            dbfs = 20 * math.log10(rms / 32768) if rms else -120
            print("MIC,{:.1f},{}".format(dbfs, peak))
            samples = 0
            sum_squares = 0
            peak = 0
finally:
    mic.deinit()
