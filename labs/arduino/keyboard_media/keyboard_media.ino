/*
 * B6 Keyboard & media: the Pulsar C5 as a Bluetooth LE keyboard with media keys (HID over GATT).
 * Arduino port of examples/micropython/pulsar-c5/ble_b6_keyboard.py
 *
 * Board: ESP32C5 Dev Module, core arduino-esp32 3.3.x. No extra libraries: the BLE
 * library bundled with the core (BLEServer, BLEService, BLECharacteristic, BLESecurity; NimBLE inside).
 * Serial Monitor at 115200, "Both NL & CR".
 *
 * Pair "Bench B6" from the phone's or the computer's Bluetooth settings (not from nRF Connect).
 * Then type in the serial console: type hello, key cmd+space, media up. Type help for the list.
 * BOOT: click = play/pause, double-click = next track, long press = mute.
 * The LED blinks blue while advertising, is green while connected.
 * The bond (the keys of the pairing) is kept in the chip's flash (NVS), so it survives a reset.
 *
 * The HID service is built by hand with BLEService/BLECharacteristic, not with the core's
 * BLEHIDDevice: that class leaves the Report Map readable without encryption and writes the PnP ID
 * with the vendor and product bytes the wrong way round.
 */

#include <BLE2901.h>
#include <BLEDescriptor.h>
#include <BLEDevice.h>
#include <BLESecurity.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <host/ble_gap.h>
#include <host/ble_hs.h>
#include <host/ble_store.h>
#include <string.h>

// ---- Settings (same as the MicroPython script) ---------------------------------------------

static const char *DEVICE_NAME = "Bench B6";
static const uint8_t KEYBOARD_ID = 1, MEDIA_ID = 2;
static const int PULSAR_BOOT_PIN = 28;
static const int PULSAR_RGB_PIN = 0;  // Some UNIT illustrations label it GPIO8: try 8 if the LED stays dark.

// ---- Types first: the Arduino builder puts function prototypes before the first function ------

enum Gesture { NONE = 0, CLICK = 1, DOUBLE = 2, LONG = 3 };

struct NamedValue {
  const char *name;
  uint16_t value;
};
static const NamedValue MEDIA[] = {{"play", 0x00CD}, {"next", 0x00B5}, {"prev", 0x00B6},
                                   {"up", 0x00E9}, {"down", 0x00EA}, {"mute", 0x00E2}};
static const NamedValue MODIFIERS[] = {{"ctrl", 0x01}, {"shift", 0x02}, {"alt", 0x04}, {"option", 0x04},
                                       {"cmd", 0x08}, {"gui", 0x08}, {"win", 0x08}};
static const NamedValue KEYS[] = {
  {"enter", 0x28}, {"esc", 0x29}, {"backspace", 0x2A}, {"tab", 0x2B}, {"space", 0x2C}, {"delete", 0x4C},
  {"right", 0x4F}, {"left", 0x50}, {"down", 0x51}, {"up", 0x52}, {"home", 0x4A}, {"end", 0x4D},
  {"pageup", 0x4B}, {"pagedown", 0x4E},
  {"f1", 0x3A}, {"f2", 0x3B}, {"f3", 0x3C}, {"f4", 0x3D}, {"f5", 0x3E}, {"f6", 0x3F},
  {"f7", 0x40}, {"f8", 0x41}, {"f9", 0x42}, {"f10", 0x43}, {"f11", 0x44}, {"f12", 0x45},
};

// US layout: the character a key types, unshifted and shifted, from usage 0x2D on.
struct Punctuation {
  uint8_t usage;
  char plain, shifted;
};
static const Punctuation PUNCTUATION[] = {
  {0x2D, '-', '_'}, {0x2E, '=', '+'}, {0x2F, '[', '{'}, {0x30, ']', '}'}, {0x31, '\\', '|'},
  {0x33, ';', ':'}, {0x34, '\'', '"'}, {0x35, '`', '~'}, {0x36, ',', '<'}, {0x37, '.', '>'}, {0x38, '/', '?'},
};
static const char *SHIFTED_DIGITS = "!@#$%^&*()";

