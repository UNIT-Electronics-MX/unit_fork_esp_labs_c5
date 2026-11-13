/*
 * W1 Scan & join: the Pulsar C5 lists every Wi-Fi network around it, 2.4 and 5 GHz, then joins yours.
 * Arduino port of examples/micropython/pulsar-c5/wifi_w1_scan.py (and the parts of wifi_kit.py it uses)
 *
 * Board: ESP32C5 Dev Module, core arduino-esp32 3.3.x. No extra libraries: WiFi, ESPmDNS and
 * Preferences come with the core. Serial Monitor at 115200, "Both NL & CR". Type help.
 *
 * Pick your network from the scan table with join <#> and type the password on the next line
 * (or join <#> <password>, or join <network name> for one the table lacks): it is saved in NVS
 * on the chip (Preferences, namespace "mpwifi"), never in this sketch. Once joined: internet time,
 * bench.local and a live signal chart. The saved network is shared with W2 and W3.
 *
 * Views: networks | channels | status | signal
 * BOOT: click = next view, double-click = scan now, long press = auto-scan on/off
 * The LED: blue while scanning, amber blink while joining, green when joined, red after a failure.
 */

#include <ESPmDNS.h>
#include <Preferences.h>
#include <WiFi.h>
#include <fcntl.h>
#include <lwip/netdb.h>
#include <lwip/sockets.h>
#include <math.h>
#include <string.h>
#include <time.h>

// ---- Settings (same as the MicroPython script) ---------------------------------------------

// True: erase the saved network when the sketch starts. Upload again with False to set it back.
static const bool FORGET_SAVED = false;

static const int PULSAR_BOOT_PIN = 28;
static const int PULSAR_RGB_PIN = 0;  // Some UNIT illustrations label it GPIO8: try 8 if the LED stays dark.
static const char *HOSTNAME = "bench";  // the board answers to bench.local
static const char *NVS_NAMESPACE = "mpwifi";

// ---- Types first: the Arduino builder puts function prototypes before the first function ------

enum Gesture { NONE = 0, CLICK = 1, DOUBLE = 2, LONG = 3 };
enum LinkState { NO_CREDENTIALS, CONNECTING, CONNECTED, FAILED };
enum TickEvent { TICK_NONE, TICK_CONNECTED, TICK_DROPPED, TICK_FAILED };

static const int AUTH_COUNT = 16;
// ESP-IDF's wifi_auth_mode_t, the fifth item of every scan result.
static const char *const AUTH_NAMES[AUTH_COUNT] = {
  "open", "WEP", "WPA", "WPA2", "WPA/WPA2", "WPA2-Ent", "WPA3", "WPA2/WPA3", "WAPI",
  "OWE", "WPA3-Ent", "WPA3-SAE", "WPA3-SAE", "DPP", "WPA3-Ent", "WPA2/3-Ent"};
static const int AUTH_OPEN = 0;
static const char *const SPARKS[8] = {"▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"};
static const char *const VIEWS[4] = {"networks", "channels", "status", "signal"};

struct Net {
  String ssid;  // empty when the network is hidden
  String bssid;
  int channel;
  int rssi;
  int auth;
};

static const int MAX_NETS = 64;
static const int HISTORY = 60;  // seconds of signal history

static const uint32_t JOIN_TIMEOUT_MS = 20000;
static const uint32_t RETRY_MS = 30000;
static const int ATTEMPTS = 3;  // the driver's own retries per join; after them we know why it failed
static const uint32_t AUTO_SCAN_MS = 10000;         // pause between automatic scans while not joined
static const uint32_t AUTO_SCAN_JOINED_MS = 60000;  // every scan takes the radio off the router's channel: less often

// ---- State -----------------------------------------------------------------------------------------

static Preferences prefs;

// Scanner
static Net nets[MAX_NETS];
static int netCount = 0;
static uint32_t scans = 0, lastScanMs = 0, finishedAt = 0;

// Connection
static String savedSsid, savedPassword;
static int utcOffset = 0;  // minutes east of UTC
static LinkState state = NO_CREDENTIALS;
static int reason = 0;
static int history[HISTORY];
static int historyCount = 0;
static uint32_t startedAt = 0, failedAt = 0, sampledAt = 0, ntpAt = 0, toldAt = 0;
static bool mdnsStarted = false;
static volatile int lastReason = 0;  // the driver's last disconnect reason (event task)
static volatile int attempts = 0;    // disconnects seen while joining (event task)

