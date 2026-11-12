"""W2 Phone control: control the Pulsar C5 from your phone, your computer, and from anywhere.

Needs wifi_kit.py saved on the board. Type help in the serial console.
Everything from W1 (scan, join, views) plus a small web server built on asyncio:

    http://bench.local/   the phone page               /api   the REST API (list below)
    ws://bench.local/ws   live status, networks and BOOT presses, pushed every second

Variation 1, on your Wi-Fi: not joined yet? The board opens the set-up network bench-setup (its
password is printed here). Join it from a phone, pick your Wi-Fi on the page that opens, type
the password. esp-labs/apps/dashboard gives the same controls on your computer.
Variation 2, from anywhere: relay wss://<your tunnel host> makes the board dial OUT to your
dashboard server and keep a WebSocket open, so the dashboard reaches it through a tunnel.

REST API (JSON), the same as the C++ lab, so the phone page and the dashboard work unchanged:
    GET /api/status | GET /api/networks | POST /api/scan
    PUT /api/view {"view": "signal"} | PUT /api/settings {"autoScan": true, "backlight": 180}
    PUT /api/led {"r": 0, "g": 120, "b": 255} | PUT /api/message {"text": "hello"}
    PUT /api/wifi {"ssid": "...", "password": "..."}   open during set-up, admin after
    DELETE /api/wifi (admin) | POST /api/reboot (admin)
Admin = header "Authorization: Bearer <token>"; the console command token prints it.

Views: networks | channels | status | signal | message
BOOT: click = next view, double-click = scan now, long press = auto-scan on/off
"""

import asyncio
import binascii
import gc
import hashlib
import json
import network
import os
import socket
import struct
import time
from machine import reset

import wifi_kit as kit

if getattr(kit, "VERSION", 1) < 3:
    raise SystemExit("wifi_kit.py on the board is too old: press Install wifi_kit.py, then run"
                     " import machine; machine.soft_reset() and start this script again.")

FORGET_SAVED = False  # True: erase the saved network when the script starts, then set it back

FIRMWARE = "wifi-lab-w2-micropython"
VIEWS = ("networks", "channels", "status", "signal", "message")
STATUS_PUSH_MS = 1000
PORTAL_AFTER_FAIL_MS = 60_000      # keep failing this long and the set-up network opens
PORTAL_CLOSE_AFTER_JOIN_MS = 30_000
SETUP_SSID = "bench-setup"
MAX_BODY = 512
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # fixed by the WebSocket standard (RFC 6455)


# ---- The admin token ------------------------------------------------------------------------

class Token:
    """A random secret in NVS that guards reboot and forget. The console prints it; the API never."""

    def __init__(self):
        self._settings = kit.Settings("mpapi")
        self.value = self._settings.get("token")
        if len(self.value) != 32:
            self.regenerate()

    def regenerate(self):
        self.value = binascii.hexlify(os.urandom(16)).decode()
        self._settings.set("token", self.value)

    def allows(self, header):
        """Compares in constant time, so response timing doesn't leak how many characters matched."""
        expected = "Bearer " + self.value
        if len(header) != len(expected):
            return False
        diff = 0
        for a, b in zip(header, expected):
            diff |= ord(a) ^ ord(b)
        return diff == 0


# ---- The set-up network and its captive portal ---------------------------------------------

