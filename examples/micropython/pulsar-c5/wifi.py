"""Pulsar C5: conectate a una red Wi-Fi e imprime la direccion IP."""

import network
from time import sleep_ms, ticks_diff, ticks_ms


SSID = "TU_RED"
PASSWORD = "TU_CLAVE"

if SSID == "TU_RED":
    raise ValueError("Edita SSID y PASSWORD antes de ejecutar")

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(SSID, PASSWORD)

started = ticks_ms()
while not wlan.isconnected() and ticks_diff(ticks_ms(), started) < 15_000:
    sleep_ms(250)

if not wlan.isconnected():
    raise OSError("No se pudo conectar a Wi-Fi")

print("Conectado. IP:", wlan.ifconfig()[0])
