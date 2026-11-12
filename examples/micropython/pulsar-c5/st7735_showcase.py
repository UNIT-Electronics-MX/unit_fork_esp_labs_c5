"""Pulsar C5 ST7735 showcase: four scenes, switched with BOOT."""

from machine import Pin, SPI
from framebuf import FrameBuffer, RGB565
from time import ticks_ms, ticks_diff, sleep_ms
import st7735

MOSI_PIN = 7       # D11 / MOSI
SCK_PIN = 27       # D13 / SCK
SD_CS_PIN = 23     # Keep the microSD deselected
CS_PIN = 9         # D4; leaves QWIIC SDA on GPIO1 free
DC_PIN = 4         # A2
RES_PIN = 5        # A3
BOOT_PIN = 28      # D3 / BOOT

Pin(SD_CS_PIN, Pin.OUT, value=1)
boot = Pin(BOOT_PIN, Pin.IN, Pin.PULL_UP)
spi = SPI(1, baudrate=4_000_000, polarity=0, phase=0,
          sck=Pin(SCK_PIN), mosi=Pin(MOSI_PIN))
WIDTH = 80
HEIGHT = 160
FRAME_MS = 125

if hasattr(st7735, "TFTColor"):
    # Compose off-screen even with the direct-drawing driver. A full-screen
    # clear on the TFT followed by hundreds of SPI writes caused the flicker.
    tft = st7735.TFT(spi, DC_PIN, RES_PIN, CS_PIN,
                     width=WIDTH, height=HEIGHT, xoffset=26, yoffset=1)
    tft.initg()
    tft.rgb(False)
    tft.invertcolor(True)
    pixels = bytearray(WIDTH * HEIGHT * 2)
    frame = FrameBuffer(pixels, WIDTH, HEIGHT, RGB565)

    # ST7735.image() sends RGB565 high byte first. FrameBuffer's byte order
    # depends on the MicroPython port, so detect it instead of assuming it.
    probe = bytearray(2)
    FrameBuffer(probe, 1, 1, RGB565).pixel(0, 0, 0xF800)
    swap_bytes = probe[0] != 0xF8

    def colour(red, green, blue):
        value = st7735.TFTColor(red, green, blue)
        return ((value & 0xFF) << 8) | (value >> 8) if swap_bytes else value

    def show_frame():
        tft.image(0, 0, WIDTH - 1, HEIGHT - 1, pixels)

elif hasattr(st7735, "rgb565"):
    # Earlier web driver already owns a framebuffer and a full-frame flush.
    tft = st7735.TFT(spi, DC_PIN, RES_PIN, CS_PIN,
                     xoffset=26, yoffset=1)
    tft.init()
    frame = tft.frame
    colour = st7735.rgb565

    def show_frame():
        tft.show()

else:
    raise RuntimeError("Unsupported st7735.py; install the driver from this lesson")


def fill(value):
    frame.fill(value)


def text(x, y, message, value):
    frame.text(message, x, y, value)


def hline(x, y, width, value):
    frame.hline(x, y, width, value)


def rect(x, y, width, height, value):
    frame.rect(x, y, width, height, value)


def fillrect(x, y, width, height, value):
    frame.fill_rect(x, y, width, height, value)


def pixel(x, y, value):
    frame.pixel(x, y, value)


BLACK = colour(0, 0, 0)
WHITE = colour(245, 245, 245)
ORANGE = colour(255, 130, 35)
CYAN = colour(35, 210, 240)
PINK = colour(255, 70, 150)
GREEN = colour(60, 230, 110)
COLOURS = (ORANGE, CYAN, PINK, GREEN)
NAMES = ("BARS", "ORBIT", "WAVES", "SYSTEM")


def draw(scene, tick):
    fill(BLACK)
    text(2, 3, "C5 DEMO", WHITE)
    hline(2, 16, 76, COLOURS[scene])
    text(2, 22, NAMES[scene], COLOURS[scene])

    if scene == 0:
        for index in range(5):
            height = 12 + ((tick // 2 + index * 13) % 70)
            fillrect(7 + index * 15, 125 - height, 10, height,
                     COLOURS[index % 4])
    elif scene == 1:
        # Moving square orbit with a trail of smaller squares.
        for index in range(8):
            phase = (tick + index * 5) % 60
            x = 4 + (phase if phase < 30 else 60 - phase) * 2
            y = 48 + index * 11
            rect(x, y, 9, 9, COLOURS[index % 4])
    elif scene == 2:
        for x in range(3, 78):
            y = 82 + ((x + tick * 3) % 28) - 14
            pixel(x, y, CYAN)
            pixel(x, 130 - (y - 82), PINK)
    else:
        used = (tick * 7) % 72
        text(2, 49, "MicroPy", WHITE)
        text(2, 68, "BOOT: next", CYAN)
        text(2, 87, "SPI 4 MHz", GREEN)
        rect(3, 111, 74, 12, WHITE)
        if used:
            fillrect(4, 112, used, 10, ORANGE)

    text(18, 144, "BOOT >", WHITE)
    show_frame()


scene = 0
tick = 0
last_press = 0
last_draw = ticks_ms()
previous = boot.value()
print("ST7735 showcase ready; press BOOT for the next scene")

while True:
    now = ticks_ms()
    pressed = boot.value()
    if previous and not pressed and ticks_diff(now, last_press) > 250:
        scene = (scene + 1) % len(NAMES)
        last_press = now
        print("scene:", NAMES[scene])
    previous = pressed
    if ticks_diff(now, last_draw) >= FRAME_MS:
        draw(scene, tick)
        tick += 1
        last_draw = now
    sleep_ms(10)
