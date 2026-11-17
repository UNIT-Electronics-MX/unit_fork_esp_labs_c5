/*
 * B4 Pair & trust: B3's Bench service plus a Vault only a paired phone may open.
 * Arduino port of examples/micropython/pulsar-c5/ble_b4_pair.py
 *
 * Board: ESP32C5 Dev Module, core arduino-esp32 3.3.x. No extra libraries: the BLE
 * library bundled with the core (BLEServer, BLESecurity; NimBLE inside).
 * Serial Monitor at 115200, "Both NL & CR". Type help in the serial console.
 *
 * Connect to "Bench B4" and read the Vault: the phone asks to pair. Both sides show six digits;
 * if they match, click BOOT (or type yes). Long press (or no) refuses.
 * The bond is kept in the chip's flash (NVS), so it survives a reset; the core keeps up to 3.
 * The LED blinks blue while advertising, is green while a phone is connected.
 */

#include <BLE2901.h>
#include <BLE2904.h>
#include <BLEDevice.h>
#include <BLESecurity.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <host/ble_gap.h>
#include <host/ble_hs.h>
#include <host/ble_store.h>
#include <math.h>
#include <string.h>

// ---- Settings (same as the MicroPython script) ---------------------------------------------

// True: LED and Message need an encrypted link too. Change it and upload again.
static const bool LOCK_CONTROLS = false;

static const char *DEVICE_NAME = "Bench B4";
static const int PULSAR_BOOT_PIN = 28;
static const int PULSAR_RGB_PIN = 0;  // Some UNIT illustrations label it GPIO8: try 8 if the LED stays dark.
static const uint32_t ANSWER_MS = 25000;  // the pairing times out after 30 s: answer before that

static const char *BENCH_SERVICE = "ba5e0000-b1e5-4ab1-9e5c-be7cbe7cb0a7";
static const uint8_t BENCH_UUID[16] = {0xBA, 0x5E, 0x00, 0x00, 0xB1, 0xE5, 0x4A, 0xB1,
                                       0x9E, 0x5C, 0xBE, 0x7C, 0xBE, 0x7C, 0xB0, 0xA7};

// ---- Types first: the Arduino builder puts function prototypes before the first function ------

enum Characteristic { LED = 0, MESSAGE, BUTTON, TEMPERATURE, UPTIME, VAULT, CHAR_COUNT };
enum Gesture { NONE = 0, CLICK = 1, DOUBLE = 2, LONG = 3 };
enum Sent { NOT_CONNECTED, NOT_SUBSCRIBED, SENT };
static const char *const GESTURE_NAMES[] = {"", "click", "double", "long"};

// What the BLE task tells loop(): connections, writes, pairing. loop() prints (no Serial from the BLE task).
enum EventType {
  EV_CONNECT, EV_DISCONNECT, EV_MTU, EV_LED, EV_MESSAGE, EV_VAULT,
  EV_ENCRYPTION, EV_COMPARE, EV_COMPARE_TIMEOUT, EV_NEW_REFUSED
};
struct Event {
  uint8_t type;
  uint8_t peerType;
  uint8_t peer[6];  // big-endian: the way it is written
  uint16_t handle;  // connection handle
  uint16_t value;   // MTU
  uint32_t number;  // ms the connection lasted, or the six digits to compare
  uint8_t length;
  uint8_t data[40];
};
static const int QUEUE_SIZE = 16;
static Event queue[QUEUE_SIZE];
static int queueHead = 0, queueTail = 0;
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

static BLEServer *server = nullptr;
static BLECharacteristic *chars[CHAR_COUNT];
static volatile uint32_t subscribed[CHAR_COUNT];  // bit n set: connection handle n subscribed
static volatile uint32_t connectedAt[8];
static bool encryptedLink[8];  // loop() only: connection handle -> the link got encrypted
static bool advertising = false;

