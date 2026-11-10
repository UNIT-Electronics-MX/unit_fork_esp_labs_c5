"""Pulsar C5: read a TMP235 analog temperature sensor on A2 and smooth it, like the D3 Arduino sketch."""

from machine import ADC, Pin
from time import sleep_ms


TMP235_PIN = 4  # A2, ADC1_CH3
OFFSET_MV = 500.0  # TMP235: 500 mV at 0 °C ...
MV_PER_C = 10.0  # ... plus 10 mV per °C, so 750 mV is 25 °C
ALPHA = 0.1  # exponential filter: 0.1 keeps 90 % of the previous value

adc = ADC(Pin(TMP235_PIN), atten=ADC.ATTN_11DB)  # full 0–3.1 V range

filtered = None
print("mV\traw_C\tfiltered_C")
while True:
    mv = adc.read_uv() / 1000  # read_uv() applies the chip's factory calibration
    celsius = (mv - OFFSET_MV) / MV_PER_C
    filtered = celsius if filtered is None else ALPHA * celsius + (1 - ALPHA) * filtered
    print("{:.0f}\t{:.1f}\t{:.1f}".format(mv, celsius, filtered))
    sleep_ms(500)
