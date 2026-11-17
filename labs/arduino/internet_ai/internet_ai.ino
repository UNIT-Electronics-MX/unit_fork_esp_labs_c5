/*
 * W3 Internet & AI: weather from the internet, a poet from the cloud, on the Pulsar C5.
 * Arduino port of examples/micropython/pulsar-c5/wifi_w3_oracle.py (and the parts of wifi_kit.py it uses)
 *
 * Board: ESP32C5 Dev Module, core arduino-esp32 3.3.x. Libraries: WiFi, HTTPClient, ESPmDNS and
 * Preferences come with the core; ArduinoJson (7.x, from the Library Manager) reads and writes the JSON.
 * Serial Monitor at 115200, "Both NL & CR". Type help.
 *
 * Needs a network saved by W1 (scan_join): join one there first.
 *
 * The board becomes an internet client. It fetches the weather for your city over HTTPS as JSON
 * (Open-Meteo: no account, no key) and prints it. Then the Bench Oracle: the board sends an AI
 * model what it knows about this moment (place, time, weather, its own temperature) and gets back
 * a haiku or an answer, plus an LED colour and a scene, as structured JSON. The request goes
 * through a LiteLLM gateway on your computer (esp-labs/apps/ai-gateway), so any provider and
 * model works and the provider keys never touch the chip.
 *
 * Views: now | forecast | week | oracle | status
 * BOOT: click = next view, double-click = refresh the weather, long press = ask the oracle
 * The LED: the oracle's colour after an answer (led off gives it back); else blue while scanning,
 * amber blink while joining, green when joined, red after a failure.
 */

#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <fcntl.h>
#include <lwip/netdb.h>
#include <lwip/sockets.h>
#include <math.h>
#include <string.h>
#include <time.h>

// ---- Settings (same as the MicroPython script) ---------------------------------------------

static const uint32_t WEATHER_EVERY_MS = 15UL * 60000;  // Open-Meteo updates its model every 15 min
static const uint32_t RETRY_MS = 60000;                  // after a failed fetch
static const uint32_t ORACLE_GAP_MS = 10000;             // at most one AI request per 10 s
static const char *DEFAULT_MODEL = "oracle";             // a model_name in ai-gateway/config.yaml

static const int PULSAR_BOOT_PIN = 28;
static const int PULSAR_RGB_PIN = 0;  // Some UNIT illustrations label it GPIO8: try 8 if the LED stays dark.
static const char *HOSTNAME = "bench";  // the board answers to bench.local

// ---- Types first: the Arduino builder puts function prototypes before the first function ------

enum Gesture { NONE = 0, CLICK = 1, DOUBLE = 2, LONG = 3 };
enum LinkState { NO_CREDENTIALS, CONNECTING, CONNECTED, FAILED };
enum TickEvent { TICK_NONE, TICK_CONNECTED, TICK_DROPPED, TICK_FAILED };

static const int VIEW_COUNT = 5;
static const char *const VIEWS[VIEW_COUNT] = {"now", "forecast", "week", "oracle", "status"};
static const int VIEW_ORACLE = 3;
static const int SCENE_COUNT = 7;
static const char *const SCENES[SCENE_COUNT] = {"sun", "clouds", "rain", "snow", "storm", "fog", "night"};
static const char *const WEEKDAYS[7] = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"};
static const char *const SPARKS[8] = {"▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"};

static const uint32_t JOIN_TIMEOUT_MS = 20000;
static const uint32_t LINK_RETRY_MS = 30000;
static const int ATTEMPTS = 3;  // the driver's own retries per join; after them we know why it failed

struct Place {
  bool valid;
  String name;
  double lat, lon;
};

// What the views and the oracle need, see fetchWeather().
struct Weather {
  bool valid;
  float temp, feels, wind;
  int humidity, code;
  bool day;
  int hourCount;
  float hourTemp[24];
  int hourRain[24];
  int dayCount;
  String dayDate[7];
  float dayMax[7], dayMin[7];
  int dayCode[7];
};

// The oracle's last answer.
struct Answer {
  bool valid;
  String title;
  String lines[4];
  int lineCount;
  String color;  // "#rrggbb"
  String scene;
  String model;
  int tokensIn, tokensOut;
  uint32_t ms;
};

// ---- State -----------------------------------------------------------------------------------------

static Place place = {};
static Weather weather = {};
static Answer answer = {};
static bool weatherTried = false;
static uint32_t weatherTriedAt = 0;
static bool oracleAsked = false;
static uint32_t oracleAt = 0;
static int view = 0;
static bool fatal = false;

// LED
static bool ledClaimed = false;
static uint8_t claimed[3];

// Connection
static String savedSsid, savedPassword;
static int utcOffset = 0;  // minutes east of UTC
static LinkState state = NO_CREDENTIALS;
static int reason = 0;
static uint32_t startedAt = 0, failedAt = 0, ntpAt = 0, toldAt = 0;
static bool mdnsStarted = false;
static volatile int lastReason = 0;  // the driver's last disconnect reason (event task)
static volatile int attempts = 0;    // disconnects seen while joining (event task)

static uint32_t since(uint32_t now, uint32_t then) {  // ticks_diff
  return now - then;
}

// ---- Settings in NVS ---------------------------------------------------------------------------------

// Short text values, one NVS namespace each: "mpwifi" (W1's network), "w3" (the city), "ai" (the
// gateway). Stored as blobs like MicroPython's esp32.NVS.set_blob, so the MicroPython labs read them too.
static String nvsGet(const char *ns, const char *key, const char *fallback = "") {
  Preferences prefs;
  prefs.begin(ns, true);
  size_t length = prefs.getBytesLength(key);
  String value = fallback;
  if (length > 0 && length <= 512) {
    uint8_t buffer[513];
    prefs.getBytes(key, buffer, length);
    buffer[length] = 0;
    value = String((char *)buffer);
  }
  prefs.end();
  return value;
}

