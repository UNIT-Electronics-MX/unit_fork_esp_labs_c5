"""D2: plot a UNIT ATOM TEMT6000 light reading on an SSD1306 OLED."""

from machine import I2C, Pin
from ssd1306 import SSD1306_I2C
from time import sleep_ms, ticks_add, ticks_diff, ticks_ms


# Install libraries/ssd1306.py as ssd1306.py on the board first.
# The OLED and the DDP light sensor share the Pulsar C5 QWIIC bus.
OLED_ADDRESS = 0x3C
SENSOR_ADDRESS = 0x20
SAMPLE_MS = 50
ADC_MAX = 4095
HISTORY_LENGTH = 126

i2c = I2C(0, sda=Pin(1), scl=Pin(6), freq=400_000)
found = i2c.scan()
if OLED_ADDRESS not in found:
    raise OSError("SSD1306 OLED not found at 0x3C")
if SENSOR_ADDRESS not in found:
    raise OSError("TEMT6000 DDP sensor not found at 0x20")


def ddp_read(command, length):
    # DDP requires a STOP after the command and a short delay before the read.
    i2c.writeto(SENSOR_ADDRESS, bytes((command,)))
    sleep_ms(5)
    return i2c.readfrom(SENSOR_ADDRESS, length)


# Identify the DDP device before interpreting 0x60 as a 12-bit ADC reading.
protocol = ddp_read(0x04, 2)
device = ddp_read(0x00, 2)
firmware = ddp_read(0x01, 2)
hardware = ddp_read(0x02, 2)
capabilities = ddp_read(0x03, 4)

print("TEMT6000  protocol {}.{}, firmware {}.{}, hardware {}.{}".format(
     protocol[0], protocol[1], firmware[0], firmware[1],
    hardware[0], hardware[1]))

print("Capabilities:", capabilities)
print("raw")

display = SSD1306_I2C(128, 64, i2c, addr=OLED_ADDRESS)
history = [0] * HISTORY_LENGTH
write_index = 0
count = 0
next_sample = ticks_ms()


def sample_y(value):
    return 62 - value * 42 // ADC_MAX


def draw_graph(value):
    display.fill(0)
    display.text("TEMT6000 0x20", 0, 0)
    display.text("ADC:{} {}%".format(value, value * 100 // ADC_MAX), 0, 9)
    display.rect(0, 19, 128, 45, 1)
    middle_y = sample_y(ADC_MAX // 2)
    for x in range(1, 127, 4):
        display.pixel(x, middle_y, 1)

    oldest = write_index if count == HISTORY_LENGTH else 0
    if count == 1:
        display.pixel(126, sample_y(history[oldest]), 1)
    else:
        offset = HISTORY_LENGTH - count
        for position in range(1, count):
            previous = history[(oldest + position - 1) % HISTORY_LENGTH]
            current = history[(oldest + position) % HISTORY_LENGTH]
            x = offset + position
            display.line(x, sample_y(previous), x + 1, sample_y(current), 1)
    display.show()


while True:
    now = ticks_ms()
    if ticks_diff(now, next_sample) < 0:
        sleep_ms(1)
        continue
    next_sample = ticks_add(now, SAMPLE_MS)
    response = ddp_read(0x60, 2)  # READ_ADC0: little-endian, 0..4095.
    raw = response[0] | (response[1] << 8)
    if raw > ADC_MAX:
        raise OSError("Invalid TEMT6000 ADC reading")
    history[write_index] = raw
    write_index = (write_index + 1) % HISTORY_LENGTH
    count = min(count + 1, HISTORY_LENGTH)
    draw_graph(raw)
    print(raw)
