/*
 * B2 Be seen: the Pulsar C5 as a beacon. Live values ride in the advertisement.
 * Arduino port of examples/micropython/pulsar-c5/ble_b2_beacon.py
 *
 * Board: ESP32C5 Dev Module, core arduino-esp32 3.3.x. No extra libraries: the BLE
 * library bundled with the core (NimBLE inside). Serial Monitor at 115200, "Both NL & CR".
 * Type help in the serial console.
 *
 * BOOT: click = next mode, double-click = press (the counter in the packet goes up),
 * long press = advertising on/off. Scan with nRF Connect or LightBlue on a phone.
 * The LED blinks blue while advertising, green while a phone is connected.
 *
 * Like ble_listen, it starts the stack with BLEDevice::init() and then talks to NimBLE's
 * ble_gap_adv_* directly: the lab needs to hand the controller the exact bytes of both packets.
 */

#include <BLEDevice.h>
#include <host/ble_gap.h>
#include <host/ble_hs.h>
#include <math.h>
#include <string.h>

// ---- Settings (same as the MicroPython script) ---------------------------------------------

static const char *DEVICE_NAME = "Bench B2";
static const uint16_t TEST_COMPANY = 0xFFFF;  // reserved by the Bluetooth SIG for tests: ours to use
static const uint8_t BENCH_VERSION = 1;
// The iBeacon "fleet" UUID: the Bench service's UUID ba5e0000-b1e5-4ab1-9e5c-be7cbe7cb0a7,
// written most significant byte first, the way iBeacon scanners read it. Major and minor pick one
// beacon in it.
static const uint8_t IBEACON_UUID[16] = {0xBA, 0x5E, 0x00, 0x00, 0xB1, 0xE5, 0x4A, 0xB1,
                                         0x9E, 0x5C, 0xBE, 0x7C, 0xBE, 0x7C, 0xB0, 0xA7};

static const int PULSAR_BOOT_PIN = 28;
static const int PULSAR_RGB_PIN = 0;  // Some UNIT illustrations label it GPIO8: try 8 if the LED stays dark.

// ---- Types first: the Arduino builder puts function prototypes before the first function ------

enum Mode { MODE_BENCH = 0, MODE_IBEACON, MODE_URL, MODE_NAME, MODE_COUNT };
static const char *const MODES[MODE_COUNT] = {"bench", "ibeacon", "url", "name"};

static const char *const URL_SCHEMES[] = {"http://www.", "https://www.", "http://", "https://"};
static const char *const URL_ENDINGS[] = {".com/", ".org/", ".edu/", ".net/", ".info/", ".biz/", ".gov/",
                                          ".com", ".org", ".edu", ".net", ".info", ".biz", ".gov"};

struct Settings {
  int mode;
  String url;
  long major, minor;
  int power;  // RSSI a phone should measure at 1 m
  long interval;
  bool connectable;
  bool scanresp;
  bool on;
};

// One packet being filled: [length][type][data...] structures. Checked against 31 bytes at the end.
struct Packet {
  uint8_t b[64];
  size_t n;
  void reset() { n = 0; }
  void add(uint8_t kind, const void *data, size_t length) {
    if (n + 2 + length > sizeof(b)) return;
    b[n++] = length + 1;
    b[n++] = kind;
    memcpy(b + n, data, length);
    n += length;
  }
};

struct AdField {
  uint8_t kind;
  const uint8_t *data;
  size_t length;  // data bytes, without the type byte
};

struct AdName {
  uint8_t kind;
  const char *name;
};
static const AdName AD_NAMES[] = {
  {0x01, "Flags"}, {0x02, "16-bit UUIDs (part)"}, {0x03, "16-bit UUIDs"},
  {0x06, "128-bit UUIDs (part)"}, {0x07, "128-bit UUIDs"}, {0x08, "Short name"}, {0x09, "Name"},
  {0x0A, "TX power"}, {0x16, "Service data"}, {0x19, "Appearance"}, {0xFF, "Manufacturer data"},
};

static const uint8_t AD_FLAGS = 0x01;
static const uint8_t AD_UUID16_ALL = 0x03;
static const uint8_t AD_NAME = 0x09;
static const uint8_t AD_TX_POWER = 0x0A;
static const uint8_t AD_SERVICE_DATA16 = 0x16;
static const uint8_t AD_APPEARANCE = 0x19;
static const uint8_t AD_MANUFACTURER = 0xFF;