// Save a value; an empty one removes the key.
static void nvsSet(const char *ns, const char *key, const String &value) {
  Preferences prefs;
  prefs.begin(ns, false);
  if (value.length()) prefs.putBytes(key, value.c_str(), value.length());
  else prefs.remove(key);
  prefs.end();
}

static String aiUrl() {
  return nvsGet("ai", "url");
}
static String aiKey() {
  return nvsGet("ai", "key");
}
static String aiModel() {
  return nvsGet("ai", "model", DEFAULT_MODEL);
}

// ---- Plain-language helpers -------------------------------------------------------------------

static bool isNumber(const String &text) {
  if (text.length() == 0) return false;
  for (size_t i = 0; i < text.length(); i++)
    if (!isdigit(text[i])) return false;
  return true;
}

// '...wxyz (41 characters)', never the whole secret.
static String masked(const String &secret) {
  if (secret.length() == 0) return "not set";
  String tail = secret.substring(secret.length() > 4 ? secret.length() - 4 : 0);
  return "..." + tail + " (" + String(secret.length()) + " characters)";
}

// The first `count` characters of a UTF-8 text (not bytes: never cuts a letter in half).
static String firstCharacters(const String &text, int count) {
  int characters = 0;
  size_t i = 0;
  while (i < text.length() && characters < count) {
    unsigned char lead = text[i];
    i += lead < 0x80 ? 1 : (lead >> 5) == 0x06 ? 2 : (lead >> 4) == 0x0E ? 3 : (lead >> 3) == 0x1E ? 4 : 1;
    characters++;
  }
  return text.substring(0, i);
}

// One character per value, from ▁ (lowest) to █ (highest).
static String sparkline(const float *values, int count, float low, float high) {
  String text;
  float span = max(high - low, 1.0f);
  for (int i = 0; i < count; i++) {
    int index = (int)((values[i] - low) * 7.0f / span + 0.5f);
    text += SPARKS[min(7, max(0, index))];
  }
  return text;
}

static String urlEncode(const String &text) {
  String out;
  for (size_t i = 0; i < text.length(); i++) {
    unsigned char c = text[i];
    if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
      out += (char)c;
    } else {
      char hex[4];
      snprintf(hex, sizeof(hex), "%%%02X", c);
      out += hex;
    }
  }
  return out;
}

static String reasonHint(int code) {
  switch (code) {
    case 0: return "none yet";
    case 2: return "wrong password?";
    case 8: return "we left on purpose";
    case 15: return "wrong password?";
    case 200: return "lost the router's beacons (weak signal)";
    case 201: return "network not found (out of range, or a typo in the name)";
    case 202: return "wrong password?";
    case 203: return "the router refused us (MAC filter, or full)";
    case 204: return "wrong password? (the handshake timed out)";
  }
  return "reason " + String(code);
}

static String formatOffset(int minutes) {
  char text[16];
  int absolute = abs(minutes);
  snprintf(text, sizeof(text), "%c%d:%02d", minutes >= 0 ? '+' : '-', absolute / 60, absolute % 60);
  return String(text);
}

// ---- Weather codes ----------------------------------------------------------------------------

// WMO weather interpretation codes, as Open-Meteo reports them.
static String describeCode(int code) {
  if (code == 0) return "clear sky";
  if (code >= 1 && code <= 3) return code == 1 ? "mainly clear" : code == 2 ? "partly cloudy" : "overcast";
  if (code == 45 || code == 48) return "fog";
  if (code >= 51 && code <= 57) return "drizzle";
  if (code == 61) return "light rain";
  if (code == 63) return "rain";
  if (code == 65) return "heavy rain";
  if (code == 66 || code == 67) return "freezing rain";
  if (code >= 71 && code <= 77) return "snow";
  if (code >= 80 && code <= 82) return "rain showers";
  if (code == 85 || code == 86) return "snow showers";
  if (code == 95) return "thunderstorm";
  if (code == 96 || code == 99) return "thunderstorm, hail";
  return "unknown";
}

static const char *sceneFor(int code, bool day) {
  if (code >= 95) return "storm";
  if ((code >= 71 && code <= 77) || code == 85 || code == 86) return "snow";
  if (code >= 51) return "rain";
  if (code == 45 || code == 48) return "fog";
  if (code >= 2) return "clouds";
  return day ? "sun" : "night";
}

// '2026-11-11' -> 'Wed' (Sakamoto's day-of-week method).
static String weekday(const String &date) {
  static const int offsets[12] = {0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4};
  int year = date.substring(0, 4).toInt(), month = date.substring(5, 7).toInt(), day = date.substring(8, 10).toInt();
  if (month < 1 || month > 12) return "?";
  if (month < 3) year--;
  int sunday0 = (year + year / 4 - year / 100 + year / 400 + offsets[month - 1] + day) % 7;
  return WEEKDAYS[(sunday0 + 6) % 7];
}

// ---- Internet time ---------------------------------------------------------------------------------

static void syncTime() {
  ntpAt = millis();
  configTime(0, 0, "pool.ntp.org");
}

static bool timeSynced() {
  return time(nullptr) >= 1735689600;  // 2025-01-01: before the first answer the clock says 1970
}

