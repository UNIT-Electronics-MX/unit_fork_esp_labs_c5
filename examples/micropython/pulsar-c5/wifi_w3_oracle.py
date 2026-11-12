"""W3 Internet & AI: weather from the internet, a poet from the cloud, on the Pulsar C5.

Needs wifi_kit.py saved on the board and a network saved by W1 (wifi_w1_scan.py).
Type help in the serial console.

The board becomes an internet client. It fetches the weather for your city over HTTPS as JSON
(Open-Meteo: no account, no key) and prints it. Then the Bench Oracle: the board sends an AI
model what it knows about this moment (place, time, weather, its own temperature) and gets back
a haiku or an answer, plus an LED colour and a scene, as structured JSON. The request goes
through a LiteLLM gateway on your computer (esp-labs/apps/ai-gateway), so any provider and
model works and the provider keys never touch the chip.

Views: now | forecast | week | oracle | status
BOOT: click = next view, double-click = refresh the weather, long press = ask the oracle
"""

import json
import time

import wifi_kit as kit

if getattr(kit, "VERSION", 1) < 3:
    raise SystemExit("wifi_kit.py on the board is too old: press Install wifi_kit.py, then run"
                     " import machine; machine.soft_reset() and start this script again.")

try:
    import requests
except ImportError:  # older MicroPython firmware names it urequests
    import urequests as requests

WEATHER_EVERY_MS = 15 * 60_000  # Open-Meteo updates its model every 15 min
RETRY_MS = 60_000               # after a failed fetch
ORACLE_GAP_MS = 10_000          # at most one AI request per 10 s
DEFAULT_MODEL = "oracle"        # a model_name in ai-gateway/config.yaml
VIEWS = ("now", "forecast", "week", "oracle", "status")
SCENES = ("sun", "clouds", "rain", "snow", "storm", "fog", "night")

connection = kit.Connection()
place_settings = kit.Settings("w3")
ai_settings = kit.Settings("ai")
console = kit.Console(secret_prefixes=("ai key ",))
button = kit.Button()
led = kit.StatusLed()

view = 0
place = None        # {"name", "lat", "lon"}
weather = None      # what the views and the oracle need, see fetch_weather()
answer = None       # the oracle's last answer
weather_tried_at = None
oracle_at = None


# ---- Weather codes ----------------------------------------------------------------------------

def describe(code):
    """WMO weather interpretation codes, as Open-Meteo reports them."""
    if code == 0:
        return "clear sky"
    if code in (1, 2, 3):
        return ("mainly clear", "partly cloudy", "overcast")[code - 1]
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 57:
        return "drizzle"
    if code in (61, 63, 65):
        return {61: "light rain", 63: "rain", 65: "heavy rain"}[code]
    if code in (66, 67):
        return "freezing rain"
    if 71 <= code <= 77:
        return "snow"
    if 80 <= code <= 82:
        return "rain showers"
    if code in (85, 86):
        return "snow showers"
    if code == 95:
        return "thunderstorm"
    if code in (96, 99):
        return "thunderstorm, hail"
    return "unknown"


def scene_for(code, day):
    if code >= 95:
        return "storm"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 51:
        return "rain"
    if code in (45, 48):
        return "fog"
    if code >= 2:
        return "clouds"
    return "sun" if day else "night"


# ---- HTTPS with requests ----------------------------------------------------------------------

def url_encode(text):
    safe = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    return "".join(chr(b) if b in safe else "%{:02X}".format(b) for b in text.encode())


def get_json(url):
    """GET a JSON answer. Returns (data, ms, bytes) or raises OSError with a readable message."""
    started = time.ticks_ms()
    response = requests.get(url, timeout=15)
    try:
        body = response.content
        if response.status_code != 200:
            raise OSError("HTTP {}".format(response.status_code))
    finally:
        response.close()
    return json.loads(body), time.ticks_diff(time.ticks_ms(), started), len(body)


def find_city(name):
    global place
    if not connection.joined():
        print("Not on Wi-Fi yet")
        return
    try:
        data, ms, size = get_json("https://geocoding-api.open-meteo.com/v1/search?count=1&language=en"
                                  "&format=json&name=" + url_encode(name))
    except (OSError, ValueError) as error:
        print("! city lookup failed: {}".format(error))
        return
    hits = data.get("results") or []
    if not hits:
        print("! no place called '{}'".format(name))
        return
    hit = hits[0]
    place = {"name": "{}, {}".format(hit.get("name", "?"), hit.get("country", "")),
             "lat": hit["latitude"], "lon": hit["longitude"]}
    place_settings.set("name", place["name"])
    place_settings.set("lat", str(place["lat"]))
    place_settings.set("lon", str(place["lon"]))
    print("Found {} ({:.2f}, {:.2f}) in {} ms, {} bytes. Saved.".format(
        place["name"], place["lat"], place["lon"], ms, size))
    fetch_weather("new city")


