"""wifi_kit: shared helpers for the Pulsar C5 Wi-Fi labs W1, W2 and W3 in MicroPython.

The MicroPython port of esp-labs' kit/lib/wifi-kit. Save it on the board as
wifi_kit.py before running a wifi_w*.py example. The Pulsar C5 has no screen, so
the labs print to the serial console and use the on-board parts:

    BOOT button  GPIO28   click, double-click and long press
    WS2812 LED   GPIO0    the radio's state (blue: scanning, amber: joining, green: joined)

The network you join is kept in NVS, a small key-value area in the chip's flash, under the
namespace "mpwifi". It survives power-off and re-flashing MicroPython, and it is never in a
script, in git or in the console. `forget` erases it. NVS is not encrypted: fine for a bench.
"""

import esp32
import network
import select
import socket
import sys
import time
from machine import Pin
from micropython import const
from neopixel import NeoPixel
from time import sleep_ms, ticks_diff, ticks_ms

VERSION = 3  # the labs check it: an older wifi_kit.py on the board lacks what they use

PULSAR_BOOT_PIN = 28
PULSAR_RGB_PIN = 0  # Some UNIT illustrations label it GPIO8: try 8 if the LED stays dark.
HOSTNAME = "bench"  # the board answers to bench.local
NVS_NAMESPACE = "mpwifi"


# ---- Plain-language helpers -------------------------------------------------------------------

# ESP-IDF's wifi_auth_mode_t, the fifth item of every scan result.
AUTH_NAMES = ("open", "WEP", "WPA", "WPA2", "WPA/WPA2", "WPA2-Ent", "WPA3", "WPA2/WPA3", "WAPI",
              "OWE", "WPA3-Ent", "WPA3-SAE", "WPA3-SAE", "DPP", "WPA3-Ent", "WPA2/3-Ent")
AUTH_OPEN = const(0)


def auth_name(auth):
    return AUTH_NAMES[auth] if 0 <= auth < len(AUTH_NAMES) else "other"


def band(channel):
    """The C5 has two radios' worth of channels: 1-14 are 2.4 GHz, 36 and up are 5 GHz."""
    return "5 GHz" if channel > 14 else "2.4 GHz"


def signal_bars(rssi):
    """1-4 bars, the same rough scale phones use."""
    if rssi >= -55:
        return 4
    if rssi >= -67:
        return 3
    if rssi >= -78:
        return 2
    return 1


def bars_text(rssi):
    bars = signal_bars(rssi)
    return "█" * bars + "·" * (4 - bars)


SPARKS = "▁▂▃▄▅▆▇█"


def sparkline(values, low=None, high=None):
    """One character per value, from ▁ (lowest) to █ (highest)."""
    if not values:
        return ""
    low = min(values) if low is None else low
    high = max(values) if high is None else high
    span = max(high - low, 1)
    return "".join(SPARKS[min(len(SPARKS) - 1, max(0, int((v - low) * (len(SPARKS) - 1) / span + 0.5)))]
                   for v in values)


def mac(address):
    return ":".join("{:02X}".format(b) for b in bytes(address))


# The common disconnect reasons (ESP-IDF wifi_err_reason_t, as wlan.status() reports them).
REASONS = {
    0: "none yet",
    2: "wrong password?",
    8: "we left on purpose",
    15: "wrong password?",
    200: "lost the router's beacons (weak signal)",
    201: "network not found (out of range, or a typo in the name)",
    202: "wrong password?",
    203: "the router refused us (MAC filter, or full)",
    204: "wrong password? (the handshake timed out)",
}


def reason_hint(code):
    return REASONS.get(code, "reason {}".format(code))


def chip_temperature():
    """The chip's own sensor in degrees Celsius, or None when this firmware lacks it."""
    try:
        return float(esp32.mcu_temperature())
    except (AttributeError, OSError):
        return None


def masked(secret):
    """'...wxyz (41 characters)', never the whole secret."""
    if not secret:
        return "not set"
    return "...{} ({} characters)".format(secret[-4:], len(secret))


# ---- Settings in NVS --------------------------------------------------------------------------

