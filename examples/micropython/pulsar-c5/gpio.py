"""Pulsar C5: parpadeo del LED y lectura del boton BOOT."""

from machine import Pin
from time import sleep_ms


LED_PIN = 27
BOOT_PIN = 28

led = Pin(LED_PIN, Pin.OUT)
boot = Pin(BOOT_PIN, Pin.IN, Pin.PULL_UP)

print("GPIO listo. Mantén BOOT para acelerar el parpadeo.")
last_pressed = False

try:
    while True:
        pressed = boot.value() == 0
        if pressed != last_pressed:
            print("BOOT pulsado" if pressed else "BOOT liberado")
            last_pressed = pressed
        led.value(1 - led.value())
        sleep_ms(100 if pressed else 500)
finally:
    led.off()