// '14:05:09' in your time zone, or "" before the first NTP answer.
static String localTime() {
  if (!timeSynced()) return "";
  time_t shifted = time(nullptr) + utcOffset * 60;
  struct tm t;
  gmtime_r(&shifted, &t);
  char text[16];
  snprintf(text, sizeof(text), "%02d:%02d:%02d", t.tm_hour, t.tm_min, t.tm_sec);
  return String(text);
}

// ---- Connection: join the saved network and keep it --------------------------------------------------

// The driver's disconnect reasons only arrive as events.
static void onWifiEvent(arduino_event_id_t event, arduino_event_info_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    lastReason = info.wifi_sta_disconnected.reason;
    if (lastReason != 8 && state == CONNECTING) attempts = attempts + 1;  // 8: we left on purpose
  }
}

static bool joined() {
  return state == CONNECTED;
}

static void connectNow() {
  if (savedSsid.length() == 0) return;
  WiFi.disconnect(false);
  reason = 0;
  attempts = 0;
  startedAt = toldAt = millis();
  WiFi.setAutoReconnect(true);
  wl_status_t status = savedPassword.length() ? WiFi.begin(savedSsid.c_str(), savedPassword.c_str())
                                              : WiFi.begin(savedSsid.c_str());
  if (status == WL_CONNECT_FAILED) {
    Serial.println("! could not start joining");
    state = FAILED;
    failedAt = millis();
    return;
  }
  state = CONNECTING;
  Serial.printf("Joining '%s' (%u password characters)...\n", savedSsid.c_str(), (unsigned)savedPassword.length());
}

static void setUtcOffset(int minutes) {
  utcOffset = minutes;
  nvsSet("mpwifi", "tz", String(minutes));
  Serial.println("Time zone: UTC" + formatOffset(minutes));
}

static int joiningFor() {
  return since(millis(), startedAt) / 1000;
}

// Call from loop(). Returns the event once when it happens, otherwise TICK_NONE.
static TickEvent connectionTick() {
  uint32_t now = millis();
  bool isJoined = WiFi.status() == WL_CONNECTED;
  if (isJoined && state != CONNECTED) {
    state = CONNECTED;
    WiFi.setSleep(false);  // a radio that dozes between beacons misses requests
    syncTime();
    if (!mdnsStarted) mdnsStarted = MDNS.begin(HOSTNAME);
    return TICK_CONNECTED;
  }
  if (!isJoined && state == CONNECTED) {
    reason = lastReason;
    state = CONNECTING;
    startedAt = now;
    attempts = 0;
    return TICK_DROPPED;
  }
  if (state == CONNECTING) {
    bool gaveUp = attempts >= ATTEMPTS;
    if (gaveUp) reason = lastReason;
    if (since(now, toldAt) >= 3000) {
      toldAt = now;
      Serial.printf("  ...%d s, %s\n", joiningFor(), gaveUp ? reasonHint(reason).c_str() : "still trying");
    }
    if (gaveUp || since(now, startedAt) > JOIN_TIMEOUT_MS) {
      state = FAILED;
      failedAt = now;
      WiFi.disconnect(false);
      return TICK_FAILED;
    }
  }
  if (state == FAILED && since(now, failedAt) > LINK_RETRY_MS) connectNow();
  if (state == CONNECTED && !timeSynced() && since(now, ntpAt) > 30000) syncTime();
  return TICK_NONE;
}

static String describeLink() {
  switch (state) {
    case NO_CREDENTIALS: return "Not joined. Scan, then: join <#>";
    case CONNECTING: return "Joining '" + savedSsid + "' (" + String(joiningFor()) + " s)...";
    case FAILED:
      return "Could not join '" + savedSsid + "': " + reasonHint(reason) + ". Retrying every " +
             String(LINK_RETRY_MS / 1000) + " s.";
    default: break;
  }
  int channel = WiFi.channel();
  String clock = localTime();
  return "Joined '" + savedSsid + "' | IP " + WiFi.localIP().toString() + " | " + HOSTNAME + ".local | ch " +
         String(channel) + " (" + (channel > 14 ? "5 GHz" : "2.4 GHz") + ") | " + String(WiFi.RSSI()) + " dBm | time " +
         (clock.length() ? clock : String("not synced yet"));
}

// ---- Reach: is anyone there? ------------------------------------------------------------------------

// Time a TCP connection: a host that answers at all (accepts, or refuses with a reset) is reachable.
// Returns the milliseconds, or -1 with the reason in how.
static int reachOnce(const char *host, int port, uint32_t timeoutMs, String &how) {
  struct addrinfo hints = {};
  hints.ai_family = AF_INET;
  hints.ai_socktype = SOCK_STREAM;
  struct addrinfo *result = nullptr;
  char portText[8];
  snprintf(portText, sizeof(portText), "%d", port);
  if (getaddrinfo(host, portText, &hints, &result) != 0 || !result) {
    how = "unknown host";
    return -1;
  }
  int sock = socket(result->ai_family, result->ai_socktype, 0);
  if (sock < 0) {
    freeaddrinfo(result);
    how = "no answer";
    return -1;
  }
  fcntl(sock, F_SETFL, fcntl(sock, F_GETFL, 0) | O_NONBLOCK);
  uint32_t started = millis();
  int rc = connect(sock, result->ai_addr, result->ai_addrlen);
  int error = rc == 0 ? 0 : errno;
  if (rc != 0 && errno == EINPROGRESS) {
    fd_set writes;
    FD_ZERO(&writes);
    FD_SET(sock, &writes);
    struct timeval timeout = {(time_t)(timeoutMs / 1000), (suseconds_t)(timeoutMs % 1000 * 1000)};
    if (select(sock + 1, nullptr, &writes, nullptr, &timeout) > 0) {
      socklen_t length = sizeof(error);
      getsockopt(sock, SOL_SOCKET, SO_ERROR, &error, &length);
    } else {
      error = ETIMEDOUT;
    }
  }
  uint32_t ms = since(millis(), started);
  close(sock);
  freeaddrinfo(result);
  if (error == 0) {
    how = "port " + String(port) + " open";
    return ms;
  }
  if (error == ECONNRESET || error == ECONNREFUSED) {  // it answered "no"
    how = "port " + String(port) + " closed, but it answered";
    return ms;
  }
  how = "no answer";
  return -1;
}

