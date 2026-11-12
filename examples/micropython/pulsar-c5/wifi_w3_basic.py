"""W3 basic: read current weather over HTTPS with the Pulsar ESP32-C5.

Set Wi-Fi and coordinates below, or connect with W1 first and keep Wi-Fi active.
Run once to print current temperature, humidity and wind. No API key needed.
"""

import network
import requests
from time import sleep_ms, ticks_diff, ticks_ms

WIFI_SSID = ""
WIFI_PASSWORD = ""
PLACE = "Ciudad de Mexico"
LATITUDE = 19.43
LONGITUDE = -99.13


def connect_wifi():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        if not WIFI_SSID:
            raise ValueError("Set WIFI_SSID/WIFI_PASSWORD, or connect with W1 first")
        sta.connect(WIFI_SSID, WIFI_PASSWORD)
        start = ticks_ms()
        while not sta.isconnected():
            if ticks_diff(ticks_ms(), start) > 15000:
                raise OSError("Wi-Fi connection timed out; check SSID and password")
            sleep_ms(250)
    print("Wi-Fi connected:", sta.ifconfig()[0])


def main():
    connect_wifi()
    url = ("https://api.open-meteo.com/v1/forecast?latitude={}&longitude={}"
           "&current=temperature_2m,relative_humidity_2m,wind_speed_10m"
           "&forecast_days=1").format(LATITUDE, LONGITUDE)
    response = requests.get(url, timeout=10)
    try:
        if response.status_code != 200:
            raise OSError("Weather HTTP {}".format(response.status_code))
        current = response.json()["current"]
        print("Weather in {} ({})".format(PLACE, current["time"]))
        print("Temperature: {} C".format(current["temperature_2m"]))
        print("Humidity: {} %".format(current["relative_humidity_2m"]))
        print("Wind: {} km/h".format(current["wind_speed_10m"]))
    finally:
        response.close()


main()
