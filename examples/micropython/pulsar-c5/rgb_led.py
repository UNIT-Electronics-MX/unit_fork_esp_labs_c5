"""Pulsar C5: cycle colors on the onboard WS2812."""

from machine import Pin
from neopixel import NeoPixel
from time import sleep_ms


# The schematic shows GPIO0; some UNIT illustrations show GPIO8.
RGB_PIN = 0
BRIGHTNESS = 40
pixel = NeoPixel(Pin(RGB_PIN, Pin.OUT), 1)


def wheel(position):
    if position < 85:
        return (255 - position * 3, position * 3, 0)
    if position < 170:
        position -= 85
        return (0, 255 - position * 3, position * 3)
    position -= 170
    return (position * 3, 0, 255 - position * 3)


try:
    while True:
        for hue in range(256):
            color = wheel(hue)
            pixel[0] = tuple(channel * BRIGHTNESS // 255 for channel in color)
            pixel.write()
            sleep_ms(20)
finally:
    pixel[0] = (0, 0, 0)
    pixel.write()