// ---- The WS2812 as a status light ---------------------------------------------------------------------

// The LED is very bright at full power.
static void showLed(uint8_t r, uint8_t g, uint8_t b) {
  const int LEVEL = 40;  // 0-255
  static int32_t shown = -1;
  int32_t value = ((r * LEVEL / 255) << 16) | ((g * LEVEL / 255) << 8) | (b * LEVEL / 255);
  if (value != shown) {
    shown = value;
    rgbLedWrite(PULSAR_RGB_PIN, r * LEVEL / 255, g * LEVEL / 255, b * LEVEL / 255);
  }
}

// A colour chosen on purpose (the oracle's) wins; else the Wi-Fi state.
static void ledWifi() {
  bool blink = (millis() / 400) % 2 == 0;
  if (ledClaimed) showLed(claimed[0], claimed[1], claimed[2]);
  else if (state == CONNECTED) showLed(0, 255, 0);
  else if (state == CONNECTING) blink ? showLed(255, 140, 0) : showLed(40, 20, 0);
  else if (state == FAILED) showLed(255, 0, 0);
  else showLed(0, 0, 0);
}

// ---- HTTPS ---------------------------------------------------------------------------------------------

// GET a JSON answer over HTTPS. Like MicroPython's requests, it does not check the certificate.
static bool getJson(const String &url, JsonDocument &doc, uint32_t &ms, size_t &size, String &error) {
  uint32_t started = millis();
  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.setTimeout(15000);
  if (!http.begin(client, url)) {
    error = "bad URL";
    return false;
  }
  int code = http.GET();
  if (code <= 0) {
    error = HTTPClient::errorToString(code);
    http.end();
    return false;
  }
  if (code != 200) {
    error = "HTTP " + String(code);
    http.end();
    return false;
  }
  String body = http.getString();
  http.end();
  ms = since(millis(), started);
  size = body.length();
  DeserializationError parse = deserializeJson(doc, body);
  if (parse) {
    error = parse.c_str();
    return false;
  }
  return true;
}

static void showNow();

static void fetchWeather(const char *why) {
  if (!place.valid) {
    Serial.println("No city yet: type city <name>");
    return;
  }
  if (!joined()) {
    Serial.println("Not on Wi-Fi yet");
    return;
  }
  weatherTried = true;
  weatherTriedAt = millis();
  char url[420];
  snprintf(url, sizeof(url),
           "https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f&timezone=auto"
           "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day"
           "&hourly=temperature_2m,precipitation_probability&forecast_hours=24"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min&forecast_days=7",
           place.lat, place.lon);
  Serial.printf("weather for %s (%s)...\n", place.name.c_str(), why);

  JsonDocument doc;
  uint32_t ms = 0;
  size_t size = 0;
  String error;
  bool ok = getJson(url, doc, ms, size, error);
  if (ok) {
    JsonObject now = doc["current"], hourly = doc["hourly"], daily = doc["daily"];
    if (now.isNull() || hourly.isNull() || daily.isNull()) {
      ok = false;
      error = "missing 'current', 'hourly' or 'daily'";
    } else {
      Weather w = {};
      w.temp = now["temperature_2m"] | 0.0f;
      w.feels = now["apparent_temperature"] | 0.0f;
      w.humidity = now["relative_humidity_2m"] | 0;
      w.code = now["weather_code"] | 0;
      w.wind = now["wind_speed_10m"] | 0.0f;
      w.day = (now["is_day"] | 0) == 1;
      JsonArray temps = hourly["temperature_2m"], rain = hourly["precipitation_probability"];
      for (JsonVariant value : temps) {
        if (w.hourCount >= 24) break;
        w.hourTemp[w.hourCount] = value | 0.0f;
        w.hourRain[w.hourCount] = rain[w.hourCount] | 0;  // null means no chance
        w.hourCount++;
      }
      JsonArray dates = daily["time"], codes = daily["weather_code"], highs = daily["temperature_2m_max"],
                lows = daily["temperature_2m_min"];
      for (int i = 0; i < (int)dates.size() && i < 7; i++) {
        w.dayDate[i] = dates[i].as<String>();
        w.dayMax[i] = highs[i] | 0.0f;
        w.dayMin[i] = lows[i] | 0.0f;
        w.dayCode[i] = codes[i] | 0;
        w.dayCount++;
      }
      w.valid = true;
      weather = w;
    }
  }
  if (!ok) {
    Serial.printf("! weather failed: %s. Retrying in %lu s.\n", error.c_str(), (unsigned long)(RETRY_MS / 1000));
    return;
  }
  // Open-Meteo answers in your local time zone (timezone=auto) and says which one: use it.
  if (doc["utc_offset_seconds"].is<int>()) {
    int minutes = doc["utc_offset_seconds"].as<int>() / 60;
    if (minutes != utcOffset) setUtcOffset(minutes);
  }
  Serial.printf("< %u bytes in %lu ms\n", (unsigned)size, (unsigned long)ms);
  showNow();
}

