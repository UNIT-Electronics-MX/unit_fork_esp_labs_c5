"""Pulsar C5: read a potentiometer on A2 (GPIO4)."""

from machine import ADC, Pin
from time import sleep_ms


ADC_PIN = 4
adc = ADC(Pin(ADC_PIN), atten=ADC.ATTN_11DB)

print("A2 (GPIO4): 0 to 65535 raw reading and approximate voltage")
while True:
    raw = adc.read_u16()
    millivolts = adc.read_uv() // 1000
    print("raw={}  mV={}".format(raw, millivolts))
    sleep_ms(200)