enum Gesture { NONE = 0, CLICK = 1, DOUBLE = 2, LONG = 3 };

// ---- State -----------------------------------------------------------------------------------------

static Settings settings = {MODE_BENCH, "https://espressif.com", 1, 2, -59, 100, false, true, true};
static uint16_t presses = 0;
static bool advertising = false;
static uint8_t ownType = BLE_OWN_ADDR_PUBLIC;

// Connection events come from the NimBLE host task; loop() prints and reacts (no Serial from there).
static volatile int connHandle = -1;
static volatile uint32_t connectedAt = 0;
static volatile bool evConnect = false, evDisconnect = false;
static volatile uint32_t evLived = 0;
static uint8_t peerAddress[6];
static uint8_t peerType = 0;

// ---- ble_kit pieces: formatting --------------------------------------------------------------------

static uint32_t since(uint32_t now, uint32_t then) {  // ticks_diff
  return now - then;
}

static bool nextField(const uint8_t *payload, size_t size, size_t &i, AdField &field) {
  if (i + 1 >= size) return false;
  size_t length = payload[i];
  if (length == 0 || i + 1 + length > size) return false;
  field.kind = payload[i + 1];
  field.data = payload + i + 2;
  field.length = length - 1;
  i += 1 + length;
  return true;
}

static void hexBytes(char *out, size_t outSize, const uint8_t *data, size_t length, char sep) {
  size_t n = 0;
  for (size_t i = 0; i < length && n + 4 < outSize; i++) {
    if (i && sep) out[n++] = sep;
    n += snprintf(out + n, outSize - n, "%02X", data[i]);
  }
  out[n] = 0;
}

static String mac(const uint8_t *address) {
  char text[24];
  hexBytes(text, sizeof(text), address, 6, ':');
  return String(text);
}

// Public, or which kind of random address (the top two bits of the first byte say).
static const char *addressKind(uint8_t addrType, const uint8_t *address) {
  if (addrType == 0) return "public";
  switch (address[0] >> 6) {
    case 3: return "random static";
    case 1: return "private (RPA)";
    case 0: return "private (NRPA)";
  }
  return "random";
}

// ---- The packets -----------------------------------------------------------------------------------

// Eddystone-URL frame: 0x10, TX power at 0 m, scheme byte, the rest with .com/ as one byte.
// Returns the frame length (at most 20), or 0 when the URL does not fit.
static size_t eddystoneUrl(const String &url, int power, uint8_t *out) {
  const int byLength[4] = {1, 0, 3, 2};  // indexes of URL_SCHEMES, longest scheme first
  for (int code : byLength) {
    if (!url.startsWith(URL_SCHEMES[code])) continue;
    String rest = url.substring(strlen(URL_SCHEMES[code]));
    size_t n = 0;
    out[n++] = 0x10;
    out[n++] = power & 0xFF;
    out[n++] = code;
    size_t i = 0;
    while (i < rest.length()) {
      if (n >= 21) return 0;
      bool matched = false;
      for (size_t e = 0; e < sizeof(URL_ENDINGS) / sizeof(URL_ENDINGS[0]); e++) {
        if (rest.startsWith(URL_ENDINGS[e], i)) {
          out[n++] = e;
          i += strlen(URL_ENDINGS[e]);
          matched = true;
          break;
        }
      }
      if (!matched) out[n++] = (uint8_t)rest[i++];
    }
    return n <= 20 ? n : 0;
  }
  return 0;
}