static void findCity(const String &name) {
  if (!joined()) {
    Serial.println("Not on Wi-Fi yet");
    return;
  }
  JsonDocument doc;
  uint32_t ms = 0;
  size_t size = 0;
  String error;
  if (!getJson("https://geocoding-api.open-meteo.com/v1/search?count=1&language=en&format=json&name=" + urlEncode(name),
               doc, ms, size, error)) {
    Serial.println("! city lookup failed: " + error);
    return;
  }
  JsonArray hits = doc["results"];
  if (hits.isNull() || hits.size() == 0) {
    Serial.println("! no place called '" + name + "'");
    return;
  }
  JsonObject hit = hits[0];
  place.valid = true;
  place.name = String(hit["name"] | "?") + ", " + String(hit["country"] | "");
  place.lat = hit["latitude"] | 0.0;
  place.lon = hit["longitude"] | 0.0;
  nvsSet("w3", "name", place.name);
  nvsSet("w3", "lat", String(place.lat, 6));
  nvsSet("w3", "lon", String(place.lon, 6));
  Serial.printf("Found %s (%.2f, %.2f) in %lu ms, %u bytes. Saved.\n", place.name.c_str(), place.lat, place.lon,
                (unsigned long)ms, (unsigned)size);
  fetchWeather("new city");
}

static void loadPlace() {
  String name = nvsGet("w3", "name");
  if (name.length() == 0) return;
  place.valid = true;
  place.name = name;
  place.lat = nvsGet("w3", "lat", "0").toDouble();
  place.lon = nvsGet("w3", "lon", "0").toDouble();
}

// ---- Views ------------------------------------------------------------------------------------------------

static bool needWeather() {
  if (!weather.valid) {
    Serial.printf("No weather yet: %s\n", place.valid ? "type weather" : "type city <name>");
    return false;
  }
  return true;
}

static void showNow() {
  if (!needWeather()) return;
  const Weather &w = weather;
  Serial.printf("\n%s  %s\n", place.name.c_str(), localTime().c_str());
  Serial.printf("  %.1f °C, feels like %.1f °C, %s\n", w.temp, w.feels, describeCode(w.code).c_str());
  Serial.printf("  humidity %d %%, wind %.0f km/h, %s  [scene: %s]\n", w.humidity, w.wind, w.day ? "daylight" : "night",
                sceneFor(w.code, w.day));
}

static void showForecast() {
  if (!needWeather()) return;
  const Weather &w = weather;
  float low = w.hourTemp[0], high = w.hourTemp[0];
  int maxRain = 0;
  float rain[24];
  for (int i = 0; i < w.hourCount; i++) {
    low = min(low, w.hourTemp[i]);
    high = max(high, w.hourTemp[i]);
    maxRain = max(maxRain, w.hourRain[i]);
    rain[i] = w.hourRain[i];
  }
  Serial.printf("\nNext %d hours: %.0f to %.0f °C\n", w.hourCount, low, high);
  Serial.println("  temp  " + sparkline(w.hourTemp, w.hourCount, low, high));
  Serial.println("  rain  " + sparkline(rain, w.hourCount, 0, 100) + "  (max " + String(maxRain) + " %)");
  String ruler = "        ";
  for (int h = 0; h < w.hourCount; h += 3) {
    char cell[8];
    snprintf(cell, sizeof(cell), "%-3d", h);
    ruler += cell;
  }
  Serial.println(ruler + " hours from now");
}

static void showWeek() {
  if (!needWeather()) return;
  Serial.println();
  for (int i = 0; i < weather.dayCount; i++)
    Serial.printf("  %s %s  %5.1f / %5.1f °C  %s\n", weekday(weather.dayDate[i]).c_str(),
                  weather.dayDate[i].substring(5).c_str(), weather.dayMin[i], weather.dayMax[i],
                  describeCode(weather.dayCode[i]).c_str());
}

static void showOracle() {
  if (!answer.valid) {
    Serial.println("No answer yet: type oracle, ask <question>, or hold BOOT");
    return;
  }
  Serial.printf("\n=== %s ===\n", answer.title.c_str());
  for (int i = 0; i < answer.lineCount; i++) Serial.println("  " + answer.lines[i]);
  Serial.printf("LED %s | scene %s | %s | %d+%d tokens | %.1f s\n", answer.color.c_str(), answer.scene.c_str(),
                answer.model.c_str(), answer.tokensIn, answer.tokensOut, answer.ms / 1000.0f);
}

static void showAi() {
  String url = aiUrl();
  Serial.printf("AI gateway: %s | key %s | model %s\n", url.length() ? url.c_str() : "not set (ai url <url>)",
                masked(aiKey()).c_str(), aiModel().c_str());
}

static void showStatus() {
  Serial.println(describeLink());
  Serial.println("City: " + (place.valid ? place.name : String("not set (city <name>)")));
  showAi();
}

static void setView(int number) {
  view = ((number % VIEW_COUNT) + VIEW_COUNT) % VIEW_COUNT;
  Serial.printf("view %d: %s\n", view + 1, VIEWS[view]);
  switch (view) {
    case 0: showNow(); break;
    case 1: showForecast(); break;
    case 2: showWeek(); break;
    case 3: showOracle(); break;
    default: showStatus(); break;
  }
}

// ---- The Bench Oracle -------------------------------------------------------------------------------------