// ScanLab
static Net listed[MAX_NETS];
static int listedCount = 0;
static bool pendingPassword = false;  // set by join: the next console line is the password
static String pendingSsid;
static bool autoScan = true;
static int view = 0;
static bool stopped = false;

static uint32_t since(uint32_t now, uint32_t then) {  // ticks_diff
  return now - then;
}

// ---- Plain-language helpers -------------------------------------------------------------------

static const char *authName(int auth) {
  return auth >= 0 && auth < AUTH_COUNT ? AUTH_NAMES[auth] : "other";
}

// The C5 has two radios' worth of channels: 1-14 are 2.4 GHz, 36 and up are 5 GHz.
static const char *band(int channel) {
  return channel > 14 ? "5 GHz" : "2.4 GHz";
}

// 1-4 bars, the same rough scale phones use.
static int signalBars(int rssi) {
  if (rssi >= -55) return 4;
  if (rssi >= -67) return 3;
  if (rssi >= -78) return 2;
  return 1;
}

static String barsText(int rssi) {
  int bars = signalBars(rssi);
  String text;
  for (int i = 0; i < 4; i++) text += i < bars ? "█" : "·";
  return text;
}

// One character per value, from ▁ (lowest) to █ (highest).
static String sparkline(const int *values, int count, int low, int high) {
  String text;
  int span = max(high - low, 1);
  for (int i = 0; i < count; i++) {
    int index = (int)((values[i] - low) * 7.0f / span + 0.5f);
    text += SPARKS[min(7, max(0, index))];
  }
  return text;
}

static String macText(const uint8_t *address) {
  char text[24];
  snprintf(text, sizeof(text), "%02X:%02X:%02X:%02X:%02X:%02X", address[0], address[1], address[2], address[3],
           address[4], address[5]);
  return String(text);
}

// The common disconnect reasons (ESP-IDF wifi_err_reason_t).
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

// '+1', '-6', '5:30', '-3:30' -> minutes east of UTC, or false.
static bool parseOffset(String text, int &minutes) {
  text.trim();
  text.toUpperCase();
  text.replace("UTC", "");
  int sign = text.startsWith("-") ? -1 : 1;
  while (text.startsWith("+") || text.startsWith("-")) text.remove(0, 1);
  int colon = text.indexOf(':');
  String hours = colon < 0 ? text : text.substring(0, colon);
  String mins = colon < 0 ? "" : text.substring(colon + 1);
  if (hours.length() == 0) return false;
  for (size_t i = 0; i < hours.length(); i++)
    if (!isdigit(hours[i])) return false;
  for (size_t i = 0; i < mins.length(); i++)
    if (!isdigit(mins[i])) return false;
  int total = hours.toInt() * 60 + (mins.length() ? mins.toInt() : 0);
  if (total > 14 * 60) return false;
  minutes = sign * total;
  return true;
}

static bool isNumber(const String &text) {
  if (text.length() == 0) return false;
  for (size_t i = 0; i < text.length(); i++)
    if (!isdigit(text[i])) return false;
  return true;
}

// ---- Internet time ---------------------------------------------------------------------------------

// Internet time (NTP): one small UDP request, then the chip's clock keeps counting.
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

// Short text values in NVS, stored as blobs like MicroPython's esp32.NVS.set_blob: a network saved
// by the MicroPython labs is read here, and the other way round.
static String settingGet(const char *key, const char *fallback = "") {
  size_t length = prefs.getBytesLength(key);
  if (length == 0 || length > 512) return fallback;
  uint8_t buffer[513];
  prefs.getBytes(key, buffer, length);
  buffer[length] = 0;
  return String((char *)buffer);
}

// Save a value; an empty one removes the key.
static void settingSet(const char *key, const String &value) {
  if (value.length()) prefs.putBytes(key, value.c_str(), value.length());
  else prefs.remove(key);
}

// ---- Connection: join the saved network and keep it --------------------------------------------------

// The driver's disconnect reasons only arrive as events.
static void onWifiEvent(arduino_event_id_t event, arduino_event_info_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    lastReason = info.wifi_sta_disconnected.reason;
    if (lastReason != 8 && state == CONNECTING) attempts = attempts + 1;  // 8: we left on purpose
  }
}

static void disconnectWifi() {
  WiFi.disconnect(false);
}

