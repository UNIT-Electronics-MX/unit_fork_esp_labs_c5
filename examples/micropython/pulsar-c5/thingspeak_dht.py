"""Pulsar C5: join Wi-Fi (2.4 or 5 GHz) and post DHT11 readings to ThingSpeak."""

import dht
import network
from machine import Pin
from time import sleep, sleep_ms, ticks_diff, ticks_ms

try:
    import requests
except ImportError:  # older MicroPython firmware names it urequests
    import urequests as requests


WIFI_SSID = "YOUR_WIFI"
WIFI_PASS = "YOUR_PASSWORD"
THINGSPEAK_API_KEY = "YOUR_WRITE_API_KEY"

DHT_PIN = 4  # A2
PERIOD_S = 20  # ThingSpeak's free tier accepts one update every 15 s

if WIFI_SSID == "YOUR_WIFI" or THINGSPEAK_API_KEY == "YOUR_WRITE_API_KEY":
    raise ValueError("Edit WIFI_SSID, WIFI_PASS and THINGSPEAK_API_KEY before running")

sensor = dht.DHT11(Pin(DHT_PIN))
wlan = network.WLAN(network.STA_IF)
wlan.active(True)


def join():
    wlan.connect(WIFI_SSID, WIFI_PASS)
    print("Joining Wi-Fi", end="")
    started = ticks_ms()
    while not wlan.isconnected():
        if ticks_diff(ticks_ms(), started) > 20_000:
            raise OSError("Could not join Wi-Fi: check the name and password")
        print(".", end="")
        sleep_ms(500)
    channel = wlan.config("channel")
    band = "5 GHz" if channel > 14 else "2.4 GHz"
    print("\nIP {}, channel {} ({}), RSSI {} dBm".format(wlan.ifconfig()[0], channel, band, wlan.status("rssi")))


join()
while True:
    try:
        sensor.measure()
    except OSError:
        print("DHT11 read failed")
        sleep(2)
        continue
    temperature = sensor.temperature()
    humidity = sensor.humidity()
    if not wlan.isconnected():
        join()
    url = "http://api.thingspeak.com/update?api_key={}&field1={:.1f}&field2={:.1f}".format(
        THINGSPEAK_API_KEY, temperature, humidity)
    try:
        response = requests.get(url)
        status = response.status_code
        response.close()
    except OSError as error:
        status = "error {}".format(error)
    print("{:.1f} °C {:.0f} %  -> HTTP {}".format(temperature, humidity, status))
    sleep(PERIOD_S)