// Both packets for the current settings; false if the advertisement is too long.
static bool build(Packet &adv, Packet &rsp) {
  adv.reset();
  rsp.reset();
  size_t nameLength = strlen(DEVICE_NAME);
  const uint8_t flags = 0x06;  // LE General Discoverable, BR/EDR (Classic) not supported
  adv.add(AD_FLAGS, &flags, 1);
  switch (settings.mode) {
    case MODE_BENCH: {
      float celsius = temperatureRead();
      if (isnan(celsius)) celsius = 0;
      int16_t centi = (int16_t)(celsius * 100);
      uint16_t uptime = (millis() / 1000) & 0xFFFF;
      // company, version, presses, temperature, uptime: little-endian, like everything in Bluetooth LE
      const uint8_t live[9] = {TEST_COMPANY & 0xFF, TEST_COMPANY >> 8, BENCH_VERSION,
                               (uint8_t)(presses & 0xFF), (uint8_t)(presses >> 8),
                               (uint8_t)(centi & 0xFF), (uint8_t)((uint16_t)centi >> 8),
                               (uint8_t)(uptime & 0xFF), (uint8_t)(uptime >> 8)};
      adv.add(AD_NAME, DEVICE_NAME, nameLength);
      adv.add(AD_MANUFACTURER, live, sizeof(live));
      const uint8_t txPower = 0x09, appearance[2] = {0x40, 0x05};
      rsp.add(AD_TX_POWER, &txPower, 1);
      rsp.add(AD_APPEARANCE, appearance, 2);
      break;
    }
    case MODE_IBEACON: {
      // Apple's layout. Major and minor are big-endian: the one exception in a little-endian packet.
      uint8_t data[25] = {0x4C, 0x00, 0x02, 0x15};
      memcpy(data + 4, IBEACON_UUID, 16);
      data[20] = settings.major >> 8;
      data[21] = settings.major & 0xFF;
      data[22] = settings.minor >> 8;
      data[23] = settings.minor & 0xFF;
      data[24] = (uint8_t)settings.power;
      adv.add(AD_MANUFACTURER, data, sizeof(data));
      rsp.add(AD_NAME, DEVICE_NAME, nameLength);
      break;
    }
    case MODE_URL: {
      // Eddystone wants the power at 0 m: by convention the 1 m reference + 41 dB.
      uint8_t frame[24];
      size_t n = eddystoneUrl(settings.url, settings.power + 41, frame);
      if (n == 0) return false;
      const uint8_t uuid[2] = {0xAA, 0xFE};
      uint8_t service[22];
      memcpy(service, uuid, 2);
      memcpy(service + 2, frame, n);
      adv.add(AD_UUID16_ALL, uuid, 2);
      adv.add(AD_SERVICE_DATA16, service, 2 + n);
      rsp.add(AD_NAME, DEVICE_NAME, nameLength);
      break;
    }
    default: {
      const uint8_t txPower = 0x09;
      adv.add(AD_NAME, DEVICE_NAME, nameLength);
      adv.add(AD_TX_POWER, &txPower, 1);
      break;
    }
  }
  if (adv.n > 31) return false;
  if (!settings.scanresp) rsp.reset();
  return true;
}

// ---- The radio: advertising and connections ----------------------------------------------------------

static int onGap(struct ble_gap_event *event, void *arg) {
  switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
      if (event->connect.status == 0) {
        struct ble_gap_conn_desc desc;
        if (ble_gap_conn_find(event->connect.conn_handle, &desc) == 0) {
          peerType = desc.peer_id_addr.type;
          for (int i = 0; i < 6; i++) peerAddress[i] = desc.peer_id_addr.val[5 - i];
        }
        connectedAt = millis();
        connHandle = event->connect.conn_handle;
        advertising = false;
        evConnect = true;
      }
      break;
    case BLE_GAP_EVENT_DISCONNECT:
      evLived = since(millis(), connectedAt);
      connHandle = -1;
      evDisconnect = true;
      break;
    default: break;
  }
  return 0;
}

static void advertise(const Packet &adv, const Packet &rsp, uint32_t intervalMs, bool connectable) {
  ble_gap_adv_stop();  // harmless when it is not advertising
  ble_gap_adv_set_data(adv.b, adv.n);
  ble_gap_adv_rsp_set_data(rsp.b, rsp.n);
  struct ble_gap_adv_params params = {};
  params.conn_mode = connectable ? BLE_GAP_CONN_MODE_UND : BLE_GAP_CONN_MODE_NON;
  params.disc_mode = BLE_GAP_DISC_MODE_GEN;
  params.itvl_min = params.itvl_max = intervalMs * 1000 / 625;  // units of 0.625 ms
  int rc = ble_gap_adv_start(ownType, nullptr, BLE_HS_FOREVER, &params, onGap, nullptr);
  if (rc != 0 && rc != BLE_HS_EALREADY) {
    Serial.printf("! advertising failed (rc=%d)\n", rc);
    return;
  }
  advertising = true;
}

static void stopAdvertising() {
  ble_gap_adv_stop();
  advertising = false;
}

// ---- Applying settings ---------------------------------------------------------------------------------

