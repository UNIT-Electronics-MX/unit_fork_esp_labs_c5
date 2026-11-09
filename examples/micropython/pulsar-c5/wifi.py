"""Pulsar C5: connect to Wi-Fi and print the IP address."""

import network
from time import sleep_ms, ticks_diff, ticks_ms


SSID = "YOUR_SSID"
PASSWORD = "YOUR_PASSWORD"

if SSID == "YOUR_SSID":
    raise ValueError("Edit SSID and PASSWORD before running")

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(SSID, PASSWORD)

started = ticks_ms()
while not wlan.isconnected() and ticks_diff(ticks_ms(), started) < 15_000:
    sleep_ms(250)

if not wlan.isconnected():
    raise OSError("Could not connect to Wi-Fi")

print("Connected. IP:", wlan.ifconfig()[0])
