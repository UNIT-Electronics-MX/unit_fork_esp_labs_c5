"""Pulsar C5: rueda de colores en el WS2812 integrado."""

from machine import Pin
from neopixel import NeoPixel
from time import sleep_ms


# El esquema indica GPIO0; algunos graficos de UNIT indican GPIO8.
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