static bool apply(bool announce = false) {
  Packet adv, rsp;
  if (!build(adv, rsp)) {
    Serial.println("That does not fit in 31 bytes: http(s)://, then at most 17 bytes (.com/ counts as 1)");
    return false;
  }
  if (!settings.on) return true;
  // Below 100 ms only connectable advertising is allowed.
  uint32_t interval = settings.connectable ? settings.interval : max(100L, settings.interval);
  advertise(adv, rsp, interval, settings.connectable);
  if (announce)
    Serial.printf("+ %s %s every %lu ms, %u+%u B\n", MODES[settings.mode],
                  settings.connectable ? "ADV_IND" : "ADV_NONCONN/SCAN_IND", (unsigned long)interval,
                  (unsigned)adv.n, (unsigned)rsp.n);
  return true;
}

static void printPacket(const char *title, const Packet &packet) {
  Serial.printf("%s: %u of 31 bytes\n", title, (unsigned)packet.n);
  size_t i = 0;
  AdField field;
  while (nextField(packet.b, packet.n, i, field)) {
    char hex[96];
    hexBytes(hex, sizeof(hex), field.data, field.length, ' ');
    const char *name = nullptr;
    for (const AdName &known : AD_NAMES)
      if (known.kind == field.kind) name = known.name;
    char other[16];
    if (!name) {
      snprintf(other, sizeof(other), "type 0x%02X", field.kind);
      name = other;
    }
    Serial.printf("  [%02X %02X | %s] %s\n", (unsigned)(field.length + 1), field.kind, hex, name);
  }
}

static void cmdBytes() {
  Packet adv, rsp;
  if (!build(adv, rsp)) {
    Serial.println("These settings do not fit");
    return;
  }
  printPacket("advertisement", adv);
  printPacket("scan response", rsp);
}

static void press() {
  presses++;
  Serial.printf("press #%u: %s\n", presses,
                settings.mode == MODE_BENCH ? "in the bench packet" : "counted (switch to mode bench to see it)");
  apply();
}

static void setOn(bool on) {
  settings.on = on;
  if (on) {
    apply(true);
  } else {
    stopAdvertising();
    Serial.println("Advertising off: silent");
  }
}

// ---- Commands --------------------------------------------------------------------------------------------

static bool parseInt(const String &text, long &value) {
  String trimmed = text;
  trimmed.trim();
  if (trimmed.length() == 0) return false;
  char *end;
  value = strtol(trimmed.c_str(), &end, 10);
  return *end == 0;
}

// Exactly two integers separated by blanks, like "major, minor = (int(p) for p in rest.split())".
static bool parseTwoInts(const String &text, long &first, long &second) {
  String trimmed = text;
  trimmed.trim();
  int space = trimmed.indexOf(' ');
  if (space < 0) return false;
  String tail = trimmed.substring(space + 1);
  tail.trim();
  return tail.indexOf(' ') < 0 && parseInt(trimmed.substring(0, space), first) && parseInt(tail, second);
}

static void run(const String &text) {
  int space = text.indexOf(' ');
  String command = space < 0 ? text : text.substring(0, space);
  String rest = space < 0 ? "" : text.substring(space + 1);
  command.toLowerCase();
  rest.trim();
  String lower = rest;
  lower.toLowerCase();
  Settings before = settings;

  int modeIndex = -1;
  for (int i = 0; i < MODE_COUNT; i++)
    if (lower == MODES[i]) modeIndex = i;

  if (command == "mode" && modeIndex >= 0) {
    settings.mode = modeIndex;
  } else if (command == "url" && rest.length()) {
    settings.url = rest;
    settings.mode = MODE_URL;
  } else if (command == "beacon") {
    long major, minor;
    if (!parseTwoInts(rest, major, minor) || major < 0 || major > 65535 || minor < 0 || minor > 65535) {
      Serial.println("beacon <major> <minor>, each 0-65535");
      return;
    }
    settings.major = major;
    settings.minor = minor;
    settings.mode = MODE_IBEACON;
  } else if (command == "interval") {
    long ms;
    if (!parseInt(rest, ms) || ms < 20 || ms > 10240) {
      Serial.println("interval <ms>, 20-10240 (100 at least when not connectable)");
      return;
    }
    settings.interval = ms;
  } else if ((command == "connectable" || command == "scanresp") && (lower == "on" || lower == "off")) {
    if (command == "connectable") settings.connectable = lower == "on";
    else settings.scanresp = lower == "on";
  } else if (command == "adv") {
    setOn(lower == "on" || lower == "off" ? lower == "on" : !settings.on);
    return;
  } else if (command == "press") {
    press();
    return;
  } else if (command == "bytes") {
    cmdBytes();
    return;
  } else {
    Serial.println("mode bench|ibeacon|url|name | url <https://...> | beacon <major> <minor> | interval <ms> |"
                   " connectable on|off | scanresp on|off | adv [on|off] | press | bytes");
    return;
  }
  if (!apply(true)) {
    settings = before;
    apply();
  }
}

