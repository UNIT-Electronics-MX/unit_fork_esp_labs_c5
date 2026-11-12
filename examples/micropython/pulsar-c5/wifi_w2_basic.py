"""W2 basic: control the Pulsar C5 RGB LED and view Wi-Fi signal in one page.

Set WIFI_SSID and WIFI_PASSWORD, or connect with W1 first and keep Wi-Fi active.
Run this file, then open the printed http://IP on a phone on the same network.
Press Ctrl-C (or Stop in Code) to end the server.
"""

import json
import network
import socket
from machine import Pin
from neopixel import NeoPixel
from time import sleep_ms, ticks_diff, ticks_ms

WIFI_SSID = ""
WIFI_PASSWORD = ""
LED_PIN = 0  # Pulsar C5 on-board WS2812

COLORS = {
    b"/red": (24, 0, 0),
    b"/green": (0, 24, 0),
    b"/blue": (0, 0, 24),
    b"/off": (0, 0, 0),
}


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
    return sta


def signal(sta):
    if not sta.isconnected():
        return {"connected": False, "rssi": None, "bars": 0}
    try:
        rssi = sta.status("rssi")
    except OSError:
        return {"connected": False, "rssi": None, "bars": 0}
    bars = 4 if rssi >= -55 else 3 if rssi >= -67 else 2 if rssi >= -78 else 1
    return {"connected": True, "rssi": rssi, "bars": bars}


def page(color):
    return ("<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Pulsar C5 W2</title><style>body{font:18px sans-serif;max-width:440px;"
            "margin:2rem auto;padding:0 1rem;background:#101827;color:#edf4ff}"
            "section{background:#202d42;padding:1rem;border-radius:14px;margin:1rem 0}"
            "a{display:inline-block;padding:.8rem;margin:.25rem;border-radius:9px;background:#d6e9ff;"
            "color:#101827;text-decoration:none}#track{height:16px;background:#46556b;border-radius:10px}"
            "#fill{height:100%;width:0;background:#64e7a5;border-radius:10px}</style></head>"
            "<body><h1>Pulsar C5 · W2</h1><section><h2>Wi-Fi signal</h2>"
            "<p id='rssi'>Reading…</p><div id='track'><div id='fill'></div></div>"
            "<small>Signal from the connected access point, in dBm.</small></section>"
            "<section><h2>RGB LED</h2><p>Colour: " + color + "</p>"
            "<a href='/red'>Red</a><a href='/green'>Green</a>"
            "<a href='/blue'>Blue</a><a href='/off'>Off</a></section>"
            "<script>async function refresh(){try{const r=await fetch('/api/signal',{cache:'no-store'});"
            "const s=await r.json();document.getElementById('rssi').textContent=s.connected?"
            "s.rssi+' dBm · '+s.bars+'/4 bars':'Wi-Fi disconnected';"
            "document.getElementById('fill').style.width=(s.bars*25)+'%'}catch(e){"
            "document.getElementById('rssi').textContent='Signal unavailable'}}"
            "refresh();setInterval(refresh,2000)</script></body></html>").encode()


def send_response(client, body, content_type):
    headers = ("HTTP/1.0 200 OK\r\nContent-Type: {}\r\nContent-Length: {}\r\n"
               "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
               .format(content_type, len(body))).encode()
    client.sendall(headers + body)


def serve(client, led, sta, state):
    client.settimeout(3)
    parts = client.recv(512).split(b" ", 2)
    path = parts[1].split(b"?", 1)[0] if len(parts) > 1 else b"/"
    if path == b"/api/signal":
        send_response(client, json.dumps(signal(sta)).encode(), "application/json")
        return
    if path in COLORS:
        led[0] = COLORS[path]
        led.write()
        state["color"] = path[1:].decode()
    send_response(client, page(state["color"]), "text/html; charset=utf-8")


def main():
    sta = connect_wifi()
    led = NeoPixel(Pin(LED_PIN, Pin.OUT), 1)
    state = {"color": "ready"}
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(socket.getaddrinfo("0.0.0.0", 80)[0][-1])
    server.listen(2)
    print("W2 basic: open http://{} on a phone on the same Wi-Fi".format(sta.ifconfig()[0]))
    try:
        while True:
            client, _ = server.accept()
            try:
                serve(client, led, sta, state)
            except OSError as exc:
                print("HTTP error:", exc)
            finally:
                client.close()
    finally:
        server.close()


main()
