"""Pulsar C5: lee un potenciometro en A2 (GPIO4)."""

from machine import ADC, Pin
from time import sleep_ms


ADC_PIN = 4
adc = ADC(Pin(ADC_PIN), atten=ADC.ATTN_11DB)

print("A2 (GPIO4): lectura de 0 a 65535 y voltaje aproximado")
while True:
    raw = adc.read_u16()
    millivolts = adc.read_uv() // 1000
    print("raw={}  mV={}".format(raw, millivolts))
    sleep_ms(200)