// ---- ble_kit pieces: console, BOOT button, status LED ---------------------------------------------------

// Collects typed characters and returns one command line at a time, without blocking.
static bool consoleLine(String &out) {
  static String buffer;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r' || c == '\n') {
      buffer.trim();
      if (buffer.length()) {
        out = buffer;
        buffer = "";
        Serial.println("> " + out);
        return true;
      }
      buffer = "";
    } else if (c == 0x08 || c == 0x7F) {
      if (buffer.length()) buffer.remove(buffer.length() - 1);
    } else {
      buffer += c;
    }
  }
  return false;
}

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

// The WS2812 is very bright at full power.
static void showLed(uint8_t r, uint8_t g, uint8_t b) {
  const int LEVEL = 40;  // 0-255
  static int32_t shown = -1;
  int32_t value = ((r * LEVEL / 255) << 16) | ((g * LEVEL / 255) << 8) | (b * LEVEL / 255);
  if (value != shown) {
    shown = value;
    rgbLedWrite(PULSAR_RGB_PIN, r * LEVEL / 255, g * LEVEL / 255, b * LEVEL / 255);
  }
}

// Blue blink while advertising, green when connected, dark when silent.
static void tickLed() {
  if (connHandle >= 0) {
    showLed(0, 255, 0);
  } else if (advertising) {
    bool blink = (millis() / 500) % 2 == 0;
    showLed(0, 0, blink ? 255 : 40);
  } else {
    showLed(0, 0, 0);
  }
}

// ---- setup / loop ----------------------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  uint32_t waitedAt = millis();
  while (!Serial && since(millis(), waitedAt) < 3000) delay(10);  // USB serial: wait for the monitor

  pinMode(PULSAR_BOOT_PIN, INPUT_PULLUP);

  BLEDevice::init(DEVICE_NAME);
  if (ble_hs_id_infer_auto(0, &ownType) != 0) ownType = BLE_OWN_ADDR_PUBLIC;
  uint8_t own[6] = {};
  ble_addr_t ownAddr;
  if (ble_hs_id_copy_addr(BLE_ADDR_PUBLIC, ownAddr.val, nullptr) == 0)
    for (int i = 0; i < 6; i++) own[i] = ownAddr.val[5 - i];
  Serial.println("B2 Be seen on " + mac(own) + " (public)");

  apply(true);
}

void loop() {
  static uint32_t refreshed = 0;

  String text;
  if (consoleLine(text)) run(text);

  switch (pollButton()) {
    case CLICK:
      settings.mode = (settings.mode + 1) % MODE_COUNT;
      apply(true);
      break;
    case DOUBLE: press(); break;
    case LONG: setOn(!settings.on); break;
    default: break;
  }

  // Connection events, queued by the BLE task.
  if (evConnect) {
    evConnect = false;
    Serial.printf("+ connected: %s (%s)\n", mac(peerAddress).c_str(), addressKind(peerType, peerAddress));
  }
  if (evDisconnect) {
    evDisconnect = false;
    Serial.println("- disconnected");
    if (evLived < 3000) {
      // The board never hangs up on its own here: the phone (or its stack) did. The usual reason is
      // a phone that still holds keys for this address from an earlier firmware.
      Serial.println("  The phone hung up within 3 s without encrypting. If this repeats, forget this"
                     " board in the phone's Bluetooth settings (and in nRF Connect) and connect again.");
    }
    if (settings.on) apply();  // advertise again
  }

  // The bench packet carries live values: refresh them every two seconds.
  uint32_t now = millis();
  if (settings.on && settings.mode == MODE_BENCH && since(now, refreshed) >= 2000) {
    refreshed = now;
    if (connHandle < 0) apply();
  }

  tickLed();
  delay(10);
}
