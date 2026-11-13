/*
 * B5 Phone app: B3's Bench service plus sketch, live sensors and a speed test for esp-labs' ble-app.
 * Arduino port of examples/micropython/pulsar-c5/ble_b5_app.py
 *
 * Board: ESP32C5 Dev Module, core arduino-esp32 3.3.x. No extra libraries: the BLE
 * library bundled with the core (BLEServer, BLEService, BLECharacteristic; NimBLE inside).
 * Serial Monitor at 115200, "Both NL & CR". Type help in the serial console.
 *
 * Open the ble-app (Web Bluetooth in Chrome, Bluefy on iPhone) and connect to "Bench B5".
 * The Pulsar C5 has no screen: the sketch's commands are counted here and the RGB LED takes the
 * last colour drawn. The Sensors line carries the A2 (GPIO4) reading where the S3 lab sends the
 * touch sensor: the ESP32-C5 has no touch sensor.
 * BOOT: every click, double-click and long press is sent as a Button notification.
 *
 * Unlike MicroPython, Arduino can ask for the link settings: the Link characteristic
 * (1 fast, 2 slow, 3 2M PHY, 4 1M PHY, 5 longer radio packets) really changes the connection.
 */

#include <BLE2901.h>
#include <BLE2904.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <host/ble_gap.h>
#include <host/ble_hs.h>
#include <math.h>
#include <string.h>

// ---- Settings (same as the MicroPython script) ---------------------------------------------

static const uint32_t SENSOR_PERIOD_MS = 200;
static const int ANALOG_PIN = 4;  // A2

static const char *DEVICE_NAME = "Bench B5";
static const int PULSAR_BOOT_PIN = 28;
static const int PULSAR_RGB_PIN = 0;  // Some UNIT illustrations label it GPIO8: try 8 if the LED stays dark.

static const char *BENCH_SERVICE = "ba5e0000-b1e5-4ab1-9e5c-be7cbe7cb0a7";
static const uint8_t BENCH_UUID[16] = {0xBA, 0x5E, 0x00, 0x00, 0xB1, 0xE5, 0x4A, 0xB1,
                                       0x9E, 0x5C, 0xBE, 0x7C, 0xBE, 0x7C, 0xB0, 0xA7};

// ---- Types first: the Arduino builder puts function prototypes before the first function ------

// ba5e0001 ... ba5e0005 are B3's; the four extras are ba5e0010 ... ba5e0013.
enum Characteristic { LED = 0, MESSAGE, BUTTON, TEMPERATURE, UPTIME, CANVAS, SENSORS, SPEED, LINK, CHAR_COUNT };
enum Gesture { NONE = 0, CLICK = 1, DOUBLE = 2, LONG = 3 };
enum Sent { NOT_CONNECTED, NOT_SUBSCRIBED, SENT };
static const char *const GESTURE_NAMES[] = {"", "click", "double", "long"};

// What the BLE task tells loop(): connections and writes. loop() prints (no Serial from the BLE task).
enum EventType { EV_CONNECT, EV_DISCONNECT, EV_MTU, EV_LED, EV_MESSAGE, EV_SPEED, EV_LINK };
struct Event {
  uint8_t type;
  uint8_t peerType;
  uint8_t peer[6];  // big-endian: the way it is written
  uint16_t handle;  // connection handle
  uint16_t value;   // MTU
  uint32_t number;  // ms the connection lasted
  uint8_t length;
  uint8_t data[40];
};
// One write to the Canvas characteristic, waiting for loop(): the largest write that fits a 247-byte MTU.
struct CanvasWrite {
  uint8_t length;
  uint8_t data[244];
};
struct SpeedRun {
  bool active;
  uint16_t handle;
  uint32_t target, sent, seq, startedAt, retries;
};
struct CanvasCount {
  uint32_t writes, bytes, commands;
};