class Settings:
    """Short text values in one NVS namespace (at most 15 characters per name)."""

    SIZE = 512

    def __init__(self, namespace=NVS_NAMESPACE):
        self._nvs = esp32.NVS(namespace)

    def get(self, key, default=""):
        buffer = bytearray(self.SIZE)
        try:
            length = self._nvs.get_blob(key, buffer)
        except OSError:  # not set yet
            return default
        return bytes(buffer[:length]).decode()

    def set(self, key, value):
        """Save a value; an empty one removes the key."""
        if value:
            self._nvs.set_blob(key, value.encode())
        else:
            try:
                self._nvs.erase_key(key)
            except OSError:
                pass
        self._nvs.commit()


# ---- Scanning ---------------------------------------------------------------------------------

class Scanner:
    """wlan.scan() hops through every channel, 2.4 and 5 GHz, and collects the routers' answers.

    It blocks for two to five seconds: the console and BOOT wait until it is done.
    """

    def __init__(self, wlan):
        self.wlan = wlan
        self.networks = []
        self.scans = 0
        self.last_ms = 0
        self.finished_at = ticks_ms()

    def scan(self):
        """Returns True with fresh results, False when the radio was busy (e.g. still joining)."""
        started = ticks_ms()
        try:
            raw = self.wlan.scan()
        except OSError as error:
            print("! scan failed ({}), will retry".format(error))
            self.finished_at = ticks_ms()
            return False
        self.last_ms = ticks_diff(ticks_ms(), started)
        self.finished_at = ticks_ms()
        self.scans += 1
        networks = []
        for ssid, bssid, channel, rssi, auth, hidden in raw:
            networks.append({
                "ssid": "" if hidden else ssid.decode("utf-8", "replace"),
                "bssid": mac(bssid), "channel": channel, "rssi": rssi, "auth": auth,
            })
        networks.sort(key=lambda n: -n["rssi"])
        self.networks = networks
        return True

    def since(self):
        return ticks_diff(ticks_ms(), self.finished_at)


# ---- Joining, remembering, staying connected --------------------------------------------------

NO_CREDENTIALS = "not joined"
CONNECTING = "joining"
CONNECTED = "joined"
FAILED = "failed"