class Portal:
    """Access point bench-setup, plus a tiny DNS server that answers every name with the board's
    own address, so whatever page the phone opens lands on the set-up page (a captive portal)."""

    def __init__(self, sta_mac):
        self.ap = network.WLAN(network.AP_IF)
        self.password = "bench-" + binascii.hexlify(bytes(sta_mac)[3:6]).decode()
        self.active = False
        self.started_at = 0

    def start(self):
        if self.active:
            return
        self.ap.active(True)
        try:
            self.ap.config(ssid=SETUP_SSID, key=self.password, security=3)  # 3 = WPA2-PSK
        except (TypeError, ValueError):
            self.ap.config(essid=SETUP_SSID, password=self.password, authmode=3)  # older names
        self.active = True
        self.started_at = time.ticks_ms()
        print("Set-up network '{}' is up, password {}. Join it and open http://{}".format(
            SETUP_SSID, self.password, self.ip()))

    def stop(self):
        if self.active:
            self.ap.active(False)
            self.active = False
            print("Set-up network closed")

    def ip(self):
        return self.ap.ifconfig()[0]

    def active_for(self):
        return time.ticks_diff(time.ticks_ms(), self.started_at) if self.active else 0

    async def dns(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", 53))
        sock.setblocking(False)
        try:
            while True:
                try:
                    query, client = sock.recvfrom(512)
                except OSError:
                    await asyncio.sleep_ms(50 if self.active else 500)
                    continue
                if self.active:
                    answer = dns_answer(query, bytes(int(part) for part in self.ip().split(".")))
                    if answer:
                        sock.sendto(answer, client)
        finally:
            sock.close()


def dns_answer(query, ip):
    """Answer the first question of a DNS query with one A record: ip, valid for 60 s."""
    if len(query) < 17 or query[4:6] != b"\x00\x01":
        return None
    end = 12
    while end < len(query) and query[end]:
        end += query[end] + 1
    end += 5  # the zero byte that ends the name, then type and class
    if end > len(query):
        return None
    return (query[:2] + b"\x81\x80" + b"\x00\x01\x00\x01\x00\x00\x00\x00" + query[12:end]
            + b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04" + ip)


# ---- WebSocket frames (RFC 6455) ------------------------------------------------------------

def ws_frame(data, opcode=1, mask=False):
    """One unfragmented frame. A client must mask what it sends; a server must not."""
    n = len(data)
    bit = 0x80 if mask else 0
    if n < 126:
        head = bytes((0x80 | opcode, bit | n))
    elif n < 65536:
        head = struct.pack("!BBH", 0x80 | opcode, bit | 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, bit | 127, n)
    if not mask:
        return head + data
    key = os.urandom(4)
    body = bytearray(data)
    for i in range(n):
        body[i] ^= key[i & 3]
    return head + key + body


async def ws_read(reader):
    """Returns (opcode, payload) of the next frame."""
    b0, b1 = await reader.readexactly(2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack("!H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", await reader.readexactly(8))[0]
    if n > 4096:
        raise OSError("frame too big")
    key = await reader.readexactly(4) if b1 & 0x80 else None
    data = bytearray(await reader.readexactly(n))
    if key:
        for i in range(n):
            data[i] ^= key[i & 3]
    return b0 & 0x0F, bytes(data)


def wrap(kind, data):
    return '{{"type":"{}","data":{}}}'.format(kind, data)


# ---- The bench: what the console, the button, the web and the relay all change --------------

if FORGET_SAVED:
    kit.forget_saved()

connection = kit.Connection()
console = kit.Console(mask=kit.hide_join_password)
led = kit.StatusLed()
lab = kit.ScanLab(connection, console, led)
# Each scan blocks the radio and this whole program for 2-5 s: web requests wait meanwhile.
# Joined, scan only when asked (console, BOOT, the page's Scan button).
lab.AUTO_SCAN_JOINED_MS = 0
button = kit.Button()
token = Token()
portal = Portal(connection.wlan.config("mac"))
relay_settings = kit.Settings("relay")

bench = {"view": 0, "backlight": 255, "led": (0, 0, 0), "message": "", "reboot_at": None}
clients = []      # the open WebSocket connections (their stream writers)
todo = []         # what web requests asked for, done by the main loop after they got their answer
networks_cache = ['{"scans":0,"durationMs":0,"networks":[]}']
relay_link = {"state": "not-configured", "writer": None}


def show_message():
    print("Message: {}".format(bench["message"] or "(none yet: send one from the phone page)"))


SHOW = (lab.show_networks, lab.show_channels, lab.show_status, lab.show_signal, show_message)


def set_view(number):
    bench["view"] = number % len(VIEWS)
    print("view {}: {}".format(bench["view"] + 1, VIEWS[bench["view"]]))
    SHOW[bench["view"]]()


def set_led(r, g, b):
    """Black gives the LED back to showing the Wi-Fi state."""
    bench["led"] = (r, g, b)
    led.claimed = None if (r, g, b) == (0, 0, 0) else (r, g, b)


def set_backlight(level):
    """No screen to dim: the brightness slider sets the LED's brightness instead."""
    bench["backlight"] = level
    led.LEVEL = max(1, level * 60 // 255)  # the next led.wifi() redraws at the new level


def wifi_state():
    return {kit.CONNECTED: "connected", kit.CONNECTING: "connecting", kit.FAILED: "failed"}.get(
        connection.state, "not-set-up")


def status_json():
    wifi = {"state": wifi_state(), "ssid": connection.ssid, "hostname": kit.HOSTNAME + ".local",
            "reason": kit.reason_hint(connection.reason)}
    if connection.joined():
        wifi.update(ip=connection.ip(), rssi=connection.rssi(), channel=connection.channel())
    r, g, b = bench["led"]
    status = {
        "firmware": FIRMWARE, "uptimeS": time.ticks_ms() // 1000, "heapFree": gc.mem_free(), "psramFree": 0,
        "clients": len(clients), "wifi": wifi, "remote": {"state": relay_link["state"]},
        "setup": {"active": portal.active, "ssid": SETUP_SSID},  # the password stays on the console
        "screen": {"view": VIEWS[bench["view"]], "views": list(VIEWS), "autoScan": lab.auto_scan,
                   "backlight": bench["backlight"], "message": bench["message"]},
        "led": {"r": r, "g": g, "b": b},
        "scan": {"running": False, "count": len(lab.scanner.networks), "durationMs": lab.scanner.last_ms},
    }
    celsius = kit.chip_temperature()
    if celsius is not None:
        status["tempC"] = round(celsius, 1)
    return json.dumps(status)


def remember_networks():
    """After every scan: hardware addresses (BSSIDs) stay off the web on purpose."""
    networks_cache[0] = json.dumps({
        "scans": lab.scanner.scans, "durationMs": lab.scanner.last_ms,
        "networks": [{"ssid": n["ssid"], "rssi": n["rssi"], "channel": n["channel"], "auth": kit.auth_name(n["auth"])}
                     for n in lab.scanner.networks]})
    todo.append(lambda: publish("networks", networks_cache[0]))


lab.on_scan = remember_networks


def portal_lines():
    lines = ["Web: http://{}.local or http://{} | {} live clients".format(
        kit.HOSTNAME, connection.ip() if connection.joined() else "-", len(clients))]
    if portal.active:
        lines.append("Set-up network '{}' open, password {}, page http://{}".format(SETUP_SSID, portal.password, portal.ip()))
    lines.append("Remote link: {}{}".format(relay_link["state"], " | " + relay_url() if relay_url() else ""))
    return lines


lab.status_lines = portal_lines


# ---- What each API request means (the local server and the relay both ask here) ------------

NEEDS_ADMIN = (401, "admin action: send Authorization: Bearer <token> (console command: token)")


def api(method, path, body, admin):
    """Checks a request and queues what it asks for. Returns (status, error or None)."""
    if method == "POST" and path == "/api/scan":
        todo.append(lambda: lab.scan_now("web"))
        return 202, None
    if method == "PUT" and path == "/api/view":
        wanted = body.get("view")
        index = VIEWS.index(wanted) if wanted in VIEWS else wanted if isinstance(wanted, int) else -1
        if not 0 <= index < len(VIEWS):
            return 400, "view must be one of the names in /api/status screen.views"
        todo.append(lambda: set_view(index))
        return 200, None
    if method == "PUT" and path == "/api/settings":
        auto, level = body.get("autoScan"), body.get("backlight")
        if not isinstance(auto, bool) and level is None:
            return 400, "send autoScan (true/false) and/or backlight (0-255)"
        if level is not None and not (isinstance(level, int) and 0 <= level <= 255):
            return 400, "backlight must be 0-255"
        if isinstance(auto, bool):
            todo.append(lambda: lab.set_auto(auto))
        if level is not None:
            todo.append(lambda: set_backlight(level))
        return 200, None
    if method == "PUT" and path == "/api/led":
        rgb = [body.get(k) for k in ("r", "g", "b")]
        if not all(isinstance(c, int) and 0 <= c <= 255 for c in rgb):
            return 400, "r, g and b must be 0-255"
        todo.append(lambda: set_led(*rgb))
        return 200, None
    if method == "PUT" and path == "/api/message":
        text = body.get("text", "")
        if not isinstance(text, str) or len(text) > 96:
            return 400, "text is limited to 96 characters"

        def show():
            bench["message"] = text
            set_view(VIEWS.index("message"))
        todo.append(show)
        return 200, None
    if method == "PUT" and path == "/api/wifi":
        # During set-up nobody has the token yet (only the console shows it), so saving a network
        # is open until the board is joined. After that, changing networks is an admin action.
        if connection.joined() and not admin:
            return NEEDS_ADMIN
        ssid, password = body.get("ssid", ""), body.get("password", "")
        if not isinstance(ssid, str) or not 1 <= len(ssid) <= 32:
            return 400, "ssid must be 1-32 characters"
        if not isinstance(password, str) or len(password) > 63 or 0 < len(password) < 8:
            return 400, "password must be empty (open network) or 8-63 characters"
        todo.append(lambda: connection.save(ssid, password))
        return 202, None
    if method == "DELETE" and path == "/api/wifi":
        if not admin:
            return NEEDS_ADMIN
        todo.append(connection.forget)
        return 200, None
    if method == "POST" and path == "/api/reboot":
        if not admin:
            return NEEDS_ADMIN
        bench["reboot_at"] = time.ticks_add(time.ticks_ms(), 800)  # let the answer go out first
        return 202, None
    if path == "/api/ota":
        return 501, "MicroPython runs scripts, not firmware images: save new scripts over USB from the Code view"
    return 404, "no such endpoint, see /api"


API_INDEX = json.dumps({"endpoints": [
    "GET /api/status", "GET /api/networks", "POST /api/scan", "PUT /api/view", "PUT /api/settings",
    "PUT /api/led", "PUT /api/message", "PUT /api/wifi", "DELETE /api/wifi (admin)", "POST /api/reboot (admin)",
    "WS /ws"]})


# ---- The local web server -------------------------------------------------------------------

REASONS = {200: "OK", 202: "Accepted", 302: "Found", 400: "Bad Request", 401: "Unauthorized",
           404: "Not Found", 413: "Payload Too Large", 501: "Not Implemented"}


async def respond(writer, status, body, kind="application/json", extra=""):
    data = body.encode() if isinstance(body, str) else body
    writer.write("HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nCache-Control: no-store\r\n"
                 "Connection: close\r\n{}\r\n".format(status, REASONS.get(status, ""), kind, len(data), extra).encode())
    writer.write(data)
    await writer.drain()


def error_body(message):
    return json.dumps({"error": message})


def is_captive(host):
    """A phone on the set-up network asking some other site (its 'am I online?' check)."""
    host = host.split(":")[0]
    return portal.active and host not in (portal.ip(), kit.HOSTNAME + ".local", kit.HOSTNAME,
                                          connection.ip() if connection.joined() else "")


async def serve(reader, writer):
    upgraded = False
    try:
        request = (await reader.readline()).decode()
        method, target, _ = request.split(" ", 2)
        headers = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            name, _, value = line.decode().partition(":")
            headers[name.strip().lower()] = value.strip()
        path = target.split("?")[0]
        if path == "/ws" and headers.get("upgrade", "").lower() == "websocket":
            upgraded = True
            await websocket(reader, writer, headers)
            return
        length = int(headers.get("content-length", "0") or 0)
        if path == "/api/ota" or length > MAX_BODY:
            status, problem = api(method, path, {}, False) if path == "/api/ota" else (413, "body too big")
            await respond(writer, status, error_body(problem))
            return
        raw = await reader.readexactly(length) if length else b""
        if method == "GET" and path == "/":
            await respond(writer, 200, PAGE, "text/html; charset=utf-8")
        elif method == "GET" and path == "/api":
            await respond(writer, 200, API_INDEX)
        elif method == "GET" and path == "/api/status":
            await respond(writer, 200, status_json())
        elif method == "GET" and path == "/api/networks":
            await respond(writer, 200, networks_cache[0])
        elif path.startswith("/api/"):
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                body = None
            if not isinstance(body, dict):
                await respond(writer, 400, error_body("send a JSON object"))
                return
            status, problem = api(method, path, body, token.allows(headers.get("authorization", "")))
            await respond(writer, status, error_body(problem) if problem else '{"ok":true}')
        elif is_captive(headers.get("host", "")):
            await respond(writer, 302, "", "text/plain", "Location: http://{}/\r\n".format(portal.ip()))
        else:
            await respond(writer, 404, error_body("no such page, see /api"))
    except (OSError, ValueError, EOFError):
        pass
    finally:
        if not upgraded:
            writer.close()
            await writer.wait_closed()


async def websocket(reader, writer, headers):
    key = headers.get("sec-websocket-key", "").encode()
    accept = binascii.b2a_base64(hashlib.sha1(key + WS_GUID).digest()).strip()
    writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                 b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
    # Send the latest picture straight away, so a new page doesn't wait a second.
    writer.write(ws_frame(wrap("status", status_json()).encode()))
    writer.write(ws_frame(wrap("networks", networks_cache[0]).encode()))
    await writer.drain()
    clients.append(writer)
    print("+ web client ({} live)".format(len(clients)))
    try:
        while True:
            opcode, payload = await ws_read(reader)
            if opcode == 8:  # close
                break
            if opcode == 9:  # ping: answer with a pong carrying the same bytes
                writer.write(ws_frame(payload, opcode=10))
                await writer.drain()
    except (OSError, ValueError, EOFError):
        pass
    finally:
        if writer in clients:
            clients.remove(writer)
        writer.close()
        await writer.wait_closed()
        print("- web client ({} live)".format(len(clients)))


async def broadcast(text):
    frame = ws_frame(text.encode())
    for writer in list(clients):
        try:
            writer.write(frame)
            await writer.drain()
        except OSError:
            if writer in clients:
                clients.remove(writer)


def publish(kind, data):
    """Push to every open page and to the relay: status every second, networks after scans,
    BOOT presses as events."""
    text = wrap(kind, data)
    if clients:
        asyncio.create_task(broadcast(text))
    if relay_link["state"] == "online":
        asyncio.create_task(relay_send(text))


# ---- The remote link: the board dials out to your dashboard server -------------------------

def relay_url():
    return relay_settings.get("url")


def parse_relay(url):
    """wss://host[:port][/path] or ws://host[:port][/path] -> (secure, host, port, path) or None."""
    for scheme, secure, port in (("wss://", True, 443), ("ws://", False, 80)):
        if url.startswith(scheme):
            rest = url[len(scheme):]
            break
    else:
        return None
    host, slash, path = rest.partition("/")
    path = "/" + path if slash and path else "/device"
    if ":" in host:
        host, _, number = host.partition(":")
        if not number.isdigit() or not 0 < int(number) < 65536:
            return None
        port = int(number)
    return (secure, host, port, path) if host else None


def set_relay(url):
    if url and parse_relay(url) is None:
        print("relay: can't read '{}'. Use wss://your-tunnel-host or ws://ip:port".format(url))
        return
    relay_settings.set("url", url)
    print("relay: " + ("off" if not url else "saved, connecting to " + url))
    writer = relay_link["writer"]
    if writer:
        writer.close()  # the link task notices and starts again with the new address


async def relay_send(text):
    writer = relay_link["writer"]
    if writer is None:
        return
    try:
        writer.write(ws_frame(text.encode(), mask=True))
        await writer.drain()
    except OSError:
        pass


async def relay_answer(writer, payload):
    try:
        message = json.loads(payload)
    except ValueError:
        return
    if not isinstance(message, dict) or message.get("type") != "request":
        return
    body = message.get("body") if isinstance(message.get("body"), dict) else {}
    # Only the server holding our token accepted this link, so everything arriving counts as admin.
    status, problem = api(message.get("method", ""), message.get("path", ""), body, True)
    reply = {"type": "reply", "id": message.get("id"), "status": status,
             "body": {"error": problem} if problem else {"ok": True}}
    writer.write(ws_frame(json.dumps(reply).encode(), mask=True))
    await writer.drain()
    print("relay: {} {} -> {}".format(message.get("method"), message.get("path"), status))


async def relay_task():
    complained = False
    while True:
        url = relay_url()
        target = parse_relay(url) if url else None
        if target is None:
            relay_link["state"] = "not-configured"
            await asyncio.sleep(1)
            continue
        if not connection.joined():
            relay_link["state"] = "waiting-for-wifi"
            await asyncio.sleep(1)
            continue
        secure, host, port, path = target
        relay_link["state"] = "connecting"
        writer = None
        try:
            context = None
            if secure:
                import ssl
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.verify_mode = ssl.CERT_NONE  # encrypted, but the certificate is not checked
            reader, writer = await asyncio.open_connection(host, port, ssl=context)
            key = binascii.b2a_base64(os.urandom(16)).strip().decode()
            writer.write("GET {} HTTP/1.1\r\nHost: {}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         "Sec-WebSocket-Key: {}\r\nSec-WebSocket-Version: 13\r\n"
                         "Authorization: Bearer {}\r\n\r\n".format(path, host, key, token.value).encode())
            await writer.drain()
            answer = (await reader.readline()).decode()
            if " 101 " not in answer:
                raise OSError("server said " + answer.strip()[:60])
            while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                pass
            relay_link["writer"] = writer
            relay_link["state"] = "online"
            complained = False
            print("relay: online, connected to " + host)
            writer.write(ws_frame(wrap("networks", networks_cache[0]).encode(), mask=True))
            writer.write(ws_frame(wrap("status", status_json()).encode(), mask=True))
            await writer.drain()
            while True:
                try:
                    opcode, payload = await asyncio.wait_for(ws_read(reader), 20)
                except asyncio.TimeoutError:
                    # Quiet for 20 s: tunnels close idle links, so ping. A dead link fails here.
                    writer.write(ws_frame(b"", opcode=9, mask=True))
                    await writer.drain()
                    continue
                if opcode == 8:
                    break
                if opcode == 9:
                    writer.write(ws_frame(payload, opcode=10, mask=True))
                    await writer.drain()
                elif opcode == 1:
                    await relay_answer(writer, payload)
            print("relay: the server closed the link, retrying in 5 s")
        except (OSError, ValueError, EOFError) as error:
            if relay_link["state"] == "online":
                print("relay: lost the server ({}), retrying every 5 s".format(error))
            elif not complained:
                print("relay: can't reach {} yet ({}). Is the server + tunnel running, the address current,"
                      " and ESP_TOKEN in its .env = console token?".format(host, error))
                complained = True
        finally:
            relay_link["writer"] = None
            if writer:
                writer.close()
        relay_link["state"] = "connecting"
        await asyncio.sleep(5)


# ---- The console ----------------------------------------------------------------------------

def show_help():
    print()
    print("Commands: scan | view <1-5> | networks | channels | status | signal | message | auto on|off")
    print("          join <#> [password] | join <network name> | forget | reconnect | tz <+1 | -6>")
    print("          reach [ip] [port] | setup | token | token new | relay [wss://host | off] | stop | help")
    print("BOOT: click = next view, double-click = scan now, long press = auto-scan on/off")


def handle(text):
    if lab.take_password(text):
        return
    command, argument = kit.split_command(text)
    if command in VIEWS:
        set_view(VIEWS.index(command))
    elif command == "view" and argument.isdigit() and 1 <= int(argument) <= len(VIEWS):
        set_view(int(argument) - 1)
    elif command == "setup":
        portal.start()
    elif command == "token" and argument == "new":
        token.regenerate()
        print("New admin token: {}  (the old one stops working now)".format(token.value))
    elif command == "token":
        print("Admin token: {}  (put it in esp-labs/apps/dashboard/.env as ESP_TOKEN)".format(token.value))
    elif command == "relay":
        if not argument:
            print("Remote link: {}{}".format(relay_link["state"], " | " + relay_url() if relay_url() else ""))
        else:
            set_relay("" if argument == "off" else argument)
    elif command in ("stop", "exit", "quit"):
        raise SystemExit  # leaves through finally: server closed, LED off, back to >>>
    elif command in ("help", "?"):
        show_help()
    elif not lab.handle(command, argument):
        print("unknown command '{}' (type help)".format(text))


def button_event(gesture):
    publish("event", json.dumps({"source": "BOOT", "gesture": gesture, "uptimeS": time.ticks_ms() // 1000}))


# ---- The main loop --------------------------------------------------------------------------

failing_since = None


def manage_portal():
    """Open the set-up network when there is nothing to join (or joining keeps failing), and
    close it a while after the board is joined."""
    global failing_since
    if connection.state == kit.NO_CREDENTIALS:
        portal.start()
    if connection.state == kit.FAILED:
        failing_since = failing_since or time.ticks_ms()
        if time.ticks_diff(time.ticks_ms(), failing_since) > PORTAL_AFTER_FAIL_MS:
            portal.start()
    elif connection.state != kit.CONNECTING:
        failing_since = None
    if connection.joined() and portal.active_for() > PORTAL_CLOSE_AFTER_JOIN_MS:
        portal.stop()


async def main():
    server = await asyncio.start_server(serve, "0.0.0.0", 80)
    tasks = [asyncio.create_task(portal.dns()), asyncio.create_task(relay_task())]
    print("Web server on port 80: phone page at /, API at /api, live updates at /ws")
    pushed_at = time.ticks_ms()
    try:
        while True:
            line = console.line()
            if line is not None:
                handle(line)
            gesture = button.poll()
            if gesture == kit.CLICK:
                set_view(bench["view"] + 1)
                button_event("click")
            elif gesture == kit.DOUBLE:
                lab.scan_now("BOOT")
                button_event("double-click")
            elif gesture == kit.LONG:
                lab.set_auto(not lab.auto_scan)
                button_event("long-press")
            while todo:
                todo.pop(0)()
            if lab.report(connection.tick()):
                set_view(VIEWS.index("status"))
            manage_portal()
            # No automatic scans while the set-up network is open: a scan would cut off the phone.
            if lab.auto_scan_due(blocked=portal.active):
                lab.scan_now("auto", quiet=True)
            if bench["reboot_at"] is not None and time.ticks_diff(time.ticks_ms(), bench["reboot_at"]) >= 0:
                print("Rebooting (asked over the web)")
                reset()
            if time.ticks_diff(time.ticks_ms(), pushed_at) >= STATUS_PUSH_MS:
                pushed_at = time.ticks_ms()
                if clients or relay_link["state"] == "online":
                    publish("status", status_json())
            led.wifi(connection.state)
            await asyncio.sleep_ms(20)
    finally:
        for task in tasks:
            task.cancel()
        for writer in list(clients):
            writer.close()
        server.close()  # frees port 80 for the next run


# The phone page: plain HTML and JavaScript, no libraries, so it works without internet. It uses
# the same REST API and WebSocket as the dashboard in esp-labs/apps/dashboard.
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bench</title>
<style>
:root{--bg:#f2f4f3;--card:#fff;--ink:#17201d;--muted:#5b6964;--line:#d3dbd7;--accent:#b0601f;--ok:#1f7a4a;--warn:#a05a00;--bad:#b3261e;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#0e1412;--card:#161e1b;--ink:#e4ebe7;--muted:#9aaba4;--line:#2b3833;--accent:#e39a5c;--ok:#5fcf8f;--warn:#f0b85a;--bad:#ff8a80;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,sans-serif;padding:16px}
main{max-width:560px;margin:0 auto;display:grid;gap:12px}h1{font-size:22px;margin:0}h2{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-variant-numeric:tabular-nums}.kv dt{color:var(--muted)}.kv dd{margin:0}
button,input,select{font:inherit;color:inherit;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 12px}button{cursor:pointer}button.on{background:var(--ink);color:var(--card)}button:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
input[type=text],input[type=password]{flex:1;min-width:0}input[type=range]{flex:1}.dot{width:10px;height:10px;border-radius:50%;display:inline-block;background:var(--line)}
.muted{color:var(--muted);font-size:13px}#feed{list-style:none;margin:0;padding:0;max-height:140px;overflow:auto;font:13px ui-monospace,monospace}
table{width:100%;border-collapse:collapse;font-size:14px}td{padding:4px 6px;border-bottom:1px solid var(--line)}td.n{text-align:right;font-variant-numeric:tabular-nums}
</style></head><body><main>
<header class="row"><span class="dot" id="link"></span><h1>Bench</h1><span class="muted" id="sub">connecting…</span></header>
<section id="setup" hidden><h2>Set up Wi-Fi</h2><p class="muted">Pick your network and type its password. It is stored on the board only.</p>
<div class="row"><select id="ssid" aria-label="Network"></select><button id="rescan" type="button">Scan</button></div>
<div class="row" style="margin-top:8px"><input id="pass" type="password" placeholder="Password" aria-label="Password" autocomplete="current-password"><button id="save" type="button">Join</button></div></section>
<section><h2>Status</h2><dl class="kv" id="kv"></dl></section>
<section><h2>Console view</h2><p class="muted">The Pulsar C5 has no screen: each view prints on its serial console.</p><div class="row" id="views"></div>
<div class="row" style="margin-top:10px"><label for="bl" class="muted">LED brightness</label><input id="bl" type="range" min="0" max="255"></div>
<div class="row" style="margin-top:10px"><button id="auto" type="button">Auto-scan</button><button id="scan" type="button">Scan now</button></div></section>
<section><h2>Message for the console</h2><div class="row"><input id="msg" type="text" maxlength="96" placeholder="Type something" aria-label="Message"><button id="send" type="button">Show</button></div></section>
<section><h2>On-board LED</h2><div class="row"><input id="led" type="color" aria-label="LED colour" value="#000000"><button id="ledoff" type="button">Off</button></div><p class="muted">Black gives the LED back to showing the Wi-Fi state.</p></section>
<section><h2>Networks</h2><table id="nets"></table></section>
<section><h2>Live events</h2><ul id="feed"></ul></section>
<section><h2>Admin</h2><p class="muted">Needs the token printed on the console (command: token). Kept in this browser only.</p>
<div class="row"><input id="tok" type="password" placeholder="Token" aria-label="Admin token"><button id="reboot" type="button">Reboot</button><button id="forget" type="button">Forget Wi-Fi</button></div></section>
</main><script>
const $=id=>document.getElementById(id);let S=null;const esc=t=>String(t).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tok=()=>{const t=$('tok').value.trim();try{localStorage.setItem('tok',t)}catch(e){}return t};
try{$('tok').value=localStorage.getItem('tok')||''}catch(e){}
async function api(method,path,body,admin){const h={};if(body)h['Content-Type']='application/json';if(admin)h.Authorization='Bearer '+tok();
 const r=await fetch(path,{method,headers:h,body:body?JSON.stringify(body):undefined});const j=await r.json().catch(()=>({}));if(!r.ok)note('⚠ '+(j.error||r.status));return r.ok}
function note(t){const li=document.createElement('li');li.textContent=new Date().toLocaleTimeString()+'  '+t;$('feed').prepend(li)}
function status(s){S=s;const w=s.wifi;$('link').style.background=w.state==='connected'?'var(--ok)':w.state==='connecting'?'var(--warn)':w.state==='failed'?'var(--bad)':'var(--line)';
 $('sub').textContent=w.state==='connected'?w.ssid+' · '+w.rssi+' dBm':w.state;$('setup').hidden=w.state==='connected';
 const kv={'IP':w.ip||'—','Name':w.hostname,'Channel':w.channel||'—','Chip':(s.tempC??0).toFixed(1)+' °C','Uptime':Math.floor(s.uptimeS/60)+' min','Free RAM':Math.round(s.heapFree/1024)+' KB','Clients':s.clients};
 $('kv').innerHTML=Object.entries(kv).map(([k,v])=>`<dt>${k}</dt><dd>${esc(v)}</dd>`).join('');
 const sc=s.screen;$('views').innerHTML=sc.views.map(v=>`<button type="button" class="${v===sc.view?'on':''}" data-v="${v}">${v}</button>`).join('');
 if(document.activeElement!==$('bl'))$('bl').value=sc.backlight;$('auto').className=sc.autoScan?'on':'';}
function networks(n){$('nets').innerHTML=n.networks.slice(0,12).map(x=>`<tr><td>${x.ssid?esc(x.ssid):'(hidden)'}</td><td class="n">${x.rssi} dBm</td><td class="n">ch ${x.channel}</td><td>${esc(x.auth)}</td></tr>`).join('');
 const sel=$('ssid'),cur=sel.value;sel.innerHTML=n.networks.filter(x=>x.ssid).map(x=>`<option value="${esc(x.ssid)}">${esc(x.ssid)}</option>`).join('');if(cur)sel.value=cur}
function connect(){const ws=new WebSocket(`ws://${location.host}/ws`);ws.onmessage=e=>{const m=JSON.parse(e.data);
 if(m.type==='status')status(m.data);else if(m.type==='networks')networks(m.data);else if(m.type==='event')note(m.data.source+': '+m.data.gesture)};
 ws.onclose=()=>{$('link').style.background='var(--line)';$('sub').textContent='reconnecting…';setTimeout(connect,2000)}}
$('views').onclick=e=>{const v=e.target.dataset.v;if(v)api('PUT','/api/view',{view:v})};
$('bl').onchange=e=>api('PUT','/api/settings',{backlight:+e.target.value});
$('auto').onclick=()=>api('PUT','/api/settings',{autoScan:!(S&&S.screen.autoScan)});
$('scan').onclick=$('rescan').onclick=()=>api('POST','/api/scan');
$('send').onclick=()=>api('PUT','/api/message',{text:$('msg').value});
$('led').oninput=e=>{const h=e.target.value;api('PUT','/api/led',{r:parseInt(h.slice(1,3),16),g:parseInt(h.slice(3,5),16),b:parseInt(h.slice(5,7),16)})};
$('ledoff').onclick=()=>{$('led').value='#000000';api('PUT','/api/led',{r:0,g:0,b:0})};
$('save').onclick=async()=>{if(await api('PUT','/api/wifi',{ssid:$('ssid').value,password:$('pass').value},true))note('Saved. The board is joining…')};
$('reboot').onclick=()=>api('POST','/api/reboot',null,true);$('forget').onclick=()=>api('DELETE','/api/wifi',null,true);
fetch('/api/networks').then(r=>r.ok&&r.json()).then(n=>n&&networks(n));connect();
</script></body></html>"""

print("wifi_w2_phone: MAC {}. Admin token: {} (console: token)".format(
    kit.mac(connection.wlan.config("mac")), token.value))
show_help()
if connection.state != kit.CONNECTING:
    lab.scan_now("start-up")
try:
    asyncio.run(main())
finally:
    portal.stop()
    led.off()
    asyncio.new_event_loop()  # a clean loop for whatever runs next in this REPL session
    print("Stopped. The web server is closed; the saved network, token and relay address stay in NVS.")