// What the oracle gets to know about this moment, as a small JSON text.
static String context() {
  JsonDocument moment;
  if (place.valid) moment["place"] = place.name;
  String local = localTime();
  if (local.length()) moment["local_time"] = local;
  if (weather.valid) {
    const Weather &w = weather;
    JsonObject o = moment["weather"].to<JsonObject>();
    o["now"] = describeCode(w.code);
    o["temp_c"] = w.temp;
    o["feels_like_c"] = w.feels;
    o["wind_kmh"] = (int)lroundf(w.wind);
    o["humidity_pct"] = w.humidity;
    o["daylight"] = w.day;
    int maxRain = 0;
    for (int i = 0; i < w.hourCount; i++) maxRain = max(maxRain, w.hourRain[i]);
    o["max_rain_chance_next_24h_pct"] = maxRain;
    o["today_min_c"] = (int)lroundf(w.dayMin[0]);
    o["today_max_c"] = (int)lroundf(w.dayMax[0]);
  }
  JsonObject bench = moment["bench"].to<JsonObject>();
  bench["board"] = "UNIT Pulsar ESP32-C5: no screen, a serial console and one RGB LED";
  bench["uptime_min"] = millis() / 60000;
  bench["wifi_signal_dbm"] = WiFi.RSSI();
  float celsius = temperatureRead();
  if (!isnan(celsius)) bench["chip_temp_c"] = celsius;
  String text;
  serializeJson(moment, text);
  return text;
}

static const char *SYSTEM_PROMPT =
  "You are the Bench Oracle, living inside an ESP32-C5 on a maker's workbench with a serial "
  "console and an RGB LED. Be warm, witty and concrete; use the context (place, time, weather, "
  "the chip's own state) when it fits. Always answer by calling show_on_bench.";

// One function, and the model must call it: the reply is then always JSON in this shape.
static void buildRequest(const String &task, String &body) {
  JsonDocument request;
  request["model"] = aiModel();
  request["max_tokens"] = 400;
  JsonArray messages = request["messages"].to<JsonArray>();
  JsonObject system = messages.add<JsonObject>();
  system["role"] = "system";
  system["content"] = SYSTEM_PROMPT;
  JsonObject user = messages.add<JsonObject>();
  user["role"] = "user";
  user["content"] = "Context: " + context() + "\n" + task;

  JsonObject function = request["tools"].to<JsonArray>().add<JsonObject>();
  function["type"] = "function";
  JsonObject definition = function["function"].to<JsonObject>();
  definition["name"] = "show_on_bench";
  definition["description"] = "Print text on the bench's serial console and set its RGB LED.";
  JsonObject parameters = definition["parameters"].to<JsonObject>();
  parameters["type"] = "object";
  JsonObject properties = parameters["properties"].to<JsonObject>();
  JsonObject title = properties["title"].to<JsonObject>();
  title["type"] = "string";
  title["description"] = "one or two words, at most 14 characters";
  JsonObject lines = properties["lines"].to<JsonObject>();
  lines["type"] = "array";
  lines["items"].to<JsonObject>()["type"] = "string";
  lines["description"] = "1 to 4 lines, at most 40 characters each";
  JsonObject color = properties["color"].to<JsonObject>();
  color["type"] = "string";
  color["description"] = "LED colour matching the mood, as #rrggbb";
  JsonObject scene = properties["scene"].to<JsonObject>();
  scene["type"] = "string";
  JsonArray sceneNames = scene["enum"].to<JsonArray>();
  for (const char *name : SCENES) sceneNames.add(name);
  JsonArray required = parameters["required"].to<JsonArray>();
  required.add("title");
  required.add("lines");
  required.add("color");
  required.add("scene");

  JsonObject choice = request["tool_choice"].to<JsonObject>();
  choice["type"] = "function";
  choice["function"].to<JsonObject>()["name"] = "show_on_bench";
  serializeJson(request, body);
}

// POST the JSON to the gateway (http:// on your computer, or https://). Returns the HTTP status, or
// a negative HTTPClient error with the words in error.
static int postJson(const String &url, const String &key, const String &body, String &response, String &error) {
  WiFiClient plain;
  WiFiClientSecure secure;
  secure.setInsecure();
  HTTPClient http;
  http.setTimeout(30000);
  bool started = url.startsWith("https://") ? http.begin(secure, url) : http.begin(plain, url);
  if (!started) {
    error = "bad URL";
    return -1;
  }
  http.addHeader("Authorization", "Bearer " + key);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  if (code <= 0) error = HTTPClient::errorToString(code);
  else response = http.getString();
  http.end();
  return code;
}

