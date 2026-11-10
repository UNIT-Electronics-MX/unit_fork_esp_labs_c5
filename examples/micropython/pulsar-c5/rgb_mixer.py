"""P2: mix colours on the Pulsar C5's on-board WS2812 from the USB REPL."""

from machine import Pin
from neopixel import NeoPixel


# The schematic uses GPIO0; some UNIT illustrations label the LED GPIO8.
RGB_PIN = 0
LEVEL = 40
pixel = NeoPixel(Pin(RGB_PIN, Pin.OUT), 1)
colour = (0, 0, 0)


def show():
    pixel[0] = tuple(channel * LEVEL // 255 for channel in colour)
    pixel.write()


print("P2 RGB mixer ready: rgb R G B, level 0-255, or off")
show()
try:
    while True:
        parts = input("rgb> ").strip().lower().split()
        if len(parts) == 4 and parts[0] == "rgb":
            try:
                values = tuple(int(part) for part in parts[1:])
            except ValueError:
                values = ()
            if len(values) == 3 and all(0 <= value <= 255 for value in values):
                colour = values
                show()
                print("RGB {} {} {} at level {}".format(*colour, LEVEL))
                continue
        elif len(parts) == 2 and parts[0] == "level":
            try:
                value = int(parts[1])
            except ValueError:
                value = -1
            if 0 <= value <= 255:
                LEVEL = value
                show()
                print("Level {}".format(LEVEL))
                continue
        elif parts == ["off"]:
            colour = (0, 0, 0)
            show()
            print("RGB off")
            continue
        print("Use: rgb R G B, level 0-255, or off")
finally:
    colour = (0, 0, 0)
    show()