// Pairing: the BLE task waits in onConfirmPIN() for loop() to set the answer.
static volatile bool pendingCompare = false;
static volatile int decision = 0;  // 0 waiting, 1 yes, -1 no
static volatile bool allowBondedOnly = false;  // allow bonded | anyone

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
  void onWrite(BLECharacteristic *chr, ble_gap_conn_desc *) override {
    Event event = {};
    event.type = chr == chars[LED] ? EV_LED : chr == chars[VAULT] ? EV_VAULT : EV_MESSAGE;
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

// Display + yes/no: both sides show six digits and the user says whether they match.
class SecurityEvents : public BLESecurityCallbacks {
 public:
  using BLESecurityCallbacks::onAuthenticationComplete;

  bool onConfirmPIN(uint32_t pin) override {
    if (allowBondedOnly) {  // a new phone: refuse at once
      Event event = {};
      event.type = EV_NEW_REFUSED;
      push(event);
      return false;
    }
    decision = 0;
    pendingCompare = true;
    Event event = {};
    event.type = EV_COMPARE;
    event.number = pin;
    push(event);
    // The host task waits here (the core wants the answer as the return value) until loop() answers.
    uint32_t waitedAt = millis();
    while (decision == 0 && since(millis(), waitedAt) < ANSWER_MS) vTaskDelay(pdMS_TO_TICKS(20));
    bool accept = decision > 0;
    if (decision == 0) {
      Event late = {};
      late.type = EV_COMPARE_TIMEOUT;
      push(late);
    }
    pendingCompare = false;
    return accept;
  }

  // The link changed its encryption: IRQ_ENCRYPTION_UPDATE.
  void onAuthenticationComplete(ble_gap_conn_desc *desc, int status) override {
    Event event = {};
    event.type = EV_ENCRYPTION;
    event.handle = desc->conn_handle;
    event.value = status;
    event.data[0] = status == 0 && desc->sec_state.encrypted;
    event.data[1] = desc->sec_state.authenticated;
    event.data[2] = desc->sec_state.bonded;
    event.data[3] = desc->sec_state.key_size;
    push(event);
  }
};

static ServerEvents serverEvents;
static WriteEvents writeEvents;
static SecurityEvents securityEvents;

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

// ---- The Bench service, with the Vault --------------------------------------------------------------

static uint16_t buttonCount = 0;
static String message;
static bool ledClaimed = false;
static uint8_t claimed[3];

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
  const uint32_t lock = LOCK_CONTROLS ? (C::PROPERTY_READ_ENC | C::PROPERTY_WRITE_ENC) : 0;
  BLEService *bench = server->createService(BLEUUID(BENCH_SERVICE));
  chars[LED] = makeCharacteristic(bench, benchUuid(1), C::PROPERTY_READ | C::PROPERTY_WRITE | lock,
                                  "LED colour: red, green, blue");
  chars[MESSAGE] = makeCharacteristic(bench, benchUuid(2),
                                      C::PROPERTY_READ | C::PROPERTY_WRITE | C::PROPERTY_WRITE_NR | lock,
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
  // The Vault: encrypted AND authenticated (MITM) to read or write. The phone has to pair first.
  chars[VAULT] = makeCharacteristic(bench, benchUuid(6),
                                    C::PROPERTY_READ | C::PROPERTY_WRITE | C::PROPERTY_READ_ENC |
                                      C::PROPERTY_READ_AUTHEN | C::PROPERTY_WRITE_ENC | C::PROPERTY_WRITE_AUTHEN,
                                    "Vault (pair first)");

  const uint8_t zero3[3] = {0, 0, 0};
  chars[LED]->setValue(zero3, 3);
  chars[BUTTON]->setValue(zero3, 3);
  chars[VAULT]->setValue(String("nothing here yet"));
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

// ---- Pairing and bonds -------------------------------------------------------------------------------

// Numeric comparison: the six digits match (true) or not (false).
static void answer(bool accept) {
  if (!pendingCompare) {
    Serial.println("Nothing to confirm right now");
    return;
  }
  decision = accept ? 1 : -1;
  Serial.printf("You answered %s\n", accept ? "yes: the numbers match" : "no: pairing refused");
}

static int bondCount() {
  int count = 0;
  ble_store_util_count(BLE_STORE_OBJ_TYPE_PEER_SEC, &count);
  return count;
}

// ---- Commands --------------------------------------------------------------------------------------------

static void run(const String &text) {
  int space = text.indexOf(' ');
  String command = space < 0 ? text : text.substring(0, space);
  String rest = space < 0 ? "" : text.substring(space + 1);
  command.toLowerCase();
  rest.trim();

  if (command == "yes") {
    answer(true);
  } else if (command == "no") {
    answer(false);
  } else if (command == "secure") {
    if (server->getConnectedCount() == 0) Serial.println("Connect a phone first");
    for (auto &peer : server->getPeerDevices(false)) {
      BLESecurity::startSecurity(peer.first);
      Serial.println("Asked the phone to encrypt (it pairs if it has no keys)");
    }
  } else if (command == "bonds") {
    Serial.printf("%d bonded device(s) in the flash (NVS)\n", bondCount());
  } else if (command == "forget") {
    ble_store_clear();
    Serial.println("Bonds deleted. Forget the board on the phone too, then pair again");
  } else if (command == "allow" && (rest == "bonded" || rest == "anyone")) {
    allowBondedOnly = rest == "bonded";
    Serial.println("Allowing " + rest);
  } else if (command == "vault" && rest.length()) {
    chars[VAULT]->setValue(rest.substring(0, 40));
    Serial.println("Vault set here");
  } else if (command == "press") {
    pressButton(CLICK);
  } else if (command == "disconnect") {
    for (auto &peer : server->getPeerDevices(false)) server->disconnect(peer.first);
  } else {
    Serial.println("yes | no | secure | bonds | forget | allow bonded|anyone | vault <text> | press | disconnect");
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

// A colour a phone chose wins; else green when connected, blue blink while advertising.
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
      if (event.handle < 8) encryptedLink[event.handle] = false;
      Serial.printf("+ connected: %s (%s)\n", mac(event.peer).c_str(), addressKind(event.peerType, event.peer));
      // "allow bonded": ask for encryption at once. A bonded phone encrypts with its saved keys;
      // a new one would have to pair, and onConfirmPIN refuses that.
      if (allowBondedOnly) BLESecurity::startSecurity(event.handle);
      break;
    case EV_DISCONNECT: {
      Serial.println("- disconnected");
      bool encrypted = event.handle < 8 && encryptedLink[event.handle];
      if (!encrypted && event.number < 3000) {
        // The board never hangs up on its own here: the phone (or its stack) did. The usual reason is
        // a phone that still holds keys for this address from an earlier firmware.
        Serial.println("  The phone hung up within 3 s without encrypting. If this repeats, forget this"
                       " board in the phone's Bluetooth settings (and in nRF Connect) and connect again.");
      }
      if (server->getConnectedCount() == 0 && !advertising) advertise();  // readvertise
      break;
    }
    case EV_MTU:
      Serial.printf("  MTU %u: up to %u bytes per packet\n", event.value, event.value - 3);
      break;
    case EV_ENCRYPTION: {
      bool encrypted = event.data[0], authenticated = event.data[1], bonded = event.data[2];
      if (event.handle < 8) encryptedLink[event.handle] = encrypted;
      Serial.printf("  link %s, %s, %s, %u-byte key\n", encrypted ? "encrypted" : "NOT encrypted",
                    authenticated ? "authenticated (MITM-safe)" : "Just Works",
                    bonded ? "bonded" : "not bonded", event.data[3]);
      if (allowBondedOnly && !(encrypted && bonded)) {
        Serial.println("Not a bonded phone: hanging up");
        server->disconnect(event.handle);
      }
      break;
    }
    case EV_COMPARE:
      Serial.printf("* COMPARE %06lu: same number on the other screen? click BOOT or type yes;"
                    " long press or no refuses\n", (unsigned long)event.number);
      break;
    case EV_COMPARE_TIMEOUT:
      Serial.println("No answer in time: pairing refused");
      break;
    case EV_NEW_REFUSED:
      Serial.println("A new phone wants to pair, but only bonded phones are allowed");
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
    case EV_VAULT:
      Serial.printf("< write Vault (%u bytes, over an encrypted link)\n", event.length);
      break;
  }
}

// ---- setup / loop ----------------------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  uint32_t waitedAt = millis();
  while (!Serial && since(millis(), waitedAt) < 3000) delay(10);  // USB serial: wait for the monitor

  pinMode(PULSAR_BOOT_PIN, INPUT_PULLUP);

  BLEDevice::init(DEVICE_NAME);
  BLEDevice::setMTU(247);
  // Bonding + LE Secure Connections + MITM, with a display and yes/no: numeric comparison.
  BLESecurity *security = new BLESecurity();
  security->setCapability(ESP_IO_CAP_IO);
  security->setAuthenticationMode(true, true, true);
  BLEDevice::setSecurityCallbacks(&securityEvents);

  server = BLEDevice::createServer();
  server->setCallbacks(&serverEvents);
  registerServices();

  uint8_t own[6] = {};
  ble_addr_t ownAddr;
  if (ble_hs_id_copy_addr(BLE_ADDR_PUBLIC, ownAddr.val, nullptr) == 0)
    for (int i = 0; i < 6; i++) own[i] = ownAddr.val[5 - i];
  Serial.println("B4 Pair & trust on " + mac(own) + " (public)");
  Serial.printf("%d bonded device(s); LED and Message %s\n", bondCount(),
                LOCK_CONTROLS ? "need encryption" : "are open");
  advertise();
}

void loop() {
  String text;
  if (consoleLine(text)) run(text);

  Gesture gesture = pollButton();
  if (pendingCompare && (gesture == CLICK || gesture == LONG)) answer(gesture == CLICK);
  else if (gesture != NONE) pressButton(gesture);

  Event event;
  while (pop(event)) handleEvent(event);

  benchTick();
  tickLed();
  delay(10);
}