static void ask(const String &question) {
  if (!joined()) {
    Serial.println("Not on Wi-Fi yet");
    return;
  }
  String url = aiUrl(), key = aiKey();
  if (url.length() == 0 || key.length() == 0) {
    Serial.println("Set the gateway first: ai url http://<your computer's IP>:4000 and ai key <key>");
    return;
  }
  if (oracleAsked && since(millis(), oracleAt) < ORACLE_GAP_MS) {
    Serial.printf("One question per %lu s, please: the gateway bills every request\n",
                  (unsigned long)(ORACLE_GAP_MS / 1000));
    return;
  }
  oracleAsked = true;
  oracleAt = millis();
  String task = question.length() == 0
                  ? String("Write a haiku (3 lines) about this exact moment at the bench, weaving in the weather. "
                           "Title: the mood in one or two words.")
                  : "Question from the maker: " + question +
                      "\nAnswer in at most 4 short lines. Title: a one or two word headline.";
  String body;
  buildRequest(task, body);
  Serial.println("asking " + aiModel() + " ...");
  uint32_t started = millis();
  while (url.endsWith("/")) url.remove(url.length() - 1);
  String response, error;
  int status = postJson(url + "/v1/chat/completions", key, body, response, error);
  JsonDocument reply;
  if (status > 0) {
    DeserializationError parse = deserializeJson(reply, response);
    if (parse) {
      error = parse.c_str();
      status = -1;
    }
  }
  if (status <= 0) {
    Serial.println("! oracle failed: " + error + " (is the gateway running, and is the address right?)");
    return;
  }
  if (status != 200) {
    // LiteLLM explains errors as {"error": {"message": ...}}
    String message = reply["error"]["message"] | "";
    Serial.printf("! HTTP %d: %s\n", status, firstCharacters(message, 200).c_str());
    return;
  }
  // The function's arguments arrive as a JSON *string* inside the JSON answer: parse it again.
  const char *arguments = reply["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"];
  JsonDocument shown;
  if (!arguments || deserializeJson(shown, arguments)) {
    Serial.println("! no show_on_bench call in the reply (does this model support tools?)");
    return;
  }
  String color = shown["color"] | "#40c0ff";
  String digits = color;
  while (digits.startsWith("#")) digits.remove(0, 1);
  digits = digits.substring(0, 6);
  char *end = nullptr;
  uint32_t rgb = strtoul(digits.c_str(), &end, 16);
  if (digits.length() == 0 || *end != 0) rgb = 0x40C0FF;

  Answer a = {};
  a.valid = true;
  a.title = firstCharacters(shown["title"] | "", 14);
  JsonArray lines = shown["lines"];
  for (JsonVariant line : lines) {
    if (a.lineCount >= 4) break;
    a.lines[a.lineCount++] = firstCharacters(line.as<String>(), 40);
  }
  a.color = "#" + String(rgb, HEX);
  while (a.color.length() < 7) a.color = "#0" + a.color.substring(1);  // #rrggbb: six digits
  String scene = shown["scene"] | "";
  a.scene = "clouds";
  for (const char *name : SCENES)
    if (scene == name) a.scene = scene;
  a.model = String(reply["model"] | aiModel().c_str());
  a.tokensIn = reply["usage"]["prompt_tokens"] | 0;
  a.tokensOut = reply["usage"]["completion_tokens"] | 0;
  a.ms = since(millis(), started);
  answer = a;
  claimed[0] = rgb >> 16;
  claimed[1] = (rgb >> 8) & 0xFF;
  claimed[2] = rgb & 0xFF;
  ledClaimed = true;
  setView(VIEW_ORACLE);
}

// ---- Commands ---------------------------------------------------------------------------------------------

static void showHelp() {
  Serial.println();
  Serial.println("Commands: city <name> | weather | now | forecast | week | oracle | ask <question> | status");
  Serial.println("          ai | ai url <url> | ai key <key> | ai key forget | ai model <name> | led off");
  Serial.println("          view <1-5> | reach [ip] [port] | reconnect | stop | help");
  Serial.println("BOOT: click = next view, double-click = refresh the weather, long press = ask the oracle");
}

// 'City Mexico City' -> ('city', 'Mexico City')
static void splitCommand(const String &text, String &head, String &rest) {
  int space = text.indexOf(' ');
  head = space < 0 ? text : text.substring(0, space);
  rest = space < 0 ? "" : text.substring(space + 1);
  head.toLowerCase();
  rest.trim();
}

static void handleAi(const String &argument) {
  String what, value;
  if (argument.length()) splitCommand(argument, what, value);
  if (what.length() == 0) {
    showAi();
  } else if (what == "url" && value.length()) {
    if (!value.startsWith("http://") && !value.startsWith("https://")) {
      Serial.println("ai url wants http://... or https://...");
      return;
    }
    nvsSet("ai", "url", value);
    Serial.println("Gateway saved: " + value);
  } else if (what == "key" && value == "forget") {
    nvsSet("ai", "key", "");
    Serial.println("Key erased from NVS");
  } else if (what == "key" && value.length()) {
    nvsSet("ai", "key", value);
    Serial.println("Key saved in NVS: " + masked(value));
  } else if (what == "model" && value.length()) {
    nvsSet("ai", "model", value);
    Serial.println("Model: " + value);
  } else {
    Serial.println("ai | ai url <url> | ai key <key> | ai key forget | ai model <name>");
  }
}

static void handle(const String &text) {
  String command, argument;
  splitCommand(text, command, argument);
  int viewIndex = -1;
  for (int i = 0; i < VIEW_COUNT; i++)
    if (command == VIEWS[i]) viewIndex = i;

  if (command == "city" && argument.length()) {
    findCity(argument);
  } else if (command == "weather") {
    fetchWeather("console");
  } else if (command == "oracle" && !answer.valid) {
    ask("");  // nothing to show yet: ask for the first haiku
  } else if (viewIndex >= 0) {
    setView(viewIndex);
  } else if (command == "ask" && argument.length()) {
    ask(argument);
  } else if (command == "ai") {
    handleAi(argument);
  } else if (command == "led" && argument == "off") {
    ledClaimed = false;
    Serial.println("LED back to showing the Wi-Fi state");
  } else if (command == "view" && isNumber(argument) && argument.toInt() >= 1 && argument.toInt() <= VIEW_COUNT) {
    setView(argument.toInt() - 1);
  } else if (command == "reach" || command == "ping") {
    if (!joined()) {
      Serial.println("reach needs a network");
    } else {
      String host = argument;
      int space = host.indexOf(' ');
      if (space >= 0) host = host.substring(0, space);
      if (host.length() == 0) host = WiFi.gatewayIP().toString();
      String how;
      int ms = reachOnce(host.c_str(), 80, 2000, how);
      if (ms < 0) Serial.println("  " + how);
      else Serial.printf("  %d ms, %s\n", ms, how.c_str());
    }
  } else if (command == "reconnect") {
    connectNow();
  } else if (command == "stop" || command == "exit" || command == "quit") {
    // A sketch cannot exit like a script: it goes quiet. RESET runs it again.
    showLed(0, 0, 0);
    Serial.println("Stopped (RESET runs it again). The city and the gateway settings stay saved in NVS.");
    fatal = true;
  } else if (command == "help" || command == "?") {
    showHelp();
  } else {
    Serial.println("unknown command '" + text + "' (type help)");
  }
}