static const int QUEUE_SIZE = 16;
static Event queue[QUEUE_SIZE];
static int queueHead = 0, queueTail = 0;
static const int CANVAS_QUEUE_SIZE = 8;
static CanvasWrite canvasQueue[CANVAS_QUEUE_SIZE];
static int canvasHead = 0, canvasTail = 0;
static volatile uint32_t canvasDropped = 0;
static portMUX_TYPE queueMux = portMUX_INITIALIZER_UNLOCKED;

static void push(const Event &event) {
  portENTER_CRITICAL(&queueMux);
  int next = (queueHead + 1) % QUEUE_SIZE;
  if (next != queueTail) {  // a full queue drops the event
    queue[queueHead] = event;
    queueHead = next;
  }
  portEXIT_CRITICAL(&queueMux);
}

static bool pop(Event &event) {
  bool any = false;
  portENTER_CRITICAL(&queueMux);
  if (queueTail != queueHead) {
    event = queue[queueTail];
    queueTail = (queueTail + 1) % QUEUE_SIZE;
    any = true;
  }
  portEXIT_CRITICAL(&queueMux);
  return any;
}

static void pushCanvas(const uint8_t *data, size_t length) {
  portENTER_CRITICAL(&queueMux);
  int next = (canvasHead + 1) % CANVAS_QUEUE_SIZE;
  if (next != canvasTail) {
    CanvasWrite &slot = canvasQueue[canvasHead];
    slot.length = length > sizeof(slot.data) ? sizeof(slot.data) : length;
    memcpy(slot.data, data, slot.length);
    canvasHead = next;
  } else {
    canvasDropped = canvasDropped + 1;
  }
  portEXIT_CRITICAL(&queueMux);
}

static bool popCanvas(CanvasWrite &write) {
  bool any = false;
  portENTER_CRITICAL(&queueMux);
  if (canvasTail != canvasHead) {
    write = canvasQueue[canvasTail];
    canvasTail = (canvasTail + 1) % CANVAS_QUEUE_SIZE;
    any = true;
  }
  portEXIT_CRITICAL(&queueMux);
  return any;
}

static BLEServer *server = nullptr;
static BLECharacteristic *chars[CHAR_COUNT];
static volatile uint32_t subscribed[CHAR_COUNT];  // bit n set: connection handle n subscribed
static volatile uint32_t connectedAt[8];
static bool advertising = false;

static SpeedRun speed = {};
static CanvasCount canvas = {};
static uint8_t sequence = 0;
static uint16_t buttonCount = 0;
static String message;
static bool ledClaimed = false;
static uint8_t claimed[3];

static uint32_t since(uint32_t now, uint32_t then) {  // ticks_diff
  return now - then;
}

static void addressOf(const ble_gap_conn_desc *desc, Event &event) {
  event.peerType = desc->peer_id_addr.type;
  for (int i = 0; i < 6; i++) event.peer[i] = desc->peer_id_addr.val[5 - i];
}

class ServerEvents : public BLEServerCallbacks {
  void onConnect(BLEServer *, ble_gap_conn_desc *desc) override {
    Event event = {};
    event.type = EV_CONNECT;
    event.handle = desc->conn_handle;
    addressOf(desc, event);
    if (desc->conn_handle < 8) connectedAt[desc->conn_handle] = millis();
    push(event);
  }
  void onDisconnect(BLEServer *, ble_gap_conn_desc *desc) override {
    Event event = {};
    event.type = EV_DISCONNECT;
    event.handle = desc->conn_handle;
    if (desc->conn_handle < 8) event.number = since(millis(), connectedAt[desc->conn_handle]);
    for (int i = 0; i < CHAR_COUNT; i++)
      if (desc->conn_handle < 32) subscribed[i] &= ~(1u << desc->conn_handle);
    push(event);
  }
  void onMtuChanged(BLEServer *, ble_gap_conn_desc *, uint16_t mtu) override {
    Event event = {};
    event.type = EV_MTU;
    event.value = mtu;
    push(event);
  }
};