// The Report Map: a small program in the HID descriptor language that tells the computer
// what our two reports mean. Report 1 = keyboard (8 bytes), report 2 = one consumer usage (2 bytes).
static const uint8_t REPORT_MAP[] = {
  0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x85, KEYBOARD_ID,  // Keyboard, Report ID 1
  0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
  0x75, 0x01, 0x95, 0x08, 0x81, 0x02,                    // byte 0: one bit per modifier
  0x95, 0x01, 0x75, 0x08, 0x81, 0x01,                    // byte 1: reserved
  0x95, 0x05, 0x75, 0x01, 0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x91, 0x02,  // LEDs out
  0x95, 0x01, 0x75, 0x03, 0x91, 0x01,                    // pad the LEDs to a byte
  0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0x65,
  0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00,        // bytes 2-7: up to six keys held
  0xC0,
  0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01, 0x85, MEDIA_ID,     // Consumer Control, Report ID 2
  0x15, 0x00, 0x26, 0xFF, 0x03, 0x19, 0x00, 0x2A, 0xFF, 0x03,
  0x75, 0x10, 0x95, 0x01, 0x81, 0x00,                    // one 16-bit usage, 0 = released
  0xC0,
};

struct UsageKey {
  bool found;
  uint8_t modifiers;
  uint8_t usage;
};

// What the BLE task tells loop(): connections, lock lights. loop() prints (no Serial from the BLE task).
enum EventType { EV_CONNECT, EV_DISCONNECT, EV_MTU, EV_ENCRYPTION, EV_LEDS };
struct Event {
  uint8_t type;
  uint8_t peerType;
  uint8_t peer[6];  // big-endian: the way it is written
  uint16_t handle;  // connection handle
  uint16_t value;   // MTU
  uint32_t number;  // ms the connection lasted
  uint8_t data[4];
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
static BLECharacteristic *keyboardIn = nullptr, *mediaIn = nullptr, *ledsOut = nullptr;
static volatile uint32_t connectedAt[8];
static bool encryptedLink[8];  // loop() only: connection handle -> the link got encrypted
static bool advertising = false;

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
    push(event);
  }
  void onMtuChanged(BLEServer *, ble_gap_conn_desc *, uint16_t mtu) override {
    Event event = {};
    event.type = EV_MTU;
    event.value = mtu;
    push(event);
  }
};

// The computer writes the lock lights (Num, Caps) to the keyboard's output report.
class WriteEvents : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *chr, ble_gap_conn_desc *) override {
    if (chr != ledsOut || chr->getLength() == 0) return;
    Event event = {};
    event.type = EV_LEDS;
    event.data[0] = chr->getData()[0];
    push(event);
  }
};