class Connection:
    """Joins the saved network and keeps it: time-outs, retries, internet time, signal history.

    Call tick() from the main loop. It returns "connected", "dropped" or "failed" once when that
    happens, otherwise None.
    """

    JOIN_TIMEOUT_MS = 20_000
    RETRY_MS = 30_000
    ATTEMPTS = 3  # the driver's own retries per join; after them wlan.status() tells why it failed
    HISTORY = 60  # seconds of signal history

    def __init__(self, settings=None):
        network.hostname(HOSTNAME)  # before active(): mDNS answers to bench.local
        self.wlan = network.WLAN(network.STA_IF)
        self.wlan.active(True)
        self.settings = settings or Settings()
        self.ssid = self.settings.get("ssid")
        self._password = self.settings.get("pass")
        self.utc_offset = int(self.settings.get("tz", "0"))  # minutes east of UTC
        self.state = NO_CREDENTIALS
        self.reason = 0
        self.history = []
        self._started_at = self._failed_at = self._sampled_at = self._ntp_at = self._told_at = ticks_ms()
        self._synced = False
        if not self.ssid:
            print("No saved network yet. Scan, then: join <#>")
        elif self.wlan.isconnected() and self.wlan.config("ssid") == self.ssid:
            self.state = CONNECTING  # still joined from the last script: tick() takes it from here
        else:
            print("Saved network found: '{}'".format(self.ssid))
            self.connect()

    # Saved network ------------------------------------------------------------------------

    def save(self, ssid, password):
        self.settings.set("ssid", ssid)
        self.settings.set("pass", password)
        self.ssid, self._password = ssid, password
        print("Saved '{}' (password: {} characters, not shown)".format(ssid, len(password)))
        self.connect()

    def forget(self):
        self.settings.set("ssid", "")
        self.settings.set("pass", "")
        self._disconnect()
        self.ssid = self._password = ""
        self.state = NO_CREDENTIALS
        print("Saved network erased from NVS")

    def set_utc_offset(self, minutes):
        self.utc_offset = minutes
        self.settings.set("tz", str(minutes))
        print("Time zone: UTC{}".format(format_offset(minutes)))

    # Radio --------------------------------------------------------------------------------

    def connect(self):
        if not self.ssid:
            return
        self._disconnect()
        self.reason = 0
        self._started_at = self._told_at = ticks_ms()
        try:
            # Without a limit the driver retries for ever and wlan.status() only ever says
            # "connecting"; after a few attempts it reports the real reason instead.
            self.wlan.config(reconnects=self.ATTEMPTS)
        except (OSError, ValueError):
            pass
        try:
            if self._password:
                self.wlan.connect(self.ssid, self._password)
            else:
                self.wlan.connect(self.ssid)
        except OSError as error:
            # e.g. "Wifi Internal State Error" when the radio is still busy: retry later
            print("! could not start joining: {}".format(error))
            self.state = FAILED
            self._failed_at = ticks_ms()
            return
        self.state = CONNECTING
        print("Joining '{}' ({} password characters)...".format(self.ssid, len(self._password)))

    def _disconnect(self):
        try:
            self.wlan.disconnect()
        except OSError:
            pass

    def tick(self):
        now = ticks_ms()
        joined = self.wlan.isconnected()
        if joined and self.state != CONNECTED:
            self.state = CONNECTED
            self.history = []
            # Power save lets the radio doze between router beacons and miss requests.
            # Fine for a sensor that only sends; a server must stay awake to be reachable.
            self.wlan.config(pm=self.wlan.PM_NONE)
            self._sync_time()
            return "connected"
        if not joined and self.state == CONNECTED:
            self.reason = self.wlan.status()
            self.state = CONNECTING
            self._started_at = now
            return "dropped"
        if self.state == CONNECTING:
            status = self.wlan.status()
            if status < 1000:  # 1000+ are "idle", "connecting", "got IP"; below are reasons
                self.reason = status
            if ticks_diff(now, self._told_at) >= 3000:
                self._told_at = now
                print("  ...{} s, {}".format(self.joining_for(),
                                            "still trying" if status >= 1000 else reason_hint(status)))
            # The driver reports a reason only once its own attempts are used up. Skip the first
            # second: a leftover reason from the previous connection can still be there.
            gave_up = status < 1000 and status != 0 and ticks_diff(now, self._started_at) > 1000
            if gave_up or ticks_diff(now, self._started_at) > self.JOIN_TIMEOUT_MS:
                self.state = FAILED
                self._failed_at = now
                self._disconnect()
                return "failed"
        if self.state == FAILED and ticks_diff(now, self._failed_at) > self.RETRY_MS:
            self.connect()
        if self.state == CONNECTED:
            if ticks_diff(now, self._sampled_at) >= 1000:
                self._sampled_at = now
                self.history.append(self.rssi())
                if len(self.history) > self.HISTORY:
                    self.history.pop(0)
            if not self._synced and ticks_diff(now, self._ntp_at) > 30_000:
                self._sync_time()
        return None

    def _sync_time(self):
        """Internet time (NTP): one small UDP request, then the chip's clock keeps counting."""
        self._ntp_at = ticks_ms()
        try:
            import ntptime
            ntptime.host = "pool.ntp.org"
            ntptime.settime()
            self._synced = True
        except (ImportError, OSError, OverflowError):
            self._synced = False

    # What the status view shows ----------------------------------------------------------

    def joined(self):
        return self.state == CONNECTED

    def ip(self):
        return self.wlan.ifconfig()[0]

    def gateway(self):
        return self.wlan.ifconfig()[2]

    def channel(self):
        return self.wlan.config("channel")

    def rssi(self):
        try:
            return self.wlan.status("rssi")
        except OSError:
            return -127

    def joining_for(self):
        return ticks_diff(ticks_ms(), self._started_at) // 1000

    def time_synced(self):
        return self._synced and time.localtime()[0] >= 2025

    def local_time(self):
        """'14:05:09' in your time zone, or None before the first NTP answer."""
        if not self.time_synced():
            return None
        t = time.localtime(time.time() + self.utc_offset * 60)
        return "{:02d}:{:02d}:{:02d}".format(t[3], t[4], t[5])

    def describe(self):
        if self.state == NO_CREDENTIALS:
            return "Not joined. Scan, then: join <#>"
        if self.state == CONNECTING:
            return "Joining '{}' ({} s)...".format(self.ssid, self.joining_for())
        if self.state == FAILED:
            return "Could not join '{}': {}. Retrying every {} s.".format(
                self.ssid, reason_hint(self.reason), self.RETRY_MS // 1000)
        channel = self.channel()
        return "Joined '{}' | IP {} | {}.local | ch {} ({}) | {} dBm | time {}".format(
            self.ssid, self.ip(), HOSTNAME, channel, band(channel), self.rssi(),
            self.local_time() or "not synced yet")


def format_offset(minutes):
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return "{}{}:{:02d}".format(sign, minutes // 60, minutes % 60)


def parse_offset(text):
    """'+1', '-6', '5:30', '-3:30' -> minutes east of UTC, or None."""
    text = text.strip().upper().replace("UTC", "")
    sign = -1 if text.startswith("-") else 1
    hours, _, mins = text.lstrip("+-").partition(":")
    try:
        minutes = int(hours) * 60 + (int(mins) if mins else 0)
    except ValueError:
        return None
    return sign * minutes if minutes <= 14 * 60 else None


# ---- Reach: is anyone there? ------------------------------------------------------------------

def reach(host, port=80, timeout_ms=2000):
    """Time a TCP connection: MicroPython has no ICMP ping, but a host that answers at all
    (accepts, or refuses with a reset) is reachable. Returns (ms, how) or (None, why)."""
    try:
        address = socket.getaddrinfo(host, port)[0][-1]
    except OSError:
        return None, "unknown host"
    sock = socket.socket()
    sock.settimeout(timeout_ms / 1000)
    started = ticks_ms()
    try:
        sock.connect(address)
        how = "port {} open".format(port)
    except OSError as error:
        code = error.args[0] if error.args else 0
        if code in (104, 111):  # ECONNRESET, ECONNREFUSED: it answered "no"
            how = "port {} closed, but it answered".format(port)
        else:
            return None, "no answer"
    finally:
        sock.close()
    return ticks_diff(ticks_ms(), started), how


# ---- The serial console, without blocking the radio -------------------------------------------

class Console:
    """Collects typed characters and returns one command line at a time.

    Secrets are never echoed: set secret_next for a password line, and name command prefixes
    (e.g. "ai key ") whose argument is masked.
    """

    def __init__(self, secret_prefixes=(), mask=None):
        self._poll = select.poll()
        self._poll.register(sys.stdin, select.POLLIN)
        self._buffer = ""
        self._secret_prefixes = secret_prefixes
        self._mask = mask  # optional: text -> what to echo instead
        self._after_cr = False
        self.secret_next = False

    def line(self):
        while self._poll.poll(0):
            char = sys.stdin.read(1)
            after_cr, self._after_cr = self._after_cr, char == "\r"
            if char == "\n" and after_cr:
                continue  # the second half of a CR LF ending, not an empty line
            if char in ("\r", "\n"):
                if self.secret_next:
                    # A password is taken exactly as typed: no trimming, no lower-casing.
                    text, self._buffer, self.secret_next = self._buffer, "", False
                    print("> (password, {} characters, not shown)".format(len(text)))
                    return text
                if self._buffer.strip():
                    text, self._buffer = self._buffer.strip(), ""
                    print(">", self._echo(text))
                    return text
                self._buffer = ""
            elif char in ("\x08", "\x7f"):
                self._buffer = self._buffer[:-1]
            elif char and len(self._buffer) < 200:
                self._buffer += char
        return None

    def _echo(self, text):
        if self._mask:
            text = self._mask(text)
        lower = text.lower()
        for prefix in self._secret_prefixes:
            if lower.startswith(prefix) and len(text) > len(prefix):
                return prefix + masked(text[len(prefix):].strip())
        return text


def split_command(text):
    """'City Mexico City' -> ('city', 'Mexico City')."""
    head, _, rest = text.partition(" ")
    return head.lower(), rest.strip()


# ---- The BOOT button: click, double-click, long press ------------------------------------------

CLICK = const(1)
DOUBLE = const(2)
LONG = const(3)


class Button:
    """Polled from the main loop: returns CLICK, DOUBLE, LONG or 0."""

    DEBOUNCE_MS = 25
    DOUBLE_MS = 300
    LONG_MS = 700

    def __init__(self, pin=PULSAR_BOOT_PIN):
        self._pin = Pin(pin, Pin.IN, Pin.PULL_UP)
        self._down = False
        self._changed_at = ticks_ms()
        self._pressed_at = 0
        self._released_at = 0
        self._clicks = 0
        self._long_sent = False

    def poll(self):
        now = ticks_ms()
        down = self._pin.value() == 0
        if down != self._down and ticks_diff(now, self._changed_at) >= self.DEBOUNCE_MS:
            self._down, self._changed_at = down, now
            if down:
                self._pressed_at, self._long_sent = now, False
            elif not self._long_sent:
                self._clicks += 1
                self._released_at = now
                if self._clicks == 2:
                    self._clicks = 0
                    return DOUBLE
        if self._down and not self._long_sent and ticks_diff(now, self._pressed_at) >= self.LONG_MS:
            self._long_sent, self._clicks = True, 0
            return LONG
        if self._clicks == 1 and not self._down and ticks_diff(now, self._released_at) >= self.DOUBLE_MS:
            self._clicks = 0
            return CLICK
        return 0


# ---- The scan table, the views and joining from the console (W1 and W2) -----------------------

def hide_join_password(text):
    """'join 3 secret' echoes as 'join 3 (password, 6 characters)'."""
    head, _, rest = text.partition(" ")
    number, _, password = rest.partition(" ")
    if head.lower() == "join" and number.isdigit() and password:
        return "join {} (password, {} characters)".format(number, len(password))
    return text


class ScanLab:
    """What W1 and W2 share: scanning, the networks/channels/status/signal views as console
    commands, and joining a network from the console.

    join <#> counts in the table you last saw (`listed`): automatic scans re-sort the networks by
    signal, so they only say what is new and never change what a number means.
    """

    AUTO_SCAN_MS = 10_000         # pause between automatic scans while not joined
    AUTO_SCAN_JOINED_MS = 60_000  # every scan takes the radio off the router's channel: less often

    def __init__(self, connection, console, led):
        self.connection = connection
        self.scanner = Scanner(connection.wlan)
        self.console = console
        self.led = led
        self.listed = []
        self.pending_ssid = None  # set by join: the next console line is the password
        self.auto_scan = True
        self.on_scan = None  # called after every scan with fresh results (W2 tells the web)
        self.status_lines = None  # optional: a function returning extra lines for the status view

    # Views ------------------------------------------------------------------------------------

    def show_networks(self):
        nets = self.listed = self.scanner.networks
        print("\n--- scan {}: {} networks in {} ms ---".format(self.scanner.scans, len(nets), self.scanner.last_ms))
        print(" #   dBm  bars   ch  band     security    bssid              ssid")
        for i, n in enumerate(nets, 1):
            print("{:2d}  {:4d}  {}  {:3d}  {:7s}  {:10s}  {}  {}".format(
                i, n["rssi"], bars_text(n["rssi"]), n["channel"], band(n["channel"]),
                auth_name(n["auth"]), n["bssid"], n["ssid"] or "(hidden)"))
        print("To join one: join <#>")

    def show_channels(self):
        """How crowded each channel is. 2.4 GHz channels overlap: only 1, 6 and 11 stay clear of
        each other. 5 GHz has many more channels and they do not overlap."""
        by_channel = {}
        for n in self.scanner.networks:
            by_channel.setdefault(n["channel"], []).append(n["rssi"])
        ours = self.connection.channel() if self.connection.joined() else None
        for title, channels in (("2.4 GHz", range(1, 14)), ("5 GHz", sorted(c for c in by_channel if c > 14))):
            print("\n{}  (networks, strongest)".format(title))
            if not channels:
                print("  none heard")
            for channel in channels:
                heard = by_channel.get(channel, [])
                mark = " <- you" if channel == ours else ""
                strongest = " {} dBm".format(max(heard)) if heard else ""
                print("  ch {:3d} {:12s} {}{}{}".format(channel, "█" * min(len(heard), 12), len(heard) or "",
                                                       strongest, mark))

    def show_status(self):
        connection = self.connection
        print(connection.describe())
        if connection.joined():
            print("Gateway {} | MAC {} | time zone UTC{}".format(
                connection.gateway(), mac(connection.wlan.config("mac")), format_offset(connection.utc_offset)))
            print("Reachable as {}.local: try ping {}.local from your computer".format(HOSTNAME, HOSTNAME))
        if self.status_lines:
            for line in self.status_lines():
                print(line)

    def show_signal(self):
        history = self.connection.history
        if not history:
            print("No signal history yet: it fills once a second while joined")
            return
        print("Signal, last {} s: {}  now {} dBm, min {}, max {}, {} bars".format(
            len(history), sparkline(history, -90, -30), history[-1], min(history), max(history),
            signal_bars(history[-1])))

    # Scanning ---------------------------------------------------------------------------------

    def scan_now(self, why, quiet=False):
        """quiet: an automatic scan only says what is new, so the table you read stays put."""
        if self.connection.state == CONNECTING:
            print("Not now: the radio must stay on the router's channel while joining")
            return
        if not quiet:
            print("scanning ({})...".format(why))
        self.led.wifi(self.connection.state, scanning=True)
        if not self.scanner.scan():
            return
        if self.on_scan:
            self.on_scan()
        if not quiet or not self.listed:
            self.show_networks()
            return
        before = {n["ssid"] for n in self.listed if n["ssid"]}
        new = sorted({n["ssid"] for n in self.scanner.networks if n["ssid"]} - before)
        if new:
            print("scan {}: new {} (type networks for a fresh table)".format(self.scanner.scans, ", ".join(new)))

    def set_auto(self, on):
        self.auto_scan = on
        print("auto-scan", "on" if on else "off")

    def auto_scan_due(self, blocked=False):
        """True when an automatic scan should run now. blocked: the lab has its own reason to wait."""
        if not self.auto_scan or blocked or self.pending_ssid is not None or self.connection.state == CONNECTING:
            return False
        interval = self.AUTO_SCAN_JOINED_MS if self.connection.joined() else self.AUTO_SCAN_MS
        return interval > 0 and self.scanner.since() >= interval

    # Joining ----------------------------------------------------------------------------------

    def start_join(self, argument):
        """join <#> | join <#> <password> | join <network name>"""
        number, _, password = argument.partition(" ")
        if not number.isdigit():
            if not argument:
                print("join <#> from the table, join <#> <password>, or join <network name>")
                return
            # A name: for a hidden network, or one the table doesn't show. The password comes next.
            self._ask_password(argument, "Password for '{}' on the next line (empty line for an open network).")
            return
        if not 1 <= int(number) <= len(self.listed):
            print("join needs a number from the table above (1-{}); type networks to see it".format(len(self.listed)))
            return
        chosen = self.listed[int(number) - 1]
        if not chosen["ssid"]:
            print("That network is hidden: its name is unknown. Use join <network name>")
            return
        print("#{}: '{}', {}, channel {} ({}), {} dBm".format(
            number, chosen["ssid"], auth_name(chosen["auth"]), chosen["channel"], band(chosen["channel"]),
            chosen["rssi"]))
        if chosen["auth"] == AUTH_OPEN:
            print("It is open, joining without a password")
            self.connection.save(chosen["ssid"], "")
        elif password:
            self.join_with(chosen["ssid"], password)
        else:
            self._ask_password(chosen["ssid"], "Password on the next line. It is stored on the chip only;\n"
                               "the web terminal still lists what you send, so don't share that screen.")

    def _ask_password(self, ssid, prompt):
        self.pending_ssid = ssid
        self.console.secret_next = True
        print(prompt.format(ssid))

    def take_password(self, text):
        """True when this console line was the password a join asked for."""
        if self.pending_ssid is None:
            return False
        ssid, self.pending_ssid = self.pending_ssid, None
        self.join_with(ssid, text)
        return True

    def join_with(self, ssid, password):
        # WPA2/WPA3 passwords are 8 to 63 characters: anything else can never work.
        if password and not 8 <= len(password) <= 63:
            print("! {} characters: Wi-Fi passwords have 8 to 63. Type join again.".format(len(password)))
            return False
        self.connection.save(ssid, password)
        return True

    # Commands and events ----------------------------------------------------------------------

    def reach(self, argument):
        if not self.connection.joined():
            print("reach needs a network: join one first")
            return
        parts = argument.split()
        host = parts[0] if parts else self.connection.gateway()
        port = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 80
        for attempt in range(1, 5):
            ms, how = reach(host, port)
            print("  {} {}: {}".format(host, attempt, how if ms is None else "{} ms, {}".format(ms, how)))
            sleep_ms(300)

    def handle(self, command, argument):
        """The commands W1 and W2 share. Returns False for a command it doesn't know."""
        connection = self.connection
        if command == "scan":
            self.scan_now("console")
        elif command == "auto" and argument in ("on", "off"):
            self.set_auto(argument == "on")
        elif command == "join":
            self.start_join(argument)
        elif command == "forget":
            connection.forget()
        elif command == "reconnect":
            connection.connect()
        elif command == "tz":
            minutes = parse_offset(argument)
            if minutes is None:
                print("tz wants hours from UTC: tz -6, tz +1, tz 5:30")
            else:
                connection.set_utc_offset(minutes)
        elif command in ("reach", "ping"):
            self.reach(argument)
        else:
            return False
        return True

    def report(self, event):
        """Prints what connection.tick() returned. True when it was a fresh connection."""
        connection = self.connection
        if event == "connected":
            print("+ joined")
            return True
        if event == "dropped":
            print("- disconnected: {}".format(reason_hint(connection.reason)))
        elif event == "failed":
            print("! could not join '{}': {}. Retrying in {} s; check it with networks, or forget.".format(
                connection.ssid, reason_hint(connection.reason) if connection.reason else "no answer in time",
                connection.RETRY_MS // 1000))
        return False


def forget_saved(settings=None):
    """For a script's FORGET_SAVED switch: erase the saved network before joining it."""
    settings = settings or Settings()
    settings.set("ssid", "")
    settings.set("pass", "")
    print("FORGET_SAVED: saved network erased. Set it back to False before the next run.")


# ---- The WS2812 as a status light ---------------------------------------------------------------

class StatusLed:
    """Blue while scanning, amber blink while joining, green when joined, red after a failure."""

    LEVEL = 40  # 0-255: the LED is very bright at full power

    def __init__(self, pin=PULSAR_RGB_PIN):
        self._pixel = NeoPixel(Pin(pin, Pin.OUT), 1)
        self._shown = None
        self.claimed = None  # a colour chosen on purpose (W3's oracle): wins over the radio state

    def show(self, rgb):
        rgb = tuple(c * self.LEVEL // 255 for c in rgb)
        if rgb != self._shown:
            self._shown = rgb
            self._pixel[0] = rgb
            self._pixel.write()

    def wifi(self, state, scanning=False):
        if self.claimed is not None:
            return self.show(self.claimed)
        blink = (ticks_ms() // 400) % 2 == 0
        if scanning:
            self.show((0, 0, 255))
        elif state == CONNECTED:
            self.show((0, 255, 0))
        elif state == CONNECTING:
            self.show((255, 140, 0) if blink else (40, 20, 0))
        elif state == FAILED:
            self.show((255, 0, 0))
        else:
            self.show((0, 0, 0))

    def off(self):
        self.claimed = None
        self.show((0, 0, 0))


def pause(ms=20):
    sleep_ms(ms)
