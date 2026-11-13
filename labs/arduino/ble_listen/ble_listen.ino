/*
 * B1 Listen: the Pulsar C5 hears every Bluetooth LE device around it.
 * Arduino port of examples/micropython/pulsar-c5/ble_b1_listen.py
 *
 * Board: ESP32C5 Dev Module, core arduino-esp32 3.3.x. No extra libraries: the BLE
 * library bundled with the core (NimBLE inside). Serial Monitor at 115200, "Both NL & CR".
 * Type help in the serial console.
 *
 * BOOT: click = next device, double-click = bytes, long press = scan on/off.
 * The LED blinks blue while scanning.
 *
 * Why ble_gap_disc() and not the BLEScan class: BLEScan only reports a scannable device
 * once its scan response arrives, merges advertisement and scan response into one payload
 * and, with duplicates on, keeps growing that payload. This lab needs every raw packet
 * (advertisement and scan response apart), exactly what MicroPython's IRQ_SCAN_RESULT gives.
 * BLEScan calls the same ble_gap_disc() underneath.
 */

#include <BLEDevice.h>
#include <host/ble_gap.h>
#include <host/ble_hs.h>
#include <math.h>
#include <string.h>

// ---- Settings (same as the MicroPython script) ---------------------------------------------

static bool ACTIVE = true;            // active scan: ask every scannable device for its scan response
static uint32_t INTERVAL_MS = 100;    // how often the radio starts listening
static uint32_t WINDOW_MS = 100;      // how long it listens each time (window = interval: all the time)
static const uint32_t FORGET_MS = 30000;  // list shows devices heard in the last 30 s
static const int MAX_DEVICES = 60;    // keep memory use small: the oldest device makes room

static const int PULSAR_BOOT_PIN = 28;
static const int PULSAR_RGB_PIN = 0;  // Some UNIT illustrations label it GPIO8: try 8 if the LED stays dark.

// ---- ble_kit pieces: AD structures --------------------------------------------------------------

static const uint8_t AD_SHORT_NAME = 0x08;
static const uint8_t AD_NAME = 0x09;
static const uint8_t AD_SERVICE_DATA16 = 0x16;
static const uint8_t AD_APPEARANCE = 0x19;
static const uint8_t AD_MANUFACTURER = 0xFF;

struct AdName {
  uint8_t kind;
  const char *name;
};
static const AdName AD_NAMES[] = {
  {0x01, "Flags"}, {0x02, "16-bit UUIDs (part)"}, {0x03, "16-bit UUIDs"},
  {0x06, "128-bit UUIDs (part)"}, {0x07, "128-bit UUIDs"}, {0x08, "Short name"}, {0x09, "Name"},
  {0x0A, "TX power"}, {0x16, "Service data"}, {0x19, "Appearance"}, {0xFF, "Manufacturer data"},
};

struct Company {
  uint16_t id;
  const char *name;
};
static const Company COMPANIES[] = {
  {0x004C, "Apple"}, {0x0006, "Microsoft"}, {0x0075, "Samsung"}, {0x00E0, "Google"},
  {0x0087, "Garmin"}, {0x0157, "Huami"}, {0x02E5, "Espressif"}, {0x0059, "Nordic"}, {0xFFFF, "test"},
};

// Types first: the Arduino builder puts function prototypes before the first function.
struct Device {
  bool used;
  uint8_t address[6];  // big-endian: the way it is written, MSB first
  uint8_t type;
  uint8_t adv[31];
  uint8_t advLen;
  uint8_t rsp[31];
  uint8_t rspLen;
  uint32_t count;
  uint32_t seen;
  int8_t rssi;
};

enum Gesture { NONE = 0, CLICK = 1, DOUBLE = 2, LONG = 3 };

// Walks [length][type][data...] structures, like ble_kit.ad_fields().
struct AdField {
  uint8_t kind;
  const uint8_t *data;
  size_t length;  // data bytes, without the type byte
};

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

