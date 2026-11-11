"""Pulsar C5: draw text and shapes on an 80x160 ST7735 TFT over hardware SPI."""

from machine import Pin, SPI
from time import sleep_ms

import st7735


# The example accepts both the direct-drawing driver in libraries/ and the
# small framebuffer driver used by earlier web builds.
# The TFT's SDA/SCL labels are SPI MOSI/SCK, not I2C. It shares the SPI bus
# with the onboard microSD, so the SD card is kept deselected on GPIO23.
SPI_ID = 1
MOSI_PIN = 7    # D11, SDA on the TFT
SCK_PIN = 27    # D13, SCL on the TFT
SD_CS_PIN = 23  # Onboard microSD chip select, kept high
CS_PIN = 9      # D4; leaves QWIIC SDA on GPIO1 free
DC_PIN = 4      # A2
RES_PIN = 5     # A3
BLK_PIN = -1    # -1: no GPIO control of the backlight

Pin(SD_CS_PIN, Pin.OUT, value=1)

if BLK_PIN >= 0:
    Pin(BLK_PIN, Pin.OUT, value=1)

spi = SPI(
    SPI_ID,
    baudrate=1000000,
    polarity=0,
    phase=0,
    sck=Pin(SCK_PIN),
    mosi=Pin(MOSI_PIN),
)

# Green-tab 0.96-inch module: column offset 26, row offset 1, BGR, inverted.
print("ST7735: initializing display")
if hasattr(st7735, "TFTColor"):
    # Guy Carver / boochow API: direct drawing, no framebuffer.
    tft = st7735.TFT(spi, DC_PIN, RES_PIN, CS_PIN,
                     width=80, height=160, xoffset=26, yoffset=1)
    tft.initg()
    tft.rgb(False)
    tft.invertcolor(True)

    for colour in (tft.RED, tft.GREEN, tft.BLUE):
        tft.fill(colour)
        sleep_ms(650)

    tft.fill(tft.BLACK)
    tft.text((8, 8), "UNIT", tft.WHITE)
    tft.text((8, 20), "ST7735", tft.CYAN)
    tft.line((4, 40), (75, 40), tft.YELLOW)
    tft.rect((4, 50), (30, 32), tft.RED)
    tft.fillrect((46, 50), (30, 32), tft.GREEN)
    tft.pixel((40, 116), tft.WHITE)
elif hasattr(st7735, "rgb565"):
    # Earlier web API: 80x160 framebuffer, flushed with show().
    tft = st7735.TFT(spi, DC_PIN, RES_PIN, CS_PIN,
                     xoffset=26, yoffset=1)
    tft.init()
    frame = tft.frame
    rgb565 = st7735.rgb565
    red = rgb565(255, 0, 0)
    green = rgb565(0, 255, 0)
    blue = rgb565(0, 0, 255)
    white = rgb565(255, 255, 255)
    black = rgb565(0, 0, 0)

    for colour in (red, green, blue):
        frame.fill(colour)
        tft.show()
        sleep_ms(650)

    frame.fill(black)
    frame.text("UNIT", 8, 8, white)
    frame.text("ST7735", 8, 20, green)
    frame.line(4, 40, 75, 40, blue)
    frame.rect(4, 50, 30, 32, red)
    frame.fill_rect(46, 50, 30, 32, green)
    frame.pixel(40, 116, white)
    tft.show()
else:
    raise RuntimeError("Unsupported st7735.py; install the driver from this lesson")
print("ST7735: drawing done")
