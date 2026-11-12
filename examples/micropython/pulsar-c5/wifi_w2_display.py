"""W2 display: phone LED control plus live Wi-Fi signal on an ST7735 TFT.

Set WIFI_SSID and WIFI_PASSWORD, or connect with W1 first and keep Wi-Fi active.
Run this file, then open the printed http://IP on a phone on the same network.
Connect the 80x160 ST7735 as in O2 and install st7735.py first.
Press Ctrl-C (or Stop in Code) to end the server.
"""

import json
import network
import socket
from machine import Pin, SPI
from framebuf import FrameBuffer, RGB565
import st7735
from neopixel import NeoPixel
from time import sleep_ms, ticks_diff, ticks_ms

WIFI_SSID = ""
WIFI_PASSWORD = ""
LED_PIN = 0  # Pulsar C5 on-board WS2812
# ST7735 80x160 (same wiring as O2/O3)
MOSI_PIN = 7    # D11
SCK_PIN = 27    # D13
SD_CS_PIN = 23  # Deselect the on-board SD card
CS_PIN = 9      # D4
DC_PIN = 4      # A2
RES_PIN = 5     # A3
WIDTH = 80
HEIGHT = 160

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


def init_display():
    # Draw into a framebuffer and send one image; clearing the TFT directly flickers.
    Pin(SD_CS_PIN, Pin.OUT, value=1)
    spi = SPI(1, baudrate=4_000_000, polarity=0, phase=0,
              sck=Pin(SCK_PIN), mosi=Pin(MOSI_PIN))
    if hasattr(st7735, "TFTColor"):
        tft = st7735.TFT(spi, DC_PIN, RES_PIN, CS_PIN,
                         width=WIDTH, height=HEIGHT, xoffset=26, yoffset=1)
        tft.initg()
        tft.rgb(False)
        tft.invertcolor(True)
        pixels = bytearray(WIDTH * HEIGHT * 2)
        frame = FrameBuffer(pixels, WIDTH, HEIGHT, RGB565)
        probe = bytearray(2)
        FrameBuffer(probe, 1, 1, RGB565).pixel(0, 0, 0xF800)
        swap_bytes = probe[0] != 0xF8

        def colour(red, green, blue):
            value = st7735.TFTColor(red, green, blue)
            return ((value & 0xFF) << 8) | (value >> 8) if swap_bytes else value

        def show():
            tft.image(0, 0, WIDTH - 1, HEIGHT - 1, pixels)

    elif hasattr(st7735, "rgb565"):
        tft = st7735.TFT(spi, DC_PIN, RES_PIN, CS_PIN, xoffset=26, yoffset=1)
        tft.init()
        frame = tft.frame
        colour = st7735.rgb565
        show = tft.show
    else:
        raise RuntimeError("Unsupported st7735.py; install the O2/O3 driver")
    return frame, colour, show


def draw_display(frame, colour, show, sample, history, ip, led_color):
    black = colour(0, 0, 0)
    white = colour(245, 245, 245)
    gray = colour(95, 112, 130)
    green = colour(60, 220, 110)
    yellow = colour(250, 190, 60)
    red = colour(245, 80, 75)
    frame.fill(black)
    frame.text("W2 SIGNAL", 2, 2, white)
    frame.hline(2, 14, 76, gray)
    frame.text("LED " + led_color[:6], 2, 19, white)

    if sample["connected"]:
        bars = sample["bars"]
        accent = green if bars >= 3 else yellow if bars == 2 else red
        frame.text(str(sample["rssi"]) + " dBm", 2, 34, accent)
        frame.text("GOOD" if bars >= 3 else "FAIR" if bars == 2 else "WEAK",
                   2, 47, accent)
    else:
        bars = 0
        accent = red
        frame.text("NO WIFI", 2, 34, accent)

    # Four fixed-height bars. Fill only those reached by the current RSSI.
    for index in range(4):
        height = 7 + index * 5
        x = 10 + index * 17
        y = 80 - height
        frame.rect(x, y, 12, height, gray)
        if index < bars:
            frame.fill_rect(x + 1, y + 1, 10, height - 2, accent)

    frame.text("LAST 24 S", 2, 87, white)
    frame.hline(2, 122, 76, gray)
    last = None
    for index, rssi in enumerate(history):
        x = 3 + index * 3
        if rssi is None:
            last = None
            continue
        # -95 dBm is the bottom of the chart and -40 dBm is the top.
        level = max(0, min(55, rssi + 95))
        y = 119 - (level * 24 // 55)
        if last is not None:
            frame.line(last[0], last[1], x, y, green)
        last = (x, y)

    frame.text("IP", 2, 127, gray)
    if ip:
        parts = ip.split(".")
        frame.text(".".join(parts[:2]), 2, 138, white)
        frame.text(".".join(parts[2:]), 2, 149, white)
    else:
        frame.text("DISCONNECTED", 2, 138, red)
    show()


def main():
    sta = connect_wifi()
    frame, colour, show = init_display()
    led = NeoPixel(Pin(LED_PIN, Pin.OUT), 1)
    state = {"color": "ready"}
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(socket.getaddrinfo("0.0.0.0", 80)[0][-1])
    server.listen(2)
    server.settimeout(0.2)  # Keep the TFT live while waiting for phone requests.
    print("W2 display: open http://{} on a phone on the same Wi-Fi".format(sta.ifconfig()[0]))

    history = []
    previous = None
    last_sample = ticks_ms()
    try:
        while True:
            now = ticks_ms()
            if previous is None or ticks_diff(now, last_sample) >= 1000:
                sample = signal(sta)
                history.append(sample["rssi"])
                if len(history) > 24:
                    history.pop(0)
                ip = sta.ifconfig()[0] if sample["connected"] else ""
                current = (sample["rssi"], sample["bars"], ip, state["color"])
                if current != previous or len(history) > 1:
                    draw_display(frame, colour, show, sample, history, ip, state["color"])
                previous = current
                last_sample = now
            try:
                client, _ = server.accept()
            except OSError:  # accept() timed out; sample RSSI again on the next tick.
                continue
            try:
                serve(client, led, sta, state)
            except OSError as exc:
                print("HTTP error:", exc)
            finally:
                client.close()
            # A colour button should be reflected on the TFT immediately.
            if state["color"] != previous[3]:
                sample = signal(sta)
                ip = sta.ifconfig()[0] if sample["connected"] else ""
                draw_display(frame, colour, show, sample, history, ip, state["color"])
                previous = (sample["rssi"], sample["bars"], ip, state["color"])
    finally:
        server.close()


main()