static size_t hexBytes(char *out, size_t outSize, const uint8_t *data, size_t length, char sep) {
  size_t n = 0;
  for (size_t i = 0; i < length && n + 4 < outSize; i++) {
    if (i && sep) out[n++] = sep;
    n += snprintf(out + n, outSize - n, "%02X", data[i]);
  }
  out[n] = 0;
  return n;
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

// ---- Devices heard ------------------------------------------------------------------------------

static Device devices[MAX_DEVICES];
static Device rows[MAX_DEVICES];  // snapshot of what list shows, copied under the lock
static int rowCount = 0;

static uint8_t picked[6];
static bool havePicked = false;
static bool hasNear = false;
static int nearDbm = 0;
static bool watching = false;
static uint32_t packets = 0;
static uint32_t statsAt = 0;
static bool scanning = false;

// The scan callback runs in the NimBLE host task, loop() in another one: a mutex guards the table.
static SemaphoreHandle_t tableLock;
struct Lock {
  Lock() { xSemaphoreTake(tableLock, portMAX_DELAY); }
  ~Lock() { xSemaphoreGive(tableLock); }
};

// What watch prints: queued by the callback, printed by loop() (no Serial from the BLE task).
struct WatchEvent {
  uint32_t gap;
  int8_t rssi;
  uint8_t length;
  bool scanRsp;
};
static const int WATCH_QUEUE = 32;
static WatchEvent watchQueue[WATCH_QUEUE];
static int watchHead = 0, watchTail = 0;

static uint32_t since(uint32_t now, uint32_t then) {  // ticks_diff
  return now - then;
}

static String adName(const Device &device) {
  uint8_t joined[62];
  memcpy(joined, device.adv, device.advLen);
  memcpy(joined + device.advLen, device.rsp, device.rspLen);
  size_t size = device.advLen + device.rspLen, i = 0;
  AdField field;
  while (nextField(joined, size, i, field)) {
    if (field.kind == AD_NAME || field.kind == AD_SHORT_NAME) {
      String name;
      for (size_t k = 0; k < field.length; k++) name += (char)field.data[k];
      return name;
    }
  }
  return "";
}

static String makerOf(const Device &device) {
  uint8_t joined[62];
  memcpy(joined, device.adv, device.advLen);
  memcpy(joined + device.advLen, device.rsp, device.rspLen);
  size_t size = device.advLen + device.rspLen, i = 0;
  AdField field;
  while (nextField(joined, size, i, field)) {
    if (field.kind == AD_MANUFACTURER && field.length >= 2) {
      uint16_t company = field.data[0] | field.data[1] << 8;
      for (const Company &known : COMPANIES)
        if (known.id == company) return known.name;
      char text[8];
      snprintf(text, sizeof(text), "0x%04X", company);
      return text;
    }
  }
  return "";
}

// A guess from what the device chose to say about itself.
static const char *kindOf(const Device &device) {
  uint8_t joined[62];
  memcpy(joined, device.adv, device.advLen);
  memcpy(joined + device.advLen, device.rsp, device.rspLen);
  size_t size = device.advLen + device.rspLen, i = 0;
  AdField field;
  while (nextField(joined, size, i, field)) {
    if (field.kind == AD_MANUFACTURER && field.length >= 4 && field.data[0] == 0x4C &&
        field.data[1] == 0x00 && field.data[2] == 0x02 && field.data[3] == 0x15)
      return "beacon";
    if (field.kind == AD_SERVICE_DATA16 && field.length >= 2 && field.data[0] == 0xAA && field.data[1] == 0xFE)
      return "beacon";
    if (field.kind == 0x02 || field.kind == 0x03) {
      for (size_t k = 0; k + 1 < field.length; k++)
        if (field.data[k] == 0x12 && field.data[k + 1] == 0x18) return "keyboard/mouse";
    }
    if (field.kind == AD_APPEARANCE && field.length == 2) {
      switch ((field.data[0] | field.data[1] << 8) >> 6) {
        case 1: return "phone";
        case 2: return "computer";
        case 3: return "watch";
        case 15: return "keyboard/mouse";
        case 33: return "audio";
        case 13: case 21: return "sensor";
        case 49: return "light";
        default: return "thing";
      }
    }
  }
  String company = makerOf(device);
  return (company == "Apple" || company == "Samsung" || company == "Google") ? "phone" : "thing";
}

// Metres from signal strength: a factor-of-two guess (path loss exponent 2.5).
static float distance(int rssi, int at1m = -59) {
  return powf(10.0f, (at1m - rssi) / 25.0f);
}

// ---- Scan callback: IRQ_SCAN_RESULT -------------------------------------------------------------

static int onGap(struct ble_gap_event *event, void *arg) {
  if (event->type != BLE_GAP_EVENT_DISC) return 0;
  const auto &disc = event->disc;
  uint8_t address[6];
  for (int i = 0; i < 6; i++) address[i] = disc.addr.val[5 - i];  // NimBLE is little-endian
  uint8_t advType = disc.event_type;
  uint8_t length = disc.length_data > 31 ? 31 : disc.length_data;
  uint32_t now = millis();

  Lock lock;
  packets++;
  Device *device = nullptr;
  for (Device &d : devices)
    if (d.used && memcmp(d.address, address, 6) == 0) {
      device = &d;
      break;
    }
  if (!device) {
    for (Device &d : devices)
      if (!d.used) {
        device = &d;
        break;
      }
    if (!device) {  // full: the oldest device makes room
      device = &devices[0];
      for (Device &d : devices)
        if (since(now, d.seen) > since(now, device->seen)) device = &d;
    }
    memset(device, 0, sizeof(Device));
    device->used = true;
    memcpy(device->address, address, 6);
    device->type = disc.addr.type;
    device->seen = now;
    device->rssi = disc.rssi;
  }
  uint32_t gap = since(now, device->seen);
  device->seen = now;
  device->rssi = disc.rssi;
  device->count++;
  if (advType == BLE_HCI_ADV_RPT_EVTYPE_SCAN_RSP) {  // the second packet, sent only to an active scanner
    memcpy(device->rsp, disc.data, length);
    device->rspLen = length;
  } else {
    memcpy(device->adv, disc.data, length);
    device->advLen = length;
  }
  if (watching && havePicked && memcmp(address, picked, 6) == 0) {
    int next = (watchHead + 1) % WATCH_QUEUE;
    if (next != watchTail) {  // a full queue drops the event
      watchQueue[watchHead] = {gap, (int8_t)disc.rssi, length, advType == BLE_HCI_ADV_RPT_EVTYPE_SCAN_RSP};
      watchHead = next;
    }
  }
  return 0;
}

static void startScan() {
  struct ble_gap_disc_params params = {};
  params.itvl = INTERVAL_MS * 1000 / 625;  // units of 0.625 ms
  params.window = WINDOW_MS * 1000 / 625;
  params.filter_policy = BLE_HCI_SCAN_FILT_NO_WL;
  params.limited = 0;
  params.passive = !ACTIVE;
  params.filter_duplicates = 0;  // every packet: the lab counts them
  uint8_t ownType;
  if (ble_hs_id_infer_auto(0, &ownType) != 0) ownType = BLE_OWN_ADDR_PUBLIC;
  int rc = ble_gap_disc(ownType, BLE_HS_FOREVER, &params, onGap, nullptr);
  if (rc != 0 && rc != BLE_HS_EALREADY) {
    Serial.printf("! scan failed (rc=%d)\n", rc);
    return;
  }
  scanning = true;
  Serial.printf("+ %s scan, %lu/%lu ms (%lu%% listening)\n", ACTIVE ? "active" : "passive",
                (unsigned long)WINDOW_MS, (unsigned long)INTERVAL_MS,
                (unsigned long)(WINDOW_MS * 100 / INTERVAL_MS));
}

static void stopScan() {
  ble_gap_disc_cancel();
  scanning = false;
  Serial.println("Scan paused");
}

// ---- The list ------------------------------------------------------------------------------------

// Copies the devices heard in the last FORGET_MS (and strong enough) into rows[], strongest first.
static void table() {
  uint32_t now = millis();
  rowCount = 0;
  Lock lock;
  for (const Device &d : devices) {
    if (!d.used || since(now, d.seen) >= FORGET_MS) continue;
    if (hasNear && d.rssi < nearDbm) continue;
    int at = rowCount++;
    while (at > 0 && rows[at - 1].rssi < d.rssi) {  // insertion sort, stable
      rows[at] = rows[at - 1];
      at--;
    }
    rows[at] = d;
  }
}

static void cmdList() {
  table();
  Serial.println(" #  dBm     m  kind            address            type            pkts  name / maker");
  for (int n = 0; n < rowCount; n++) {
    const Device &d = rows[n];
    String name = adName(d);
    if (name.length() == 0) name = makerOf(d);
    Serial.printf("%2d %4d %5.1f  %-14s  %s  %-14s %5lu  %s\n", n + 1, d.rssi, distance(d.rssi), kindOf(d),
                  mac(d.address).c_str(), addressKind(d.type, d.address), (unsigned long)d.count, name.c_str());
  }
  if (rowCount == 0) Serial.println("Nothing heard yet. Phones advertise more with the screen on.");
}

static bool parseInt(const String &text, long &value) {
  String trimmed = text;
  trimmed.trim();
  if (trimmed.length() == 0) return false;
  char *end;
  value = strtol(trimmed.c_str(), &end, 10);
  return *end == 0;
}

static void cmdPick(const String &text) {
  table();
  long n;
  if (!parseInt(text, n) || n < 1 || n > rowCount) {
    Serial.println("pick <n>: a row number from list");
    return;
  }
  memcpy(picked, rows[n - 1].address, 6);
  havePicked = true;
  Serial.println("Picked " + mac(picked));
}

static bool findDevice(const uint8_t *address, Device &out) {
  Lock lock;
  for (const Device &d : devices)
    if (d.used && memcmp(d.address, address, 6) == 0) {
      out = d;
      return true;
    }
  return false;
}

static void cmdNext() {
  table();
  if (rowCount == 0) {
    Serial.println("Nothing heard yet");
    return;
  }
  int at = -1;
  if (havePicked)
    for (int i = 0; i < rowCount; i++)
      if (memcmp(rows[i].address, picked, 6) == 0) at = i;
  int next = at >= 0 ? (at + 1) % rowCount : 0;
  memcpy(picked, rows[next].address, 6);
  havePicked = true;
  Serial.println("Picked " + mac(picked) + " " + adName(rows[next]));
}

static void printPacket(const char *title, const uint8_t *payload, size_t length) {
  if (length == 0) {
    Serial.printf("%s: none (passive mode, or the device does not send one)\n", title);
    return;
  }
  Serial.printf("%s: %u bytes\n", title, (unsigned)length);
  size_t i = 0;
  AdField field;
  while (nextField(payload, length, i, field)) {
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
  Device device;
  if (!havePicked || !findDevice(picked, device)) {
    Serial.println("pick a device first: list, then pick <n>");
    return;
  }
  Serial.println(mac(device.address) + " " + addressKind(device.type, device.address));
  printPacket("advertisement", device.adv, device.advLen);
  printPacket("scan response", device.rsp, device.rspLen);
}

static void cmdStats() {
  uint32_t now = millis();
  uint32_t count;
  {
    Lock lock;
    count = packets;
    packets = 0;
  }
  float seconds = max<uint32_t>(1, since(now, statsAt)) / 1000.0f;
  statsAt = now;
  table();  // like len(table()) in the original: honours "near" and the 30 s window
  Serial.printf("%.0f packets/s from %d devices (%s scan, %lu/%lu ms)\n", count / seconds, rowCount,
                ACTIVE ? "active" : "passive", (unsigned long)WINDOW_MS, (unsigned long)INTERVAL_MS);
}

// ---- Commands ------------------------------------------------------------------------------------

static void run(const String &text) {
  int space = text.indexOf(' ');
  String command = space < 0 ? text : text.substring(0, space);
  String rest = space < 0 ? "" : text.substring(space + 1);
  command.toLowerCase();
  rest.trim();

  if (command == "list") {
    cmdList();
  } else if (command == "pick") {
    cmdPick(rest);
  } else if (command == "next") {
    cmdNext();
  } else if (command == "bytes") {
    cmdBytes();
  } else if (command == "watch") {
    watching = rest != "off" && havePicked;
    Serial.println(watching ? "Watching" : "Watch off (pick a device to watch it)");
  } else if (command == "mode" && (rest == "active" || rest == "passive")) {
    ACTIVE = rest == "active";
    stopScan();
    startScan();
  } else if (command == "timing") {
    long interval = 0, window = 0;
    int space2 = rest.indexOf(' ');
    bool ok = space2 > 0 && parseInt(rest.substring(0, space2), interval) &&
              parseInt(rest.substring(space2 + 1), window) && rest.substring(space2 + 1).indexOf(' ') < 0;
    if (!ok || !(3 <= window && window <= interval && interval <= 10000)) {
      Serial.println("timing <interval> <window> in ms, window <= interval");
      return;
    }
    INTERVAL_MS = interval;
    WINDOW_MS = window;
    stopScan();
    startScan();
  } else if (command == "near") {
    long dbm;
    if (rest == "off") {
      hasNear = false;
    } else if (parseInt(rest, dbm)) {
      hasNear = true;
      nearDbm = dbm;
    } else {
      Serial.println("near <dBm> (e.g. near -70) or near off");
      return;
    }
    if (!hasNear) Serial.println("Showing every device");
    else Serial.printf("Showing devices at %d dBm or stronger\n", nearDbm);
  } else if (command == "scan") {
    if (scanning) stopScan();
    else startScan();
  } else if (command == "stats") {
    cmdStats();
  } else if (command == "clear") {
    {
      Lock lock;
      memset(devices, 0, sizeof(devices));
    }
    Serial.println("Forgot every device");
  } else {
    Serial.println("list | pick <n> | next | bytes | watch [off] | mode active|passive |"
                   " timing <interval> <window> | near <dBm>|off | scan | stats | clear");
  }
}

// ---- ble_kit pieces: console, BOOT button, status LED ------------------------------------------

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

// Blue blink while scanning, dark otherwise. The WS2812 is very bright at full power.
static void showLed(uint8_t r, uint8_t g, uint8_t b) {
  const int LEVEL = 40;  // 0-255
  static int32_t shown = -1;
  int32_t value = ((r * LEVEL / 255) << 16) | ((g * LEVEL / 255) << 8) | (b * LEVEL / 255);
  if (value != shown) {
    shown = value;
    rgbLedWrite(PULSAR_RGB_PIN, r * LEVEL / 255, g * LEVEL / 255, b * LEVEL / 255);
  }
}

static void tickLed() {
  if (!scanning) {
    showLed(0, 0, 0);
    return;
  }
  bool blink = (millis() / 500) % 2 == 0;
  showLed(0, 0, blink ? 255 : 40);
}

// ---- setup / loop --------------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  uint32_t waitedAt = millis();
  while (!Serial && since(millis(), waitedAt) < 3000) delay(10);  // USB serial: wait for the monitor

  tableLock = xSemaphoreCreateMutex();
  pinMode(PULSAR_BOOT_PIN, INPUT_PULLUP);

  BLEDevice::init("PulsarC5-B1");
  uint8_t own[6] = {};
  ble_addr_t ownAddr;
  if (ble_hs_id_copy_addr(BLE_ADDR_PUBLIC, ownAddr.val, nullptr) == 0)
    for (int i = 0; i < 6; i++) own[i] = ownAddr.val[5 - i];
  Serial.println("B1 Listen on " + mac(own) + " (public)");

  statsAt = millis();
  startScan();
  Serial.println("Type list, or help");
}

void loop() {
  String text;
  if (consoleLine(text)) run(text);

  switch (pollButton()) {
    case CLICK: cmdNext(); break;
    case DOUBLE: cmdBytes(); break;
    case LONG:
      if (scanning) stopScan();
      else startScan();
      break;
    default: break;
  }

  // Print what watch collected in the BLE task.
  while (true) {
    WatchEvent event;
    {
      Lock lock;
      if (watchTail == watchHead) break;
      event = watchQueue[watchTail];
      watchTail = (watchTail + 1) % WATCH_QUEUE;
    }
    Serial.printf("< +%5lu ms  %4d dBm  %2u B  %s\n", (unsigned long)event.gap, event.rssi, event.length,
                  event.scanRsp ? "scan rsp" : "adv");
  }

  tickLed();
  delay(10);
}