def load_place():
    name = place_settings.get("name")
    if not name:
        return None
    return {"name": name, "lat": float(place_settings.get("lat", "0")), "lon": float(place_settings.get("lon", "0"))}


def fetch_weather(why):
    """Now, the next 24 hours and 7 days in one request. Only what the views need is kept."""
    global weather, weather_tried_at
    if place is None:
        print("No city yet: type city <name>")
        return
    if not connection.joined():
        print("Not on Wi-Fi yet")
        return
    weather_tried_at = time.ticks_ms()
    url = ("https://api.open-meteo.com/v1/forecast?latitude={:.4f}&longitude={:.4f}&timezone=auto"
           "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day"
           "&hourly=temperature_2m,precipitation_probability&forecast_hours=24"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min&forecast_days=7").format(
        place["lat"], place["lon"])
    print("weather for {} ({})...".format(place["name"], why))
    try:
        data, ms, size = get_json(url)
        now, hourly, daily = data["current"], data["hourly"], data["daily"]
        weather = {
            "temp": now["temperature_2m"], "feels": now["apparent_temperature"],
            "humidity": now["relative_humidity_2m"], "code": now["weather_code"],
            "wind": now["wind_speed_10m"], "day": now["is_day"] == 1,
            "hour_temp": hourly["temperature_2m"], "hour_rain": [r or 0 for r in hourly["precipitation_probability"]],
            "day_dates": daily["time"], "day_max": daily["temperature_2m_max"],
            "day_min": daily["temperature_2m_min"], "day_code": daily["weather_code"],
            "ms": ms, "bytes": size,
        }
        seconds = data.get("utc_offset_seconds")
        del data  # the parsed answer is the biggest thing in memory: drop it early
    except (OSError, ValueError, KeyError) as error:
        print("! weather failed: {}. Retrying in {} s.".format(error, RETRY_MS // 1000))
        return
    # Open-Meteo answers in your local time zone (timezone=auto) and says which one: use it.
    if seconds is not None and seconds // 60 != connection.utc_offset:
        connection.set_utc_offset(seconds // 60)
    print("< {} bytes in {} ms".format(size, ms))
    show_now()


# ---- Views ------------------------------------------------------------------------------------

def need_weather():
    if weather is None:
        print("No weather yet: " + ("type city <name>" if place is None else "type weather"))
        return False
    return True


def show_now():
    if not need_weather():
        return
    w = weather
    print("\n{}  {}".format(place["name"], connection.local_time() or ""))
    print("  {:.1f} °C, feels like {:.1f} °C, {}".format(w["temp"], w["feels"], describe(w["code"])))
    print("  humidity {} %, wind {:.0f} km/h, {}  [scene: {}]".format(
        w["humidity"], w["wind"], "daylight" if w["day"] else "night", scene_for(w["code"], w["day"])))


def show_forecast():
    if not need_weather():
        return
    temps, rain = weather["hour_temp"], weather["hour_rain"]
    print("\nNext {} hours: {:.0f} to {:.0f} °C".format(len(temps), min(temps), max(temps)))
    print("  temp  " + kit.sparkline(temps))
    print("  rain  " + kit.sparkline(rain, 0, 100) + "  (max {} %)".format(max(rain) if rain else 0))
    print("        " + "".join("{:<3d}".format(h) for h in range(0, len(temps), 3)) + " hours from now")


WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def weekday(date):
    """'2026-11-11' -> 'Wed'."""
    year, month, day = (int(part) for part in date.split("-"))
    return WEEKDAYS[time.localtime(time.mktime((year, month, day, 12, 0, 0, 0, 0)))[6]]


def show_week():
    if not need_weather():
        return
    print()
    for date, high, low, code in zip(weather["day_dates"], weather["day_max"], weather["day_min"], weather["day_code"]):
        print("  {} {}  {:5.1f} / {:5.1f} °C  {}".format(weekday(date), date[5:], low, high, describe(code)))


def show_oracle():
    if answer is None:
        print("No answer yet: type oracle, ask <question>, or hold BOOT")
        return
    print("\n=== {} ===".format(answer["title"]))
    for line in answer["lines"]:
        print("  " + line)
    print("LED {} | scene {} | {} | {}+{} tokens | {:.1f} s".format(
        answer["color"], answer["scene"], answer["model"], answer["tokens_in"], answer["tokens_out"],
        answer["ms"] / 1000))


def show_status():
    print(connection.describe())
    print("City: {}".format(place["name"] if place else "not set (city <name>)"))
    show_ai()


SHOW = (show_now, show_forecast, show_week, show_oracle, show_status)


def set_view(number):
    global view
    view = number % len(VIEWS)
    print("view {}: {}".format(view + 1, VIEWS[view]))
    SHOW[view]()


# ---- The Bench Oracle -------------------------------------------------------------------------

def ai_url():
    return ai_settings.get("url")


def ai_key():
    return ai_settings.get("key")


def ai_model():
    return ai_settings.get("model", DEFAULT_MODEL)


def show_ai():
    print("AI gateway: {} | key {} | model {}".format(ai_url() or "not set (ai url <url>)",
                                                      kit.masked(ai_key()), ai_model()))


def context():
    """What the oracle gets to know about this moment, as a small JSON text."""
    moment = {}
    if place:
        moment["place"] = place["name"]
    local = connection.local_time()
    if local:
        moment["local_time"] = local
    if weather:
        w = weather
        moment["weather"] = {
            "now": describe(w["code"]), "temp_c": w["temp"], "feels_like_c": w["feels"],
            "wind_kmh": round(w["wind"]), "humidity_pct": w["humidity"], "daylight": w["day"],
            "max_rain_chance_next_24h_pct": max(w["hour_rain"]) if w["hour_rain"] else 0,
            "today_min_c": round(w["day_min"][0]), "today_max_c": round(w["day_max"][0]),
        }
    bench = {"board": "UNIT Pulsar ESP32-C5: no screen, a serial console and one RGB LED",
             "uptime_min": time.ticks_ms() // 60_000, "wifi_signal_dbm": connection.rssi()}
    celsius = kit.chip_temperature()
    if celsius is not None:
        bench["chip_temp_c"] = celsius
    moment["bench"] = bench
    return json.dumps(moment)


# One function, and the model must call it: the reply is then always JSON in this shape.
SHOW_ON_BENCH = {
    "type": "function",
    "function": {
        "name": "show_on_bench",
        "description": "Print text on the bench's serial console and set its RGB LED.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "one or two words, at most 14 characters"},
                "lines": {"type": "array", "items": {"type": "string"},
                          "description": "1 to 4 lines, at most 40 characters each"},
                "color": {"type": "string", "description": "LED colour matching the mood, as #rrggbb"},
                "scene": {"type": "string", "enum": list(SCENES)},
            },
            "required": ["title", "lines", "color", "scene"],
        },
    },
}

SYSTEM = ("You are the Bench Oracle, living inside an ESP32-C5 on a maker's workbench with a serial "
          "console and an RGB LED. Be warm, witty and concrete; use the context (place, time, weather, "
          "the chip's own state) when it fits. Always answer by calling show_on_bench.")


def ask(question):
    global answer, oracle_at
    if not connection.joined():
        print("Not on Wi-Fi yet")
        return
    if not ai_url() or not ai_key():
        print("Set the gateway first: ai url http://<your computer's IP>:4000 and ai key <key>")
        return
    if oracle_at is not None and time.ticks_diff(time.ticks_ms(), oracle_at) < ORACLE_GAP_MS:
        print("One question per {} s, please: the gateway bills every request".format(ORACLE_GAP_MS // 1000))
        return
    oracle_at = time.ticks_ms()
    task = ("Write a haiku (3 lines) about this exact moment at the bench, weaving in the weather. "
            "Title: the mood in one or two words." if not question else
            "Question from the maker: " + question + "\nAnswer in at most 4 short lines. "
            "Title: a one or two word headline.")
    request = {
        "model": ai_model(), "max_tokens": 400,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": "Context: " + context() + "\n" + task}],
        "tools": [SHOW_ON_BENCH],
        "tool_choice": {"type": "function", "function": {"name": "show_on_bench"}},
    }
    print("asking {} ...".format(ai_model()))
    started = time.ticks_ms()
    try:
        response = requests.post(ai_url().rstrip("/") + "/v1/chat/completions", data=json.dumps(request),
                                 headers={"Authorization": "Bearer " + ai_key(),
                                          "Content-Type": "application/json"}, timeout=30)
        try:
            status, body = response.status_code, response.content
        finally:
            response.close()
        reply = json.loads(body)
    except (OSError, ValueError) as error:
        print("! oracle failed: {} (is the gateway running, and is the address right?)".format(error))
        return
    if status != 200:
        # LiteLLM explains errors as {"error": {"message": ...}}
        message = reply.get("error", {}).get("message", "") if isinstance(reply, dict) else ""
        print("! HTTP {}: {}".format(status, message[:200]))
        return
    try:
        # The function's arguments arrive as a JSON *string* inside the JSON answer: parse it again.
        arguments = reply["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        shown = json.loads(arguments)
    except (KeyError, IndexError, TypeError, ValueError):
        print("! no show_on_bench call in the reply (does this model support tools?)")
        return
    color = shown.get("color", "#40c0ff")
    try:
        rgb = int(color.lstrip("#")[:6], 16)
    except ValueError:
        rgb = 0x40C0FF
    usage = reply.get("usage") or {}
    answer = {
        "title": str(shown.get("title", ""))[:14],
        "lines": [str(line)[:40] for line in shown.get("lines", [])][:4],
        "color": "#{:06x}".format(rgb),
        "scene": shown.get("scene") if shown.get("scene") in SCENES else "clouds",
        "model": reply.get("model", ai_model()),
        "tokens_in": usage.get("prompt_tokens", 0), "tokens_out": usage.get("completion_tokens", 0),
        "ms": time.ticks_diff(time.ticks_ms(), started),
    }
    led.claimed = (rgb >> 16, (rgb >> 8) & 0xFF, rgb & 0xFF)
    set_view(VIEWS.index("oracle"))


# ---- Commands ---------------------------------------------------------------------------------

def show_help():
    print()
    print("Commands: city <name> | weather | now | forecast | week | oracle | ask <question> | status")
    print("          ai | ai url <url> | ai key <key> | ai key forget | ai model <name> | led off")
    print("          view <1-5> | reach [ip] [port] | reconnect | stop | help")
    print("BOOT: click = next view, double-click = refresh the weather, long press = ask the oracle")


def handle_ai(argument):
    what, value = kit.split_command(argument) if argument else ("", "")
    if not what:
        show_ai()
    elif what == "url" and value:
        if not value.startswith(("http://", "https://")):
            print("ai url wants http://... or https://...")
            return
        ai_settings.set("url", value)
        print("Gateway saved: " + value)
    elif what == "key" and value == "forget":
        ai_settings.set("key", "")
        print("Key erased from NVS")
    elif what == "key" and value:
        ai_settings.set("key", value)
        print("Key saved in NVS: " + kit.masked(value))
    elif what == "model" and value:
        ai_settings.set("model", value)
        print("Model: " + value)
    else:
        print("ai | ai url <url> | ai key <key> | ai key forget | ai model <name>")


def handle(text):
    command, argument = kit.split_command(text)
    if command == "city" and argument:
        find_city(argument)
    elif command == "weather":
        fetch_weather("console")
    elif command == "oracle" and answer is None:
        ask("")  # nothing to show yet: ask for the first haiku
    elif command in VIEWS:
        set_view(VIEWS.index(command))
    elif command == "ask" and argument:
        ask(argument)
    elif command == "ai":
        handle_ai(argument)
    elif command == "led" and argument == "off":
        led.claimed = None
        print("LED back to showing the Wi-Fi state")
    elif command == "view" and argument.isdigit() and 1 <= int(argument) <= len(VIEWS):
        set_view(int(argument) - 1)
    elif command in ("reach", "ping"):
        if not connection.joined():
            print("reach needs a network")
        else:
            ms, how = kit.reach(*(argument.split()[:1] or [connection.gateway()]))
            print("  {}".format("{} ms, {}".format(ms, how) if ms is not None else how))
    elif command == "reconnect":
        connection.connect()
    elif command in ("stop", "exit", "quit"):
        raise SystemExit  # leaves the loop through finally: LED off, back to the >>> prompt
    elif command in ("help", "?"):
        show_help()
    else:
        print("unknown command '{}' (type help)".format(text))


# ---- Main loop --------------------------------------------------------------------------------

if not connection.ssid:
    raise SystemExit("No saved network: run wifi_w1_scan.py and join one there first.")
place = load_place()
print("wifi_w3_oracle: {}".format("city " + place["name"] if place else "no city yet: type city <name>"))
show_help()

try:
    while True:
        line = console.line()
        if line is not None:
            handle(line)
        gesture = button.poll()
        if gesture == kit.CLICK:
            set_view(view + 1)
        elif gesture == kit.DOUBLE:
            ask("") if view == VIEWS.index("oracle") else fetch_weather("BOOT")
        elif gesture == kit.LONG:
            ask("")

        event = connection.tick()
        if event == "connected":
            print("+ " + connection.describe())
            if place:
                fetch_weather("joined")
        elif event == "dropped":
            print("- disconnected: {}".format(kit.reason_hint(connection.reason)))
        elif event == "failed":
            print("! could not join '{}': {}".format(connection.ssid, kit.reason_hint(connection.reason)))

        if place and connection.joined() and weather_tried_at is not None:
            wait = WEATHER_EVERY_MS if weather else RETRY_MS
            if time.ticks_diff(time.ticks_ms(), weather_tried_at) >= wait:
                fetch_weather("timer")
        led.wifi(connection.state)
        kit.pause()
finally:
    led.off()
    print("Stopped. The city and the gateway settings stay saved in NVS.")