static void connectNow() {
  if (savedSsid.length() == 0) return;
  disconnectWifi();
  reason = 0;
  attempts = 0;
  startedAt = toldAt = millis();
  WiFi.setAutoReconnect(true);
  wl_status_t status = savedPassword.length() ? WiFi.begin(savedSsid.c_str(), savedPassword.c_str())
                                              : WiFi.begin(savedSsid.c_str());
  if (status == WL_CONNECT_FAILED) {
    // e.g. the radio is still busy: retry later
    Serial.println("! could not start joining");
    state = FAILED;
    failedAt = millis();
    return;
  }
  state = CONNECTING;
  Serial.printf("Joining '%s' (%u password characters)...\n", savedSsid.c_str(), (unsigned)savedPassword.length());
}

static void saveNetwork(const String &ssid, const String &password) {
  settingSet("ssid", ssid);
  settingSet("pass", password);
  savedSsid = ssid;
  savedPassword = password;
  Serial.printf("Saved '%s' (password: %u characters, not shown)\n", ssid.c_str(), (unsigned)password.length());
  connectNow();
}

static void forgetNetwork() {
  settingSet("ssid", "");
  settingSet("pass", "");
  disconnectWifi();
  savedSsid = savedPassword = "";
  state = NO_CREDENTIALS;
  Serial.println("Saved network erased from NVS");
}

static void setUtcOffset(int minutes) {
  utcOffset = minutes;
  settingSet("tz", String(minutes));
  Serial.println("Time zone: UTC" + formatOffset(minutes));
}

static int joiningFor() {
  return since(millis(), startedAt) / 1000;
}

static bool joined() {
  return state == CONNECTED;
}

// Call from loop(). Returns the event once when it happens, otherwise TICK_NONE.
static TickEvent connectionTick() {
  uint32_t now = millis();
  bool isJoined = WiFi.status() == WL_CONNECTED;
  if (isJoined && state != CONNECTED) {
    state = CONNECTED;
    historyCount = 0;
    // Power save lets the radio doze between router beacons and miss requests.
    // Fine for a sensor that only sends; a server must stay awake to be reachable.
    WiFi.setSleep(false);
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
    // The driver's own retries run out after ATTEMPTS: only then do we know why it failed.
    bool gaveUp = attempts >= ATTEMPTS;
    if (gaveUp) reason = lastReason;
    if (since(now, toldAt) >= 3000) {
      toldAt = now;
      Serial.printf("  ...%d s, %s\n", joiningFor(), gaveUp ? reasonHint(reason).c_str() : "still trying");
    }
    if (gaveUp || since(now, startedAt) > JOIN_TIMEOUT_MS) {
      state = FAILED;
      failedAt = now;
      disconnectWifi();
      return TICK_FAILED;
    }
  }
  if (state == FAILED && since(now, failedAt) > RETRY_MS) connectNow();
  if (state == CONNECTED) {
    if (since(now, sampledAt) >= 1000) {
      sampledAt = now;
      if (historyCount == HISTORY) {
        memmove(history, history + 1, (HISTORY - 1) * sizeof(int));
        historyCount--;
      }
      history[historyCount++] = WiFi.RSSI();
    }
    if (!timeSynced() && since(now, ntpAt) > 30000) syncTime();
  }
  return TICK_NONE;
}