// Just Works: no passkey, no yes/no. Only tells loop() when the link's encryption changes.
class SecurityEvents : public BLESecurityCallbacks {
 public:
  using BLESecurityCallbacks::onAuthenticationComplete;
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

static int bondCount() {
  int count = 0;
  ble_store_util_count(BLE_STORE_OBJ_TYPE_PEER_SEC, &count);
  return count;
}

// ---- The HID service -------------------------------------------------------------------------------------

// A report characteristic (0x2A4D) with its Report Reference descriptor (0x2908): (ID, 1 input / 2 output).
static BLECharacteristic *makeReport(BLEService *service, uint32_t properties, uint8_t id, uint8_t type) {
  BLECharacteristic *chr = service->createCharacteristic(BLEUUID((uint16_t)0x2A4D), properties);
  BLEDescriptor *reference = new BLEDescriptor(BLEUUID((uint16_t)0x2908));
  reference->setAccessPermissions(ESP_GATT_PERM_READ);
  uint8_t value[2] = {id, type};
  reference->setValue(value, 2);
  chr->addDescriptor(reference);
  return chr;
}

static void registerServices() {
  using C = BLECharacteristic;
  const uint32_t ENC_READ = C::PROPERTY_READ | C::PROPERTY_READ_ENC;

  BLEService *hid = server->createService(BLEUUID((uint16_t)0x1812), 40);
  BLECharacteristic *info = hid->createCharacteristic(BLEUUID((uint16_t)0x2A4A), C::PROPERTY_READ);  // HID Information
  BLECharacteristic *reportMap = hid->createCharacteristic(BLEUUID((uint16_t)0x2A4B), ENC_READ);     // Report Map
  hid->createCharacteristic(BLEUUID((uint16_t)0x2A4C), C::PROPERTY_WRITE_NR);                        // Control Point
  BLECharacteristic *protocol = hid->createCharacteristic(BLEUUID((uint16_t)0x2A4E),
                                                          C::PROPERTY_READ | C::PROPERTY_WRITE_NR);  // Protocol Mode
  keyboardIn = makeReport(hid, ENC_READ | C::PROPERTY_NOTIFY, KEYBOARD_ID, 1);  // keyboard in
  mediaIn = makeReport(hid, ENC_READ | C::PROPERTY_NOTIFY, MEDIA_ID, 1);        // media in
  ledsOut = makeReport(hid, ENC_READ | C::PROPERTY_WRITE | C::PROPERTY_WRITE_NR | C::PROPERTY_WRITE_ENC,
                       KEYBOARD_ID, 2);                                          // Caps/Num Lock out

  const uint8_t infoValue[4] = {0x11, 0x01, 0x00, 0x02};  // HID 1.11, no country, normally connectable
  info->setValue(infoValue, sizeof(infoValue));
  reportMap->setValue((uint8_t *)REPORT_MAP, sizeof(REPORT_MAP));
  const uint8_t reportProtocol = 0x01;
  protocol->setValue(&reportProtocol, 1);
  const uint8_t zero8[8] = {};
  keyboardIn->setValue(zero8, 8);
  mediaIn->setValue(zero8, 2);
  ledsOut->setCallbacks(&writeEvents);
  hid->start();

  // The standard Device Information (180A, with the PnP ID) and Battery (180F) services.
  BLEService *deviceInfo = server->createService(BLEUUID((uint16_t)0x180A));
  deviceInfo->createCharacteristic(BLEUUID((uint16_t)0x2A29), C::PROPERTY_READ)->setValue(String("UNIT Electronics"));
  deviceInfo->createCharacteristic(BLEUUID((uint16_t)0x2A24), C::PROPERTY_READ)->setValue(String("ESP32-C5"));
  deviceInfo->createCharacteristic(BLEUUID((uint16_t)0x2A26), C::PROPERTY_READ)->setValue(String("ble_kit 0.1"));
  // PnP ID: vendor ID source 2 (USB), Espressif's 0x303A, product 0x8001, version 1.0.0: little-endian
  const uint8_t pnp[7] = {0x02, 0x3A, 0x30, 0x01, 0x80, 0x00, 0x01};
  deviceInfo->createCharacteristic(BLEUUID((uint16_t)0x2A50), C::PROPERTY_READ)->setValue(pnp, sizeof(pnp));
  deviceInfo->start();
  BLEService *battery = server->createService(BLEUUID((uint16_t)0x180F));
  const uint8_t level = 0x64;
  battery->createCharacteristic(BLEUUID((uint16_t)0x2A19), C::PROPERTY_READ | C::PROPERTY_NOTIFY)
    ->setValue(&level, 1);
  battery->start();
}

// Flags, appearance 0x03C1 (keyboard) and the HID service UUID; the name in the scan response.
static void advertise() {
  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->stop();
  uint8_t advFull[] = {0x02, 0x01, 0x06,         // flags
                       0x03, 0x19, 0xC1, 0x03,   // appearance 0x03C1: keyboard
                       0x03, 0x03, 0x12, 0x18};  // 16-bit service UUIDs: 0x1812 (HID), little-endian
  uint8_t rspBytes[2 + 8] = {1 + 8, 0x09};
  memcpy(rspBytes + 2, DEVICE_NAME, 8);
  BLEAdvertisementData advData, scanData;
  advData.addData((char *)advFull, sizeof(advFull));
  scanData.addData((char *)rspBytes, sizeof(rspBytes));
  adv->setAdvertisementData(advData);
  adv->setScanResponseData(scanData);
  adv->setMinInterval(100 * 1000 / 625);  // 100 ms, in units of 0.625 ms
  adv->setMaxInterval(100 * 1000 / 625);
  if (adv->start()) {
    advertising = true;
    Serial.printf("Advertising as %s - %d bonded device(s)\n", DEVICE_NAME, bondCount());
  } else {
    Serial.println("! advertising failed");
  }
}

// ---- Sending keys ---------------------------------------------------------------------------------------

// HID hosts only listen over an encrypted link.
static bool ready() {
  bool secure = false;
  for (auto &peer : server->getPeerDevices(false)) {
    struct ble_gap_conn_desc desc;
    if (ble_gap_conn_find(peer.first, &desc) == 0 && desc.sec_state.encrypted) secure = true;
  }
  if (!secure) Serial.println("Not paired yet: pair Bench B6 in the phone's Bluetooth settings");
  return secure;
}

static void send(BLECharacteristic *report, const uint8_t *data, size_t length) {
  report->setValue(data, length);
  report->notify();  // to the connections that subscribed
}

static void press(uint8_t modifiers, uint8_t usage) {
  const uint8_t down[8] = {modifiers, 0, usage, 0, 0, 0, 0, 0};
  const uint8_t up[8] = {};
  send(keyboardIn, down, 8);
  delay(12);
  send(keyboardIn, up, 8);  // release: the next press of the same key counts again
  delay(12);
}

// (modifiers, usage) that types this character on a US layout, if there is one.
static UsageKey usageOf(char c) {
  if (c >= 'a' && c <= 'z') return {true, 0, (uint8_t)(0x04 + c - 'a')};
  if (c >= 'A' && c <= 'Z') return {true, 0x02, (uint8_t)(0x04 + c - 'A')};
  if (c >= '1' && c <= '9') return {true, 0, (uint8_t)(0x1E + c - '1')};
  if (c == '0') return {true, 0, 0x27};
  const char *shifted = c ? strchr(SHIFTED_DIGITS, c) : nullptr;
  if (shifted) return {true, 0x02, (uint8_t)(0x1E + (shifted - SHIFTED_DIGITS))};
  if (c == ' ') return {true, 0, 0x2C};
  for (const Punctuation &key : PUNCTUATION) {
    if (c == key.plain) return {true, 0, key.usage};
    if (c == key.shifted) return {true, 0x02, key.usage};
  }
  return {false, 0, 0};
}

static void typeText(const String &text) {
  if (!ready()) return;
  int characters = 0;
  for (size_t i = 0; i < text.length();) {
    unsigned char lead = text[i];
    size_t size = lead < 0x80 ? 1 : (lead >> 5) == 0x06 ? 2 : (lead >> 4) == 0x0E ? 3 : (lead >> 3) == 0x1E ? 4 : 1;
    String character = text.substring(i, i + size);
    i += size;
    characters++;
    UsageKey key = size == 1 ? usageOf(character[0]) : UsageKey{false, 0, 0};
    if (key.found) press(key.modifiers, key.usage);
    else Serial.printf("Skipped '%s' (not on a US keyboard)\n", character.c_str());
  }
  Serial.printf("Typed %d characters (the computer's layout should be US)\n", characters);
}

static void keyCombo(const String &text) {
  String lower = text;
  lower.toLowerCase();
  uint8_t modifiers = 0, usage = 0;
  bool haveUsage = false;
  int start = 0;
  while (start <= (int)lower.length()) {
    int plus = lower.indexOf('+', start);
    String part = lower.substring(start, plus < 0 ? lower.length() : plus);
    start = plus < 0 ? lower.length() + 1 : plus + 1;
    bool known = false;
    for (const NamedValue &modifier : MODIFIERS)
      if (part == modifier.name) {
        modifiers |= modifier.value;
        known = true;
      }
    if (!known)
      for (const NamedValue &key : KEYS)
        if (part == key.name) {
          usage = key.value;
          haveUsage = known = true;
        }
    if (!known && part.length() == 1) {
      UsageKey key = usageOf(part[0]);
      if (key.found) {
        usage = key.usage;
        haveUsage = known = true;
      }
    }
    if (!known) {
      Serial.println("Unknown key " + part);
      return;
    }
  }
  if (ready()) {
    press(modifiers, haveUsage ? usage : 0);
    Serial.println("Key " + text);
  }
}

static void media(const String &name) {
  const NamedValue *found = nullptr;
  for (const NamedValue &entry : MEDIA)
    if (name == entry.name) found = &entry;
  if (!found) {
    Serial.println("media play|next|prev|up|down|mute");
    return;
  }
  if (ready()) {
    const uint8_t down[2] = {(uint8_t)(found->value & 0xFF), (uint8_t)(found->value >> 8)};
    const uint8_t up[2] = {};
    send(mediaIn, down, 2);
    delay(12);
    send(mediaIn, up, 2);
    Serial.println("Media " + name);
  }
}

// ---- Commands --------------------------------------------------------------------------------------------

static void run(const String &text) {
  int space = text.indexOf(' ');
  String command = space < 0 ? text : text.substring(0, space);
  String rest = space < 0 ? "" : text.substring(space + 1);
  command.toLowerCase();
  rest.trim();

  if (command == "type" && rest.length()) {
    typeText(rest);
  } else if (command == "key" && rest.length()) {
    keyCombo(rest);
  } else if (command == "media") {
    String name = rest;
    name.toLowerCase();
    media(name);
  } else if (command == "forget") {
    ble_store_clear();
    Serial.println("Bond deleted. Forget Bench B6 on the phone or computer too");
  } else {
    Serial.println("type <text> | key <name|combo, e.g. enter, f5, cmd+space> | media play|next|prev|up|down|mute | forget");
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

// Green when connected, blue blink while advertising.
static void tickLed() {
  if (server->getConnectedCount() > 0) {
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
      break;
    }
    case EV_LEDS:
      Serial.printf("< lock lights: Num %s Caps %s\n", event.data[0] & 1 ? "on" : "off",
                    event.data[0] & 2 ? "on" : "off");
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
  // Bonding + LE Secure Connections, Just Works: a keyboard has no screen to show a passkey.
  BLESecurity *security = new BLESecurity();
  security->setCapability(ESP_IO_CAP_NONE);
  security->setAuthenticationMode(true, false, true);
  BLEDevice::setSecurityCallbacks(&securityEvents);

  server = BLEDevice::createServer();
  server->setCallbacks(&serverEvents);
  registerServices();

  uint8_t own[6] = {};
  ble_addr_t ownAddr;
  if (ble_hs_id_copy_addr(BLE_ADDR_PUBLIC, ownAddr.val, nullptr) == 0)
    for (int i = 0; i < 6; i++) own[i] = ownAddr.val[5 - i];
  Serial.println("B6 Keyboard & media on " + mac(own) + " (public)");
  advertise();
}

void loop() {
  String text;
  if (consoleLine(text)) run(text);

  switch (pollButton()) {
    case CLICK: media("play"); break;
    case DOUBLE: media("next"); break;
    case LONG: media("mute"); break;
    default: break;
  }

  Event event;
  while (pop(event)) handleEvent(event);

  tickLed();
  delay(10);
}