class WriteEvents : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *chr, ble_gap_conn_desc *desc) override {
    if (chr == chars[CANVAS]) {
      pushCanvas(chr->getData(), chr->getLength());
      return;
    }
    Event event = {};
    event.handle = desc->conn_handle;
    if (chr == chars[LED]) event.type = EV_LED;
    else if (chr == chars[MESSAGE]) event.type = EV_MESSAGE;
    else if (chr == chars[SPEED]) event.type = EV_SPEED;
    else if (chr == chars[LINK]) event.type = EV_LINK;
    else return;
    size_t length = chr->getLength();
    event.length = length > sizeof(event.data) ? sizeof(event.data) : length;
    memcpy(event.data, chr->getData(), event.length);
    push(event);
  }
  void onSubscribe(BLECharacteristic *chr, ble_gap_conn_desc *desc, uint16_t subValue) override {
    for (int i = 0; i < CHAR_COUNT; i++) {
      if (chr != chars[i] || desc->conn_handle >= 32) continue;
      if (subValue) subscribed[i] |= 1u << desc->conn_handle;
      else subscribed[i] &= ~(1u << desc->conn_handle);
    }
  }
};

static ServerEvents serverEvents;
static WriteEvents writeEvents;

// ---- ble_kit pieces: formatting --------------------------------------------------------------------