static String describe() {
  switch (state) {
    case NO_CREDENTIALS: return "Not joined. Scan, then: join <#>";
    case CONNECTING: return "Joining '" + savedSsid + "' (" + String(joiningFor()) + " s)...";
    case FAILED:
      return "Could not join '" + savedSsid + "': " + reasonHint(reason) + ". Retrying every " +
             String(RETRY_MS / 1000) + " s.";
    default: break;
  }
  int channel = WiFi.channel();
  String clock = localTime();
  return "Joined '" + savedSsid + "' | IP " + WiFi.localIP().toString() + " | " + HOSTNAME + ".local | ch " +
         String(channel) + " (" + band(channel) + ") | " + String(WiFi.RSSI()) + " dBm | time " +
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

static void cmdReach(const String &argument) {
  if (!joined()) {
    Serial.println("reach needs a network: join one first");
    return;
  }
  String host = WiFi.gatewayIP().toString();
  int port = 80;
  String text = argument;
  text.trim();
  if (text.length()) {
    int space = text.indexOf(' ');
    host = space < 0 ? text : text.substring(0, space);
    if (space >= 0) {
      String second = text.substring(space + 1);
      second.trim();
      int end = second.indexOf(' ');
      if (end >= 0) second = second.substring(0, end);
      if (isNumber(second)) port = second.toInt();
    }
  }
  for (int attempt = 1; attempt < 5; attempt++) {
    String how;
    int ms = reachOnce(host.c_str(), port, 2000, how);
    if (ms < 0) Serial.printf("  %s %d: %s\n", host.c_str(), attempt, how.c_str());
    else Serial.printf("  %s %d: %d ms, %s\n", host.c_str(), attempt, ms, how.c_str());
    delay(300);
  }
}

// ---- Scanning --------------------------------------------------------------------------------------

// Hops through every channel, 2.4 and 5 GHz, and collects the routers' answers. Blocks for two to
// five seconds: the console and BOOT wait until it is done.
// Returns true with fresh results, false when the radio was busy.
static bool scanNetworks() {
  uint32_t started = millis();
  int found = WiFi.scanNetworks(false, true);  // blocking, hidden networks included
  if (found < 0) {
    Serial.printf("! scan failed (%d), will retry\n", found);
    finishedAt = millis();
    return false;
  }
  lastScanMs = since(millis(), started);
  finishedAt = millis();
  scans++;
  netCount = 0;
  for (int i = 0; i < found && netCount < MAX_NETS; i++) {
    Net net;
    net.ssid = WiFi.SSID(i);
    uint8_t bssid[6] = {};
    WiFi.BSSID(i, bssid);
    net.bssid = macText(bssid);
    net.channel = WiFi.channel(i);
    net.rssi = WiFi.RSSI(i);
    net.auth = WiFi.encryptionType(i);
    int at = netCount++;
    while (at > 0 && nets[at - 1].rssi < net.rssi) {  // strongest first, stable
      nets[at] = nets[at - 1];
      at--;
    }
    nets[at] = net;
  }
  WiFi.scanDelete();
  return true;
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

// Blue while scanning, amber blink while joining, green when joined, red after a failure.
static void ledWifi(bool scanningNow = false) {
  bool blink = (millis() / 400) % 2 == 0;
  if (scanningNow) showLed(0, 0, 255);
  else if (state == CONNECTED) showLed(0, 255, 0);
  else if (state == CONNECTING) blink ? showLed(255, 140, 0) : showLed(40, 20, 0);
  else if (state == FAILED) showLed(255, 0, 0);
  else showLed(0, 0, 0);
}

// ---- The views -----------------------------------------------------------------------------------------

static void showNetworks() {
  listedCount = netCount;
  for (int i = 0; i < netCount; i++) listed[i] = nets[i];
  Serial.printf("\n--- scan %lu: %d networks in %lu ms ---\n", (unsigned long)scans, netCount,
                (unsigned long)lastScanMs);
  Serial.println(" #   dBm  bars   ch  band     security    bssid              ssid");
  for (int i = 0; i < netCount; i++) {
    const Net &n = nets[i];
    Serial.printf("%2d  %4d  %s  %3d  %-7s  %-10s  %s  %s\n", i + 1, n.rssi, barsText(n.rssi).c_str(), n.channel,
                  band(n.channel), authName(n.auth), n.bssid.c_str(), n.ssid.length() ? n.ssid.c_str() : "(hidden)");
  }
  Serial.println("To join one: join <#>");
}

// How crowded each channel is. 2.4 GHz channels overlap: only 1, 6 and 11 stay clear of each other.
// 5 GHz has many more channels and they do not overlap.
static void showChannels() {
  static int count[256];
  static int strongest[256];
  memset(count, 0, sizeof(count));
  for (int &value : strongest) value = -127;
  for (int i = 0; i < netCount; i++) {
    int channel = nets[i].channel & 0xFF;
    count[channel]++;
    strongest[channel] = max(strongest[channel], nets[i].rssi);
  }
  int ours = joined() ? WiFi.channel() : -1;
  for (int pass = 0; pass < 2; pass++) {
    Serial.printf("\n%s  (networks, strongest)\n", pass == 0 ? "2.4 GHz" : "5 GHz");
    int first = pass == 0 ? 1 : 15, last = pass == 0 ? 13 : 255;
    bool any = pass == 0;  // the 2.4 GHz table always lists channels 1-13
    for (int channel = first; channel <= last && !any; channel++)
      if (count[channel]) any = true;
    if (!any) Serial.println("  none heard");
    for (int channel = first; channel <= last && any; channel++) {
      if (pass == 1 && !count[channel]) continue;
      int n = count[channel];
      String bars;
      for (int i = 0; i < 12; i++) bars += i < min(n, 12) ? "█" : " ";
      String strong = n ? " " + String(strongest[channel]) + " dBm" : "";
      Serial.printf("  ch %3d %s %s%s%s\n", channel, bars.c_str(), n ? String(n).c_str() : "", strong.c_str(),
                    channel == ours ? " <- you" : "");
    }
  }
}

static void showStatus() {
  Serial.println(describe());
  if (joined()) {
    uint8_t mac[6];
    WiFi.macAddress(mac);
    Serial.printf("Gateway %s | MAC %s | time zone UTC%s\n", WiFi.gatewayIP().toString().c_str(),
                  macText(mac).c_str(), formatOffset(utcOffset).c_str());
    Serial.printf("Reachable as %s.local: try ping %s.local from your computer\n", HOSTNAME, HOSTNAME);
  }
}

static void showSignal() {
  if (historyCount == 0) {
    Serial.println("No signal history yet: it fills once a second while joined");
    return;
  }
  int low = history[0], high = history[0];
  for (int i = 0; i < historyCount; i++) {
    low = min(low, history[i]);
    high = max(high, history[i]);
  }
  Serial.printf("Signal, last %d s: %s  now %d dBm, min %d, max %d, %d bars\n", historyCount,
                sparkline(history, historyCount, -90, -30).c_str(), history[historyCount - 1], low, high,
                signalBars(history[historyCount - 1]));
}

static void setView(int number) {
  view = ((number % 4) + 4) % 4;
  Serial.printf("view %d: %s\n", view + 1, VIEWS[view]);
  switch (view) {
    case 0: showNetworks(); break;
    case 1: showChannels(); break;
    case 2: showStatus(); break;
    default: showSignal(); break;
  }
}

// ---- Scanning from the console ------------------------------------------------------------------------------

// quiet: an automatic scan only says what is new, so the table you read stays put.
static void scanNow(const char *why, bool quiet = false) {
  if (state == CONNECTING) {
    Serial.println("Not now: the radio must stay on the router's channel while joining");
    return;
  }
  if (!quiet) Serial.printf("scanning (%s)...\n", why);
  ledWifi(true);
  if (!scanNetworks()) return;
  if (!quiet || listedCount == 0) {
    showNetworks();
    return;
  }
  String added;
  for (int i = 0; i < netCount; i++) {
    if (nets[i].ssid.length() == 0) continue;
    bool known = false;
    for (int k = 0; k < listedCount; k++)
      if (listed[k].ssid == nets[i].ssid) known = true;
    bool already = added.indexOf("\x01" + nets[i].ssid + "\x01") >= 0;
    if (!known && !already) added += "\x01" + nets[i].ssid + "\x01";
  }
  if (added.length()) {
    // the new names, alphabetically, the way the MicroPython script prints them
    String names[MAX_NETS];
    int count = 0;
    int start = 0;
    while (start < (int)added.length() && count < MAX_NETS) {
      int end = added.indexOf('\x01', start + 1);
      names[count++] = added.substring(start + 1, end);
      start = end + 1;
    }
    for (int i = 1; i < count; i++)
      for (int k = i; k > 0 && names[k] < names[k - 1]; k--) {
        String swap = names[k];
        names[k] = names[k - 1];
        names[k - 1] = swap;
      }
    String list;
    for (int i = 0; i < count; i++) list += (i ? ", " : "") + names[i];
    Serial.printf("scan %lu: new %s (type networks for a fresh table)\n", (unsigned long)scans, list.c_str());
  }
}

static void setAuto(bool on) {
  autoScan = on;
  Serial.println(on ? "auto-scan on" : "auto-scan off");
}

static bool autoScanDue() {
  if (!autoScan || pendingPassword || state == CONNECTING) return false;
  uint32_t interval = joined() ? AUTO_SCAN_JOINED_MS : AUTO_SCAN_MS;
  return since(millis(), finishedAt) >= interval;
}

// ---- Joining from the console -----------------------------------------------------------------------------------

static void askPassword(const String &ssid, bool byName) {
  pendingPassword = true;
  pendingSsid = ssid;
  if (byName)
    Serial.printf("Password for '%s' on the next line (empty line for an open network).\n", ssid.c_str());
  else
    Serial.println("Password on the next line. It is stored on the chip only;\n"
                   "the serial monitor still lists what you send, so don't share that screen.");
}

static bool joinWith(const String &ssid, const String &password) {
  // WPA2/WPA3 passwords are 8 to 63 characters: anything else can never work.
  if (password.length() && (password.length() < 8 || password.length() > 63)) {
    Serial.printf("! %u characters: Wi-Fi passwords have 8 to 63. Type join again.\n", (unsigned)password.length());
    return false;
  }
  saveNetwork(ssid, password);
  return true;
}

// join <#> | join <#> <password> | join <network name>
static void startJoin(const String &argument) {
  int space = argument.indexOf(' ');
  String number = space < 0 ? argument : argument.substring(0, space);
  String password = space < 0 ? "" : argument.substring(space + 1);
  if (!isNumber(number)) {
    if (argument.length() == 0) {
      Serial.println("join <#> from the table, join <#> <password>, or join <network name>");
      return;
    }
    // A name: for a hidden network, or one the table doesn't show. The password comes next.
    askPassword(argument, true);
    return;
  }
  int n = number.toInt();
  if (n < 1 || n > listedCount) {
    Serial.printf("join needs a number from the table above (1-%d); type networks to see it\n", listedCount);
    return;
  }
  const Net &chosen = listed[n - 1];
  if (chosen.ssid.length() == 0) {
    Serial.println("That network is hidden: its name is unknown. Use join <network name>");
    return;
  }
  Serial.printf("#%d: '%s', %s, channel %d (%s), %d dBm\n", n, chosen.ssid.c_str(), authName(chosen.auth),
                chosen.channel, band(chosen.channel), chosen.rssi);
  if (chosen.auth == AUTH_OPEN) {
    Serial.println("It is open, joining without a password");
    saveNetwork(chosen.ssid, "");
  } else if (password.length()) {
    joinWith(chosen.ssid, password);
  } else {
    askPassword(chosen.ssid, false);
  }
}

// ---- The serial console, without blocking the radio ---------------------------------------------------------------

static bool secretNext = false;  // the next line is a password: never echoed, never trimmed

// 'join 3 secret' echoes as 'join 3 (password, 6 characters)'.
static String hideJoinPassword(const String &text) {
  int space = text.indexOf(' ');
  if (space < 0) return text;
  String head = text.substring(0, space);
  head.toLowerCase();
  String rest = text.substring(space + 1);
  int second = rest.indexOf(' ');
  if (second < 0) return text;
  String number = rest.substring(0, second), password = rest.substring(second + 1);
  if (head == "join" && isNumber(number) && password.length())
    return "join " + number + " (password, " + String(password.length()) + " characters)";
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
      if (secretNext) {
        // A password is taken exactly as typed: no trimming, no lower-casing.
        out = buffer;
        buffer = "";
        secretNext = false;
        Serial.printf("> (password, %u characters, not shown)\n", (unsigned)out.length());
        return true;
      }
      String text = buffer;
      text.trim();
      buffer = "";
      if (text.length()) {
        out = text;
        Serial.println("> " + hideJoinPassword(text));
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

// ---- Commands -------------------------------------------------------------------------------------------------------

static void showHelp() {
  Serial.println();
  Serial.println("Commands: scan | view <1-4> | networks | channels | status | signal | auto on|off");
  Serial.println("          join <#> [password] | join <network name> | forget | reconnect");
  Serial.println("          tz <+1 | -6 | 5:30> | reach [ip] [port] | stop | help");
  Serial.println("BOOT: click = next view, double-click = scan now, long press = auto-scan on/off");
}

// The commands W1 and W2 share. Returns false for a command it doesn't know.
static bool labHandle(const String &command, const String &argument) {
  if (command == "scan") {
    scanNow("console");
  } else if (command == "auto" && (argument == "on" || argument == "off")) {
    setAuto(argument == "on");
  } else if (command == "join") {
    startJoin(argument);
  } else if (command == "forget") {
    forgetNetwork();
  } else if (command == "reconnect") {
    connectNow();
  } else if (command == "tz") {
    int minutes;
    if (!parseOffset(argument, minutes)) Serial.println("tz wants hours from UTC: tz -6, tz +1, tz 5:30");
    else setUtcOffset(minutes);
  } else if (command == "reach" || command == "ping") {
    cmdReach(argument);
  } else {
    return false;
  }
  return true;
}

static void handle(const String &text) {
  if (pendingPassword) {  // the password a join asked for
    pendingPassword = false;
    joinWith(pendingSsid, text);
    return;
  }
  int space = text.indexOf(' ');
  String command = space < 0 ? text : text.substring(0, space);
  String argument = space < 0 ? "" : text.substring(space + 1);
  command.toLowerCase();
  argument.trim();

  int viewIndex = -1;
  for (int i = 0; i < 4; i++)
    if (command == VIEWS[i]) viewIndex = i;
  if (viewIndex >= 0) {
    setView(viewIndex);
  } else if (command == "view" && isNumber(argument) && argument.toInt() >= 1 && argument.toInt() <= 4) {
    setView(argument.toInt() - 1);
  } else if (command == "stop" || command == "exit" || command == "quit") {
    // A sketch cannot exit like a script: it goes quiet. RESET runs it again.
    showLed(0, 0, 0);
    Serial.println("Stopped (RESET runs it again). Wi-Fi stays joined, so W2 and W3 use the same saved network.");
    stopped = true;
  } else if (command == "help" || command == "?") {
    showHelp();
  } else if (!labHandle(command, argument)) {
    Serial.println("unknown command '" + text + "' (type help)");
  }
}

// ---- The BOOT button: click, double-click, long press -----------------------------------------------------------------

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

// ---- setup / loop -------------------------------------------------------------------------------------------------------

// Prints what connectionTick() returned. True when it was a fresh connection.
static bool report(TickEvent event) {
  if (event == TICK_CONNECTED) {
    Serial.println("+ joined");
    return true;
  }
  if (event == TICK_DROPPED) {
    Serial.println("- disconnected: " + reasonHint(reason));
  } else if (event == TICK_FAILED) {
    Serial.printf("! could not join '%s': %s. Retrying in %lu s; check it with networks, or forget.\n",
                  savedSsid.c_str(), reason ? reasonHint(reason).c_str() : "no answer in time",
                  (unsigned long)(RETRY_MS / 1000));
  }
  return false;
}

void setup() {
  Serial.begin(115200);
  uint32_t waitedAt = millis();
  while (!Serial && since(millis(), waitedAt) < 3000) delay(10);  // USB serial: wait for the monitor

  pinMode(PULSAR_BOOT_PIN, INPUT_PULLUP);
  prefs.begin(NVS_NAMESPACE, false);
  if (FORGET_SAVED) {
    settingSet("ssid", "");
    settingSet("pass", "");
    Serial.println("FORGET_SAVED: saved network erased. Set it back to false before the next upload.");
  }

  // The Connection: the hostname goes in before the radio starts, so mDNS answers to bench.local.
  WiFi.onEvent(onWifiEvent);
  WiFi.setHostname(HOSTNAME);
  WiFi.mode(WIFI_STA);
  WiFi.setBandMode(WIFI_BAND_MODE_AUTO);  // 2.4 and 5 GHz
  savedSsid = settingGet("ssid");
  savedPassword = settingGet("pass");
  utcOffset = settingGet("tz", "0").toInt();
  startedAt = failedAt = sampledAt = ntpAt = toldAt = finishedAt = millis();
  if (savedSsid.length() == 0) {
    Serial.println("No saved network yet. Scan, then: join <#>");
  } else {
    Serial.printf("Saved network found: '%s'\n", savedSsid.c_str());
    connectNow();
  }

  uint8_t mac[6];
  WiFi.macAddress(mac);
  Serial.printf("W1 Scan & join: MAC %s. Radio: 2.4 and 5 GHz.\n", macText(mac).c_str());
  showHelp();
  if (state != CONNECTING) scanNow("start-up");
}

void loop() {
  if (stopped) {
    delay(100);
    return;
  }

  String line;
  if (consoleLine(line)) {
    if (secretNext) secretNext = false;
    handle(line);
  }
  if (pendingPassword) secretNext = true;  // the join asked for a password: the next line is not echoed

  Gesture gesture = pollButton();
  if (gesture == CLICK) setView(view + 1);
  else if (gesture == DOUBLE) scanNow("BOOT");
  else if (gesture == LONG) setAuto(!autoScan);

  if (report(connectionTick())) setView(2);  // the status view
  if (autoScanDue()) scanNow("auto", true);
  ledWifi();
  delay(20);
}
