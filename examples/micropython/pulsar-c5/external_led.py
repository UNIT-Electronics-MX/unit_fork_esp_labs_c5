"""Pulsar C5: LED externo con brillo PWM en GPIO9 (D4)."""

from machine import Pin, PWM
from time import sleep_ms


LED_PIN = 9
led = PWM(Pin(LED_PIN), freq=1000, duty_u16=0)

try:
    while True:
        for value in range(0, 65536, 1024):
            led.duty_u16(value)
            sleep_ms(12)
        for value in range(65535, -1, -1024):
            led.duty_u16(value)
            sleep_ms(12)
finally:
    led.duty_u16(0)
    led.deinit()
