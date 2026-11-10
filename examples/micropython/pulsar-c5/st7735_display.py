"""Pulsar C5: draw text and shapes on an 80x160 ST7735 TFT over hardware SPI."""

from machine import Pin, SPI
from time import sleep_ms

from st7735 import TFT


# Install libraries/st7735.py as st7735.py on the board first.
# The TFT's SDA/SCL labels are SPI MOSI/SCK, not I2C. It shares the SPI bus
# with the onboard microSD, so the SD card is kept deselected on GPIO23.
SPI_ID = 1
MOSI_PIN = 7    # D11, SDA on the TFT
SCK_PIN = 27    # D13, SCL on the TFT
SD_CS_PIN = 23  # Onboard microSD chip select, kept high
CS_PIN = 1      # A1
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
tft = TFT(spi, DC_PIN, RES_PIN, CS_PIN,
          width=80, height=160, xoffset=26, yoffset=1)
print("ST7735: initializing display")
tft.initg()
tft.rgb(False)
tft.invertcolor(True)

print("ST7735: red test screen")
tft.fill(TFT.RED)
sleep_ms(3000)

tft.fill(TFT.BLACK)
tft.text((8, 8), "UNIT", TFT.WHITE)
tft.text((8, 20), "ST7735", TFT.CYAN)
tft.line((4, 40), (75, 40), TFT.YELLOW)
tft.rect((4, 50), (30, 32), TFT.RED)
tft.fillrect((46, 50), (30, 32), TFT.GREEN)
tft.circle((20, 116), 14, TFT.BLUE)
tft.fillcircle((58, 116), 14, TFT.PURPLE)
tft.pixel((40, 116), TFT.WHITE)
print("ST7735: drawing done")