static String mac(const uint8_t *address) {
  char text[24];
  size_t n = 0;
  for (int i = 0; i < 6; i++) n += snprintf(text + n, sizeof(text) - n, i ? ":%02X" : "%02X", address[i]);
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

// ---- The Bench service, with B5's four extras ---------------------------------------------------------

static String benchUuid(int number) {
  char text[40];
  snprintf(text, sizeof(text), "ba5e%04x-b1e5-4ab1-9e5c-be7cbe7cb0a7", number);
  return String(text);
}

// A characteristic with its User Description (0x2901) and, optionally, Presentation Format (0x2904).
static BLECharacteristic *makeCharacteristic(BLEService *service, const String &uuid, uint32_t properties,
                                             const char *description, BLE2904 *presentation = nullptr) {
  BLECharacteristic *chr = service->createCharacteristic(BLEUUID(uuid.c_str()), properties);
  BLE2901 *user = new BLE2901();
  user->setDescription(description);
  chr->addDescriptor(user);
  if (presentation) chr->addDescriptor(presentation);
  return chr;
}

static void registerServices() {
  using C = BLECharacteristic;
  BLEService *bench = server->createService(BLEUUID(BENCH_SERVICE));
  chars[LED] = makeCharacteristic(bench, benchUuid(1), C::PROPERTY_READ | C::PROPERTY_WRITE,
                                  "LED colour: red, green, blue");
  chars[MESSAGE] = makeCharacteristic(bench, benchUuid(2),
                                      C::PROPERTY_READ | C::PROPERTY_WRITE | C::PROPERTY_WRITE_NR,
                                      "Message for the board");
  chars[BUTTON] = makeCharacteristic(bench, benchUuid(3), C::PROPERTY_READ | C::PROPERTY_NOTIFY,
                                     "Button: gesture, count");
  // sint16, x 10^-2, degree Celsius (0x272F)
  BLE2904 *celsius = new BLE2904();
  celsius->setFormat(BLE2904::FORMAT_SINT16);
  celsius->setExponent(-2);
  celsius->setUnit(0x272F);
  celsius->setNamespace(1);
  celsius->setDescription(0);
  chars[TEMPERATURE] = makeCharacteristic(bench, benchUuid(4), C::PROPERTY_READ | C::PROPERTY_NOTIFY,
                                          "Chip temperature", celsius);
  // uint32, second (0x2703)
  BLE2904 *seconds = new BLE2904();
  seconds->setFormat(BLE2904::FORMAT_UINT32);
  seconds->setExponent(0);
  seconds->setUnit(0x2703);
  seconds->setNamespace(1);
  seconds->setDescription(0);
  chars[UPTIME] = makeCharacteristic(bench, benchUuid(5), C::PROPERTY_READ, "Uptime in seconds", seconds);

  // B5's own characteristics (see ble_b5_app.py): sketch, sensors, speed test, link controls.
  chars[CANVAS] = makeCharacteristic(bench, benchUuid(0x10), C::PROPERTY_WRITE_NR | C::PROPERTY_WRITE,
                                     "Canvas: sketch commands");
  chars[SENSORS] = makeCharacteristic(bench, benchUuid(0x11), C::PROPERTY_READ | C::PROPERTY_NOTIFY,
                                      "Sensors: temp, A2, heap, RSSI");
  chars[SPEED] = makeCharacteristic(bench, benchUuid(0x12), C::PROPERTY_WRITE | C::PROPERTY_NOTIFY,
                                    "Speed test");
  chars[LINK] = makeCharacteristic(bench, benchUuid(0x13), C::PROPERTY_WRITE,
                                   "Link: 1 fast 2 slow 3 2M 4 1M 5 DLE");

  const uint8_t zero3[3] = {0, 0, 0}, zero12[12] = {};
  chars[LED]->setValue(zero3, 3);
  chars[BUTTON]->setValue(zero3, 3);
  chars[SENSORS]->setValue(zero12, sizeof(zero12));
  for (int i = 0; i < CHAR_COUNT; i++) chars[i]->setCallbacks(&writeEvents);
  bench->start();

  // The standard Device Information (180A) and Battery (180F) services.
  BLEService *info = server->createService(BLEUUID((uint16_t)0x180A));
  info->createCharacteristic(BLEUUID((uint16_t)0x2A29), C::PROPERTY_READ)->setValue(String("UNIT Electronics"));
  info->createCharacteristic(BLEUUID((uint16_t)0x2A24), C::PROPERTY_READ)->setValue(String("ESP32-C5"));
  info->createCharacteristic(BLEUUID((uint16_t)0x2A26), C::PROPERTY_READ)->setValue(String("ble_kit 0.1"));
  info->start();
  BLEService *battery = server->createService(BLEUUID((uint16_t)0x180F));
  const uint8_t level = 0x64;
  battery->createCharacteristic(BLEUUID((uint16_t)0x2A19), C::PROPERTY_READ | C::PROPERTY_NOTIFY)
    ->setValue(&level, 1);
  battery->start();
}

// Flags and the 128-bit service UUID (21 of 31 bytes); the name and appearance in the scan response.
static void advertise() {
  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->stop();
  uint8_t advBytes[21] = {0x02, 0x01, 0x06, 0x11, 0x07};  // flags, then the 128-bit UUID list
  for (int i = 0; i < 16; i++) advBytes[5 + i] = BENCH_UUID[15 - i];  // little-endian on the air
  BLEAdvertisementData advData, scanData;
  advData.addData((char *)advBytes, sizeof(advBytes));
  uint8_t rspBytes[2 + 8 + 4] = {1 + 8, 0x09};
  memcpy(rspBytes + 2, DEVICE_NAME, 8);
  rspBytes[10] = 3;
  rspBytes[11] = 0x19;
  rspBytes[12] = 0x40;
  rspBytes[13] = 0x05;  // appearance 0x0540: sensor
  scanData.addData((char *)rspBytes, sizeof(rspBytes));
  adv->setAdvertisementData(advData);
  adv->setScanResponseData(scanData);
  adv->setMinInterval(100 * 1000 / 625);  // 100 ms, in units of 0.625 ms
  adv->setMaxInterval(100 * 1000 / 625);
  if (adv->start()) {
    advertising = true;
    Serial.printf("Advertising as %s\n", DEVICE_NAME);
  } else {
    Serial.println("! advertising failed");
  }
}

static void stopAdvertising() {
  BLEDevice::getAdvertising()->stop();
  advertising = false;
}

// Store a value and notify the devices that subscribed to it.
static Sent notify(int index, const uint8_t *value, size_t length) {
  chars[index]->setValue(value, length);
  if (server->getConnectedCount() == 0) return NOT_CONNECTED;
  if (subscribed[index] == 0) return NOT_SUBSCRIBED;
  chars[index]->notify();
  return SENT;
}

// Button notification: gesture (1 click, 2 double, 3 long), count (uint16).
static void pressButton(int gesture) {
  buttonCount++;
  const uint8_t value[3] = {(uint8_t)gesture, (uint8_t)(buttonCount & 0xFF), (uint8_t)(buttonCount >> 8)};
  Sent sent = notify(BUTTON, value, 3);
  const char *what = sent == SENT ? "notify" : sent == NOT_SUBSCRIBED ? "(not subscribed)" : "(not connected)";
  Serial.printf("%s Button: %s #%u\n", what, GESTURE_NAMES[gesture], buttonCount);
}

// From the main loop: uptime every second, temperature every 2 s.
static void benchTick() {
  static uint32_t secondsAt = 0;
  uint32_t now = millis();
  if (since(now, secondsAt) < 1000) return;
  secondsAt = now;
  uint32_t seconds = now / 1000;
  chars[UPTIME]->setValue((uint8_t *)&seconds, 4);
  if (seconds % 2 == 0) {
    float celsius = temperatureRead();
    if (!isnan(celsius)) {
      int16_t centi = (int16_t)(celsius * 100);
      notify(TEMPERATURE, (uint8_t *)&centi, 2);
    }
  }
}

// ---- The sketch (Canvas) -------------------------------------------------------------------------------

// The sketch's colours are RGB565: 5 bits red, 6 green, 5 blue.
static void rgb565(uint16_t colour, uint8_t *rgb) {
  rgb[0] = ((colour >> 11) & 0x1F) * 255 / 31;
  rgb[1] = ((colour >> 5) & 0x3F) * 255 / 63;
  rgb[2] = (colour & 0x1F) * 255 / 31;
}

// Walk one write. Each command starts with its opcode and a little-endian colour.
static void onCanvas(const uint8_t *value, size_t length) {
  struct Op {
    uint8_t code;
    const char *name;
    size_t size;
  };
  static const Op OPS[] = {{0x01, "clear", 3}, {0x02, "stroke", 5}, {0x03, "rect", 10},
                           {0x04, "text", 8}, {0x05, "dot", 7}};
  size_t i = 0;
  uint32_t commands = 0;
  bool haveLast = false;
  uint16_t last = 0;
  String names;
  while (i < length) {
    const Op *op = nullptr;
    for (const Op &known : OPS)
      if (known.code == value[i]) op = &known;
    size_t size = op ? op->size : 0;
    if (op && i + size <= length) {
      if (value[i] == 0x02) size += value[i + 4] * 3;  // stroke: n points of 3 bytes
      else if (value[i] == 0x04) size += value[i + 7];  // text: len characters
    }
    if (!op || i + size > length) {
      Serial.printf("< canvas: %s at byte %u, the rest is ignored\n", op ? "cut short" : "unknown opcode", (unsigned)i);
      break;
    }
    last = value[i + 1] | value[i + 2] << 8;
    haveLast = true;
    if (names.length()) names += ", ";
    names += op->name;
    commands++;
    i += size;
  }
  canvas.writes++;
  canvas.bytes += length;
  canvas.commands += commands;
  if (haveLast) {
    rgb565(last, claimed);
    ledClaimed = true;
  }
  if (canvas.writes % 20 == 1)
    Serial.printf("< canvas write #%lu: %s (%u bytes)\n", (unsigned long)canvas.writes, names.c_str(), (unsigned)length);
}

// ---- Speed test ------------------------------------------------------------------------------------------

static void startSpeed(uint16_t handle, uint32_t target) {
  speed = {true, handle, target, 0, 0, millis(), 0};
  Serial.printf("Speed test: %lu bytes\n", (unsigned long)target);
}

// Push notifications until the stack's buffers are full, then come back next loop.
// notify() of the core does not say when it fails, so this talks to NimBLE directly: the error
// is the back-pressure that sets the throughput.
static void speedStep() {
  struct ble_gap_conn_desc desc;
  if (ble_gap_conn_find(speed.handle, &desc) != 0) {
    speed.active = false;
    return;
  }
  uint16_t mtu = ble_att_mtu(speed.handle);
  size_t size = mtu - 3 > 244 ? 244 : mtu - 3;
  uint8_t packet[244] = {};
  uint16_t valueHandle = chars[SPEED]->getHandle();
  while (speed.sent < speed.target) {
    uint16_t seq = speed.seq % 0xFFFF;  // FFFF is the summary
    packet[0] = seq & 0xFF;
    packet[1] = seq >> 8;
    size_t chunk = min<size_t>(size, speed.target - speed.sent);
    struct os_mbuf *om = ble_hs_mbuf_from_flat(packet, max<size_t>(2, chunk));
    if (!om || ble_gatts_notify_custom(speed.handle, valueHandle, om) != 0) {
      speed.retries++;
      return;
    }
    speed.sent += chunk;
    speed.seq++;
  }
  uint32_t ms = max<uint32_t>(1, since(millis(), speed.startedAt));
  uint8_t txPhy = 1, rxPhy = 1;
  ble_gap_read_le_phy(speed.handle, &txPhy, &rxPhy);
  // FF FF, bytes, ms, MTU, interval (1.25 ms units), PHY (1 = 1M, 2 = 2M)
  uint8_t summary[15] = {0xFF, 0xFF};
  uint16_t interval = desc.conn_itvl;
  memcpy(summary + 2, &speed.sent, 4);
  memcpy(summary + 6, &ms, 4);
  memcpy(summary + 10, &mtu, 2);
  memcpy(summary + 12, &interval, 2);
  summary[14] = txPhy;
  struct os_mbuf *om = ble_hs_mbuf_from_flat(summary, sizeof(summary));
  if (!om || ble_gatts_notify_custom(speed.handle, valueHandle, om) != 0) return;
  Serial.printf("Speed: %lu bytes in %lu ms = %.1f kB/s (MTU %u, %lu waits)\n", (unsigned long)speed.sent,
                (unsigned long)ms, (float)speed.sent / ms, mtu, (unsigned long)speed.retries);
  speed.active = false;
}

static void onSpeed(uint16_t handle, const uint8_t *value, size_t length) {
  if (length >= 1 && value[0] == 0x00) {
    speed.active = false;
    Serial.println("Speed test stopped");
  } else if (length == 5 && value[0] == 1) {
    uint32_t target;
    memcpy(&target, value + 1, 4);
    if (target < 1000) target = 1000;
    if (target > 1000000) target = 1000000;
    if (subscribed[SPEED] == 0) Serial.println("Speed: subscribe to Speed (ba5e0012) first");
    else startSpeed(handle, target);
  }
}

// The Link characteristic: ask the phone for other connection settings. The central has the last word.
static void onLink(uint16_t handle, const uint8_t *value, size_t length) {
  if (length != 1 || value[0] < 1 || value[0] > 5) {
    Serial.println("Link wants one byte, 1-5");
    return;
  }
  int rc = 0;
  const char *what = "";
  // Units: interval 1.25 ms, timeout 10 ms.
  switch (value[0]) {
    case 1: {
      struct ble_gap_upd_params params = {6, 12, 0, 400, 0, 0};
      rc = ble_gap_update_params(handle, &params);
      what = "asked for 7.5-15 ms";
      break;
    }
    case 2: {
      struct ble_gap_upd_params params = {80, 160, 4, 600, 0, 0};
      rc = ble_gap_update_params(handle, &params);
      what = "asked for 100-200 ms, latency 4";
      break;
    }
    case 3:
      rc = ble_gap_set_prefered_le_phy(handle, BLE_GAP_LE_PHY_2M_MASK, BLE_GAP_LE_PHY_2M_MASK, 0);
      what = "asked for the 2M PHY";
      break;
    case 4:
      rc = ble_gap_set_prefered_le_phy(handle, BLE_GAP_LE_PHY_1M_MASK, BLE_GAP_LE_PHY_1M_MASK, 0);
      what = "asked for the 1M PHY";
      break;
    case 5:
      // Data Length Extension: link-layer packets of up to 251 bytes instead of 27, so one
      // 247-byte MTU packet no longer has to be cut into 10 radio packets.
      rc = ble_gap_set_data_len(handle, 251, 2120);
      what = "asked for 251-byte radio packets";
      break;
  }
  Serial.printf("< write Link: %u: %s%s", value[0], what, rc == 0 ? "\n" : "");
  if (rc != 0) Serial.printf(" (failed, rc=%d)\n", rc);
}

// ---- Sensors ---------------------------------------------------------------------------------------------

// temperature x 100, A2 (0-65535), free heap, RSSI of the first connection, sequence: 12 bytes.
static void sendSensors() {
  float celsius = temperatureRead();
  if (isnan(celsius)) celsius = 0;
  int16_t centi = (int16_t)(celsius * 100);
  uint32_t analog = (uint32_t)analogRead(ANALOG_PIN) * 65535 / 4095;
  uint32_t heap = ESP.getFreeHeap();
  int8_t rssi = 0;
  for (auto &peer : server->getPeerDevices(false)) {
    ble_gap_conn_rssi(peer.first, &rssi);
    break;
  }
  uint8_t packet[12];
  memcpy(packet, &centi, 2);
  memcpy(packet + 2, &analog, 4);
  memcpy(packet + 6, &heap, 4);
  packet[10] = (uint8_t)rssi;
  packet[11] = sequence++;
  notify(SENSORS, packet, sizeof(packet));
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

static void run(const String &text) {
  int space = text.indexOf(' ');
  String command = space < 0 ? text : text.substring(0, space);
  String rest = space < 0 ? "" : text.substring(space + 1);
  command.toLowerCase();
  rest.trim();

  if (command == "adv" && (rest == "on" || rest == "off")) {
    if (rest == "on") advertise();
    else stopAdvertising();
  } else if (command == "clear") {
    ledClaimed = false;
    canvas = {};
    Serial.println("Sketch counters cleared");
  } else if (command == "canvas") {
    Serial.printf("%lu writes, %lu bytes, %lu commands", (unsigned long)canvas.writes,
                  (unsigned long)canvas.bytes, (unsigned long)canvas.commands);
    if (canvasDropped) Serial.printf(" (%lu writes dropped: the app sent faster than loop() draws)", (unsigned long)canvasDropped);
    Serial.println();
  } else if (command == "speed") {
    if (rest == "stop") {
      speed.active = false;
      Serial.println("Speed test stopped");
      return;
    }
    long target;
    if (!parseInt(rest, target) || target < 1000 || target > 1000000 || server->getConnectedCount() == 0) {
      Serial.println("speed <bytes> (1000-1000000) with the app connected | speed stop");
      return;
    }
    if (subscribed[SPEED] == 0) {
      Serial.println("Speed: subscribe to Speed (ba5e0012) first");
      return;
    }
    startSpeed(server->getPeerDevices(false).begin()->first, target);
  } else if (command == "press") {
    pressButton(CLICK);
  } else if (command == "message" && rest.length()) {
    message = rest.substring(0, 40);
    chars[MESSAGE]->setValue(message);
    Serial.println("Message set here: a phone that reads it now gets it");
  } else if (command == "disconnect") {
    for (auto &peer : server->getPeerDevices(false)) server->disconnect(peer.first);
  } else {
    Serial.println("adv on|off | clear | canvas | speed <bytes>|stop | press | message <text> | disconnect");
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

// The last colour drawn (or written to LED) wins; else green when connected, blue blink while advertising.
static void tickLed() {
  if (ledClaimed) {
    showLed(claimed[0], claimed[1], claimed[2]);
  } else if (server->getConnectedCount() > 0) {
    showLed(0, 255, 0);
  } else if (advertising) {
    bool blink = (millis() / 500) % 2 == 0;
    showLed(0, 0, blink ? 255 : 40);
  } else {
    showLed(0, 0, 0);
  }
}

// ---- Events from the BLE task ----------------------------------------------------------------------------

static void handleEvent(const Event &event) {
  switch (event.type) {
    case EV_CONNECT:
      advertising = false;  // a connectable advertisement stops when a phone connects
      Serial.printf("+ connected: %s (%s)\n", mac(event.peer).c_str(), addressKind(event.peerType, event.peer));
      break;
    case EV_DISCONNECT:
      Serial.println("- disconnected");
      if (event.number < 3000) {
        // The board never hangs up on its own here: the phone (or its stack) did. The usual reason is
        // a phone that still holds keys for this address from an earlier firmware.
        Serial.println("  The phone hung up within 3 s without encrypting. If this repeats, forget this"
                       " board in the phone's Bluetooth settings (and in nRF Connect) and connect again.");
      }
      if (speed.active && speed.handle == event.handle) speed.active = false;
      if (server->getConnectedCount() == 0 && !advertising) advertise();  // readvertise
      break;
    case EV_MTU:
      Serial.printf("  MTU %u: up to %u bytes per packet\n", event.value, event.value - 3);
      break;
    case EV_LED:
      if (event.length != 3) {
        Serial.printf("< write LED: wants 3 bytes, got %u\n", event.length);
      } else {
        memcpy(claimed, event.data, 3);
        ledClaimed = true;
        Serial.printf("< write LED %02X %02X %02X\n", event.data[0], event.data[1], event.data[2]);
      }
      break;
    case EV_MESSAGE:
      message = String();
      for (size_t i = 0; i < event.length; i++) message += (char)event.data[i];
      Serial.printf("< write Message \"%s\"\n", message.c_str());
      break;
    case EV_SPEED: onSpeed(event.handle, event.data, event.length); break;
    case EV_LINK: onLink(event.handle, event.data, event.length); break;
  }
}

// ---- setup / loop ----------------------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  uint32_t waitedAt = millis();
  while (!Serial && since(millis(), waitedAt) < 3000) delay(10);  // USB serial: wait for the monitor

  pinMode(PULSAR_BOOT_PIN, INPUT_PULLUP);
  analogSetPinAttenuation(ANALOG_PIN, ADC_11db);

  BLEDevice::init(DEVICE_NAME);
  BLEDevice::setMTU(247);
  server = BLEDevice::createServer();
  server->setCallbacks(&serverEvents);
  registerServices();

  uint8_t own[6] = {};
  ble_addr_t ownAddr;
  if (ble_hs_id_copy_addr(BLE_ADDR_PUBLIC, ownAddr.val, nullptr) == 0)
    for (int i = 0; i < 6; i++) own[i] = ownAddr.val[5 - i];
  Serial.println("B5 Phone app on " + mac(own) + " (public)");
  advertise();
}

void loop() {
  static uint32_t sensorsAt = 0;

  String text;
  if (consoleLine(text)) run(text);

  Gesture gesture = pollButton();
  if (gesture != NONE) pressButton(gesture);

  Event event;
  while (pop(event)) handleEvent(event);
  CanvasWrite write;
  while (popCanvas(write)) onCanvas(write.data, write.length);

  if (speed.active) {
    speedStep();
  } else if (server->getConnectedCount() > 0 && since(millis(), sensorsAt) >= SENSOR_PERIOD_MS) {
    sensorsAt = millis();
    sendSensors();
  }

  benchTick();
  tickLed();
  delay(speed.active ? 1 : 10);
}