// ---- The serial console, without blocking the radio ---------------------------------------------------------

// The text a command echoes as: the argument of 'ai key ' is a secret and never shown.
static String echoOf(const String &text) {
  const String prefix = "ai key ";
  String lower = text;
  lower.toLowerCase();
  if (lower.startsWith(prefix) && text.length() > prefix.length()) {
    String secret = text.substring(prefix.length());
    secret.trim();
    return prefix + masked(secret);
  }
  return text;
}

// Collects typed characters and returns one command line at a time, without blocking.
static bool consoleLine(String &out) {
  static String buffer;
  static bool afterCr = false;
  while (Serial.available()) {
    char c = Serial.read();
    bool wasCr = afterCr;
    afterCr = c == '\r';
    if (c == '\n' && wasCr) continue;  // the second half of a CR LF ending, not an empty line
    if (c == '\r' || c == '\n') {
      String text = buffer;
      text.trim();
      buffer = "";
      if (text.length()) {
        out = text;
        Serial.println("> " + echoOf(text));
        return true;
      }
    } else if (c == 0x08 || c == 0x7F) {
      if (buffer.length()) buffer.remove(buffer.length() - 1);
    } else if (buffer.length() < 200) {
      buffer += c;
    }
  }
  return false;
}

// ---- The BOOT button: click, double-click, long press -----------------------------------------------------------

// Polled from the main loop: returns CLICK, DOUBLE, LONG or NONE.
static Gesture pollButton() {
  const uint32_t DEBOUNCE_MS = 25, DOUBLE_MS = 300, LONG_MS = 700;
  static bool down = false, longSent = false;
  static uint32_t changedAt = 0, pressedAt = 0, releasedAt = 0;
  static int clicks = 0;
  uint32_t now = millis();
  bool pressed = digitalRead(PULSAR_BOOT_PIN) == LOW;
  if (pressed != down && since(now, changedAt) >= DEBOUNCE_MS) {
    down = pressed;
    changedAt = now;
    if (down) {
      pressedAt = now;
      longSent = false;
    } else if (!longSent) {
      clicks++;
      releasedAt = now;
      if (clicks == 2) {
        clicks = 0;
        return DOUBLE;
      }
    }
  }
  if (down && !longSent && since(now, pressedAt) >= LONG_MS) {
    longSent = true;
    clicks = 0;
    return LONG;
  }
  if (clicks == 1 && !down && since(now, releasedAt) >= DOUBLE_MS) {
    clicks = 0;
    return CLICK;
  }
  return NONE;
}

// ---- setup / loop -------------------------------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  uint32_t waitedAt = millis();
  while (!Serial && since(millis(), waitedAt) < 3000) delay(10);  // USB serial: wait for the monitor

  pinMode(PULSAR_BOOT_PIN, INPUT_PULLUP);

  // The Connection: the hostname goes in before the radio starts, so mDNS answers to bench.local.
  WiFi.onEvent(onWifiEvent);
  WiFi.setHostname(HOSTNAME);
  WiFi.mode(WIFI_STA);
  WiFi.setBandMode(WIFI_BAND_MODE_AUTO);  // 2.4 and 5 GHz
  savedSsid = nvsGet("mpwifi", "ssid");
  savedPassword = nvsGet("mpwifi", "pass");
  utcOffset = nvsGet("mpwifi", "tz", "0").toInt();
  startedAt = failedAt = ntpAt = toldAt = millis();
  if (savedSsid.length() == 0) {
    Serial.println("No saved network: upload scan_join (W1) and join one there first.");
    fatal = true;
    return;
  }
  Serial.printf("Saved network found: '%s'\n", savedSsid.c_str());
  connectNow();

  loadPlace();
  Serial.printf("wifi_w3_oracle: %s\n", place.valid ? ("city " + place.name).c_str() : "no city yet: type city <name>");
  showHelp();
}

void loop() {
  if (fatal) {
    delay(100);
    return;
  }

  String line;
  if (consoleLine(line)) handle(line);

  Gesture gesture = pollButton();
  if (gesture == CLICK) setView(view + 1);
  else if (gesture == DOUBLE) view == VIEW_ORACLE ? ask("") : fetchWeather("BOOT");
  else if (gesture == LONG) ask("");

  TickEvent event = connectionTick();
  if (event == TICK_CONNECTED) {
    Serial.println("+ " + describeLink());
    if (place.valid) fetchWeather("joined");
  } else if (event == TICK_DROPPED) {
    Serial.println("- disconnected: " + reasonHint(reason));
  } else if (event == TICK_FAILED) {
    Serial.printf("! could not join '%s': %s\n", savedSsid.c_str(), reasonHint(reason).c_str());
  }

  if (place.valid && joined() && weatherTried) {
    uint32_t wait = weather.valid ? WEATHER_EVERY_MS : RETRY_MS;
    if (since(millis(), weatherTriedAt) >= wait) fetchWeather("timer");
  }
  ledWifi();
  delay(20);
}
