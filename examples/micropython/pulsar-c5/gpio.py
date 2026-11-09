"""Pulsar C5: blink the LED and read the BOOT button."""

from machine import Pin
from time import sleep_ms


LED_PIN = 27
BOOT_PIN = 28

led = Pin(LED_PIN, Pin.OUT)
boot = Pin(BOOT_PIN, Pin.IN, Pin.PULL_UP)

print("GPIO ready. Hold BOOT to blink faster.")
last_pressed = False

try:
    while True:
        pressed = boot.value() == 0
        if pressed != last_pressed:
            print("BOOT pressed" if pressed else "BOOT released")
            last_pressed = pressed
        led.value(1 - led.value())
        sleep_ms(100 if pressed else 500)
finally:
    led.off()
