/*
 * B8 Host: the roles swap. The Pulsar C5 is the central: it connects, explores, and hosts your BLE keyboard.
 * Arduino port of examples/micropython/pulsar-c5/ble_b8_host.py
 *
 * Board: ESP32C5 Dev Module, core arduino-esp32 3.3.x. No extra libraries: the BLE
 * library bundled with the core (BLEDevice, BLESecurity; NimBLE inside).
 * Serial Monitor at 115200, "Both NL & CR".
 *
 * Type list, connect <n>, then host for a keyboard or mouse. A keyboard pairs by typing the
 * passkey printed here, then Enter. Explore reads only the public basics (name, maker, battery),
 * never vendor data.
 * BOOT: click = next device, double-click = connect or explore, long press = disconnect.
 * The LED blinks blue while scanning and is green while connected.
 * The bond is kept in the chip's flash (NVS), so it survives a reset; the core keeps up to 3.
 *
 * Like ble_listen, it starts the stack with BLEDevice::init() and then talks to NimBLE's
 * ble_gap_* and ble_gattc_* directly: the lab walks the GATT table step by step, one operation in
 * flight at a time, and the core's BLEClient would do all that behind one blocking call.
 */

#include <BLEDevice.h>
#include <BLESecurity.h>
#include <esp_random.h>
#include <host/ble_gap.h>
#include <host/ble_hs.h>
#include <host/ble_store.h>
#include <math.h>
#include <string.h>

// ---- Settings (same as the MicroPython script) ---------------------------------------------

static const char *DEVICE_NAME = "PulsarC5-B8";
static const int PULSAR_BOOT_PIN = 28;
static const int PULSAR_RGB_PIN = 0;  // Some UNIT illustrations label it GPIO8: try 8 if the LED stays dark.
static const uint32_t FORGET_MS = 30000;  // list shows devices heard in the last 30 s
static const int MAX_DEVICES = 40;

// ---- Types first: the Arduino builder puts function prototypes before the first function ------

struct NamedUuid {
  uint16_t number;
  const char *name;
};
static const NamedUuid UUID_NAMES[] = {
  {0x1800, "Generic Access"}, {0x1801, "Generic Attribute"}, {0x180A, "Device Information"},
  {0x180F, "Battery"}, {0x1812, "HID"}, {0x2A00, "Device Name"}, {0x2A01, "Appearance"},
  {0x2A19, "Battery Level"}, {0x2A24, "Model"}, {0x2A26, "Firmware"}, {0x2A29, "Manufacturer"},
  {0x2A4A, "HID Information"}, {0x2A4B, "Report Map"}, {0x2A4D, "Report"}, {0x2A4E, "Protocol Mode"},
  {0x2A04, "Preferred Conn Params"}, {0x2A05, "Service Changed"}, {0x2A22, "Boot Keyboard In"},
  {0x2A23, "System ID"}, {0x2A27, "Hardware Rev"}, {0x2A28, "Software Rev"}, {0x2A32, "Boot Keyboard Out"},
  {0x2A33, "Boot Mouse In"}, {0x2A4C, "HID Control Point"}, {0x2A50, "PnP ID"}, {0xFE59, "Nordic DFU"},
};
static const uint16_t READ_BASICS[] = {0x2A00, 0x2A01, 0x2A19, 0x2A24, 0x2A26, 0x2A29};
static const uint16_t TEXT_BASICS[] = {0x2A00, 0x2A24, 0x2A26, 0x2A29};
static const uint16_t HID = 0x1812, REPORT = 0x2A4D, CCCD = 0x2902;
static const uint8_t PROP_READ = 0x02, PROP_NOTIFY = 0x10;
// What a US keyboard types for usage 0x04 and up (a-z, 1-0, Enter, Esc, Backspace, Tab, Space, punctuation).
static const char US_KEYS[] = "abcdefghijklmnopqrstuvwxyz1234567890\n\x1b\b\t -=[]\\#;'`,./";

enum Gesture { NONE = 0, CLICK = 1, DOUBLE = 2, LONG = 3 };

// A UUID as it travels on the air (little-endian): 2, 4 or 16 bytes.
struct Uuid {
  uint8_t length;
  uint8_t bytes[16];
};

// What the BLE task tells loop(): every callback only copies its data here, loop() does the logic.
enum EventType {
  EV_CONNECTED, EV_CONNECT_FAILED, EV_DISCONNECTED, EV_MTU, EV_ENCRYPTION,
  EV_PASSKEY_SHOW, EV_COMPARE, EV_PASSKEY_INPUT,
  EV_SERVICE, EV_SERVICES_DONE, EV_CHAR, EV_CHARS_DONE, EV_DESC, EV_DESCS_DONE,
  EV_READ, EV_READ_DONE, EV_WRITE_DONE, EV_NOTIFY
};
struct Event {
  uint8_t type;
  uint8_t peerType;
  uint8_t peer[6];  // big-endian: the way it is written
  uint16_t handle;  // connection handle
  uint16_t a, b;    // start/end handle, value handle, MTU, status
  uint8_t props;
  uint8_t flags[4];  // encryption: encrypted, authenticated, bonded, key size
  uint32_t number;   // passkey
  Uuid uuid;
  uint8_t length;
  uint8_t data[64];
};

struct Device {
  bool used;
  uint8_t address[6];  // big-endian
  uint8_t type;
  char name[32];
  int8_t rssi;
  uint32_t seen;
};

struct Service {
  uint16_t start, end;
  Uuid uuid;
};
struct Characteristic {
  uint16_t valueHandle;
  uint8_t properties;
  Uuid uuid;
};
struct Descriptor {
  uint16_t handle;
  Uuid uuid;
};

// GATT client operations run one at a time: the stack allows one in flight.
enum OpKind { OP_DISC_SERVICES, OP_DISC_CHARS, OP_READ_BASICS, OP_READ, OP_DISC_DESCS, OP_SUBSCRIBE, OP_WRITE_CCCD };
struct Op {
  uint8_t kind;
  uint16_t a, b;
};

static const int QUEUE_SIZE = 64;
static Event queue[QUEUE_SIZE];
static int queueHead = 0, queueTail = 0;
static portMUX_TYPE queueMux = portMUX_INITIALIZER_UNLOCKED;

// Discovery results must not get lost: wait a little for loop() to make room. Notifications may drop.
static void push(const Event &event, bool wait = true) {
  for (int tries = 0; tries < 500; tries++) {
    bool stored = false;
    portENTER_CRITICAL(&queueMux);
    int next = (queueHead + 1) % QUEUE_SIZE;
    if (next != queueTail) {
      queue[queueHead] = event;
      queueHead = next;
      stored = true;
    }
    portEXIT_CRITICAL(&queueMux);
    if (stored || !wait) return;
    vTaskDelay(1);
  }
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

// The devices heard: written by the scan callback (BLE task), read by loop(). A mutex guards them.
static Device devices[MAX_DEVICES];
static Device rows[MAX_DEVICES];  // snapshot of what list shows, copied under the lock
static int rowCount = 0;
static SemaphoreHandle_t tableLock;
struct Lock {
  Lock() { xSemaphoreTake(tableLock, portMAX_DELAY); }
  ~Lock() { xSemaphoreGive(tableLock); }
};

static uint8_t picked[6];
static bool havePicked = false;
static bool scanning = false;
static uint8_t ownType = BLE_OWN_ADDR_PUBLIC;

// Everything below is only touched by loop().
static const int MAX_SERVICES = 32, MAX_CHARS = 96, MAX_DESCS = 96, MAX_OPS = 128;
static int connection = -1;  // peer["connection"]
static Service services[MAX_SERVICES];
static Characteristic chars[MAX_CHARS];
static Descriptor descs[MAX_DESCS];
static int serviceCount = 0, charCount = 0, descCount = 0;
static Op ops[MAX_OPS];
static int opCount = 0;
static bool busy = false;
static String typed;
static uint8_t held[6];  // keys already down in the previous keyboard report
static int heldCount = 0;

// Pairing: set by the BLE task, answered from loop().
static volatile int compareConnection = -1;  // waiting for yes or no
static volatile uint32_t comparePasskey = 0;
static volatile int inputConnection = -1;  // waiting for a passkey typed on the console

static uint32_t since(uint32_t now, uint32_t then) {  // ticks_diff
  return now - then;
}

// ---- ble_kit pieces: formatting --------------------------------------------------------------------

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

static int uuid16(const Uuid &uuid) {
  return uuid.length == 2 ? uuid.bytes[0] | uuid.bytes[1] << 8 : -1;
}

static String uuidName(const Uuid &uuid) {
  if (uuid.length == 2) {
    int number = uuid16(uuid);
    for (const NamedUuid &known : UUID_NAMES)
      if (known.number == number) return known.name;
    char text[8];
    snprintf(text, sizeof(text), "0x%04X", number);
    return text;
  }
  // 128-bit (or 32-bit): written most significant byte first, 8-4-4-4-12
  char text[40];
  size_t n = 0;
  for (int i = uuid.length - 1; i >= 0; i--) {
    n += snprintf(text + n, sizeof(text) - n, "%02x", uuid.bytes[i]);
    if (uuid.length == 16 && (i == 12 || i == 10 || i == 8 || i == 6)) text[n++] = '-';
  }
  text[n] = 0;
  return text;
}

static bool inList(int number, const uint16_t *list, size_t count) {
  for (size_t i = 0; i < count; i++)
    if (list[i] == number) return true;
  return false;
}

static void copyUuid(const ble_uuid_any_t *source, Uuid &out) {
  out.length = 0;
  if (source->u.type == BLE_UUID_TYPE_16) {
    out.length = 2;
    out.bytes[0] = source->u16.value & 0xFF;
    out.bytes[1] = source->u16.value >> 8;
  } else if (source->u.type == BLE_UUID_TYPE_32) {
    out.length = 4;
    memcpy(out.bytes, &source->u32.value, 4);
  } else if (source->u.type == BLE_UUID_TYPE_128) {
    out.length = 16;
    memcpy(out.bytes, source->u128.value, 16);
  }
}

// ---- NimBLE callbacks (BLE task): only copy and push ------------------------------------------------------

static String adName(const uint8_t *payload, size_t size) {
  size_t i = 0;
  while (i + 1 < size) {
    size_t length = payload[i];
    if (length == 0 || i + 1 + length > size) break;
    if (payload[i + 1] == 0x09 || payload[i + 1] == 0x08) {
      String name;
      for (size_t k = 0; k < length - 1; k++) name += (char)payload[i + 2 + k];
      return name;
    }
    i += 1 + length;
  }
  return "";
}

// Scan result: connectable advertisements (and their scan responses) fill the table.
static int onScan(struct ble_gap_event *event, void *arg) {
  if (event->type != BLE_GAP_EVENT_DISC) return 0;
  const auto &disc = event->disc;
  uint8_t advType = disc.event_type;
  if (advType != BLE_HCI_ADV_RPT_EVTYPE_ADV_IND && advType != BLE_HCI_ADV_RPT_EVTYPE_DIR_IND &&
      advType != BLE_HCI_ADV_RPT_EVTYPE_SCAN_RSP)
    return 0;
  uint8_t address[6];
  for (int i = 0; i < 6; i++) address[i] = disc.addr.val[5 - i];  // NimBLE is little-endian
  String name = adName(disc.data, disc.length_data);
  uint32_t now = millis();

  Lock lock;
  Device *device = nullptr;
  for (Device &d : devices)
    if (d.used && memcmp(d.address, address, 6) == 0) {
      device = &d;
      break;
    }
  if (!device && advType == BLE_HCI_ADV_RPT_EVTYPE_SCAN_RSP) return 0;
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
  }
  device->type = disc.addr.type;
  if (name.length()) strlcpy(device->name, name.c_str(), sizeof(device->name));
  device->rssi = disc.rssi;
  device->seen = now;
  return 0;
}

static void fillPeer(Event &event, uint16_t handle) {
  struct ble_gap_conn_desc desc;
  if (ble_gap_conn_find(handle, &desc) == 0) {
    event.peerType = desc.peer_id_addr.type;
    for (int i = 0; i < 6; i++) event.peer[i] = desc.peer_id_addr.val[5 - i];
  }
}

// The connection: connect, disconnect, encryption, pairing, notifications.
static int onConnection(struct ble_gap_event *event, void *arg) {
  Event e = {};
  switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
      e.type = event->connect.status == 0 ? EV_CONNECTED : EV_CONNECT_FAILED;
      e.handle = event->connect.conn_handle;
      e.a = event->connect.status;
      if (event->connect.status == 0) fillPeer(e, e.handle);
      push(e);
      break;
    case BLE_GAP_EVENT_DISCONNECT:
      e.type = EV_DISCONNECTED;
      e.handle = event->disconnect.conn.conn_handle;
      push(e);
      break;
    case BLE_GAP_EVENT_MTU:
      e.type = EV_MTU;
      e.handle = event->mtu.conn_handle;
      e.a = event->mtu.value;
      push(e);
      break;
    case BLE_GAP_EVENT_ENC_CHANGE: {
      struct ble_gap_conn_desc desc;
      if (ble_gap_conn_find(event->enc_change.conn_handle, &desc) != 0) break;
      if (event->enc_change.status == BLE_HS_ERR_HCI_BASE + BLE_ERR_PINKEY_MISSING)
        ble_store_util_delete_peer(&desc.peer_id_addr);  // the peer lost our bond: drop it, so we can pair again
      e.type = EV_ENCRYPTION;
      e.handle = event->enc_change.conn_handle;
      e.flags[0] = event->enc_change.status == 0 && desc.sec_state.encrypted;
      e.flags[1] = desc.sec_state.authenticated;
      e.flags[2] = desc.sec_state.bonded;
      e.flags[3] = desc.sec_state.key_size;
      push(e);
      break;
    }
    case BLE_GAP_EVENT_REPEAT_PAIRING: {
      // We already have a bond with this device, but it wants to pair again: forget the old one.
      struct ble_gap_conn_desc desc;
      if (ble_gap_conn_find(event->repeat_pairing.conn_handle, &desc) != 0) return BLE_GAP_REPEAT_PAIRING_IGNORE;
      ble_store_util_delete_peer(&desc.peer_id_addr);
      return BLE_GAP_REPEAT_PAIRING_RETRY;
    }
    case BLE_GAP_EVENT_PASSKEY_ACTION: {
      uint16_t handle = event->passkey.conn_handle;
      struct ble_sm_io io = {};
      io.action = event->passkey.params.action;
      if (io.action == BLE_SM_IOACT_DISP) {  // we show six digits, the keyboard types them
        io.passkey = esp_random() % 1000000;
        ble_sm_inject_io(handle, &io);
        e.type = EV_PASSKEY_SHOW;
        e.number = io.passkey;
        push(e);
      } else if (io.action == BLE_SM_IOACT_NUMCMP) {  // both show six digits: loop() asks yes or no
        comparePasskey = event->passkey.params.numcmp;
        compareConnection = handle;
        e.type = EV_COMPARE;
        e.number = event->passkey.params.numcmp;
        push(e);
      } else if (io.action == BLE_SM_IOACT_INPUT) {  // the other device shows six digits: type them here
        inputConnection = handle;
        e.type = EV_PASSKEY_INPUT;
        push(e);
      }
      break;
    }
    case BLE_GAP_EVENT_NOTIFY_RX: {
      e.type = EV_NOTIFY;
      e.handle = event->notify_rx.conn_handle;
      e.a = event->notify_rx.attr_handle;
      size_t length = OS_MBUF_PKTLEN(event->notify_rx.om);
      e.length = length > sizeof(e.data) ? sizeof(e.data) : length;
      os_mbuf_copydata(event->notify_rx.om, 0, e.length, e.data);
      push(e, false);
      break;
    }
    default: break;
  }
  return 0;
}

static int onService(uint16_t handle, const struct ble_gatt_error *error, const struct ble_gatt_svc *svc, void *arg) {
  Event e = {};
  e.handle = handle;
  if (error->status == 0 && svc) {
    e.type = EV_SERVICE;
    e.a = svc->start_handle;
    e.b = svc->end_handle;
    copyUuid(&svc->uuid, e.uuid);
  } else {
    e.type = EV_SERVICES_DONE;  // BLE_HS_EDONE, or an error: either way nothing more comes
  }
  push(e);
  return 0;
}

static int onChar(uint16_t handle, const struct ble_gatt_error *error, const struct ble_gatt_chr *chr, void *arg) {
  Event e = {};
  e.handle = handle;
  if (error->status == 0 && chr) {
    e.type = EV_CHAR;
    e.a = chr->val_handle;
    e.props = chr->properties;
    copyUuid(&chr->uuid, e.uuid);
  } else {
    e.type = EV_CHARS_DONE;
  }
  push(e);
  return 0;
}

static int onDesc(uint16_t handle, const struct ble_gatt_error *error, uint16_t chrValHandle,
                  const struct ble_gatt_dsc *dsc, void *arg) {
  Event e = {};
  e.handle = handle;
  if (error->status == 0 && dsc) {
    e.type = EV_DESC;
    e.a = dsc->handle;
    copyUuid(&dsc->uuid, e.uuid);
  } else {
    e.type = EV_DESCS_DONE;
  }
  push(e);
  return 0;
}

static int onRead(uint16_t handle, const struct ble_gatt_error *error, struct ble_gatt_attr *attr, void *arg) {
  Event e = {};
  e.handle = handle;
  if (error->status == 0 && attr && attr->om) {
    e.type = EV_READ;
    e.a = attr->handle;
    size_t length = OS_MBUF_PKTLEN(attr->om);
    e.length = length > sizeof(e.data) ? sizeof(e.data) : length;
    os_mbuf_copydata(attr->om, 0, e.length, e.data);
    push(e);
  }
  e = {};
  e.type = EV_READ_DONE;
  e.handle = handle;
  push(e);
  return 0;
}

static int onWrite(uint16_t handle, const struct ble_gatt_error *error, struct ble_gatt_attr *attr, void *arg) {
  Event e = {};
  e.type = EV_WRITE_DONE;
  e.handle = handle;
  push(e);
  return 0;
}

// ---- Scan -----------------------------------------------------------------------------------------------

static void startScan() {
  struct ble_gap_disc_params params = {};
  params.itvl = 100 * 1000 / 625;  // 100 ms, in units of 0.625 ms
  params.window = 100 * 1000 / 625;
  params.filter_policy = BLE_HCI_SCAN_FILT_NO_WL;
  params.passive = 0;  // active: ask for scan responses, where the names often are
  params.filter_duplicates = 0;
  if (ble_hs_id_infer_auto(0, &ownType) != 0) ownType = BLE_OWN_ADDR_PUBLIC;
  int rc = ble_gap_disc(ownType, BLE_HS_FOREVER, &params, onScan, nullptr);
  if (rc != 0 && rc != BLE_HS_EALREADY) {
    Serial.printf("! scan failed (rc=%d)\n", rc);
    return;
  }
  scanning = true;
  Serial.println("Scanning for connectable devices: type list");
}

static void stopScan() {
  ble_gap_disc_cancel();
  scanning = false;
}

// Copies the devices heard in the last FORGET_MS into rows[], strongest first.
static void table() {
  uint32_t now = millis();
  rowCount = 0;
  Lock lock;
  for (const Device &d : devices) {
    if (!d.used || since(now, d.seen) >= FORGET_MS) continue;
    int at = rowCount++;
    while (at > 0 && rows[at - 1].rssi < d.rssi) {  // insertion sort, stable
      rows[at] = rows[at - 1];
      at--;
    }
    rows[at] = d;
  }
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

static void cmdList() {
  table();
  for (int n = 0; n < rowCount; n++) {
    const Device &d = rows[n];
    Serial.printf("%2d %4d dBm  %s  %-14s  %s\n", n + 1, d.rssi, mac(d.address).c_str(),
                  addressKind(d.type, d.address), d.name[0] ? d.name : "?");
  }
  if (rowCount == 0) Serial.println("Nothing connectable heard yet. Put the keyboard in pairing mode.");
}

static bool parseInt(const String &text, long &value) {
  String trimmed = text;
  trimmed.trim();
  if (trimmed.length() == 0) return false;
  char *end;
  value = strtol(trimmed.c_str(), &end, 10);
  return *end == 0;
}

// ---- GATT client operations: one at a time -------------------------------------------------------------

static void nextOp();
static void printTree();
static void subscribe();

static void queueOps(const Op *list, int count) {
  for (int i = 0; i < count && opCount < MAX_OPS; i++) ops[opCount++] = list[i];
  nextOp();
}

static void appendOp(uint8_t kind, uint16_t a = 0, uint16_t b = 0) {
  if (opCount < MAX_OPS) ops[opCount++] = {kind, a, b};
}

// Runs one operation. Returns true when it left something in flight (its callback calls nextOp()).
static bool runOp(const Op &op) {
  int rc = 0;
  switch (op.kind) {
    case OP_DISC_SERVICES: rc = ble_gattc_disc_all_svcs(connection, onService, nullptr); break;
    case OP_DISC_CHARS: rc = ble_gattc_disc_all_chrs(connection, op.a, op.b, onChar, nullptr); break;
    case OP_READ: rc = ble_gattc_read(connection, op.a, onRead, nullptr); break;
    case OP_DISC_DESCS: rc = ble_gattc_disc_all_dscs(connection, op.a, op.b, onDesc, nullptr); break;
    case OP_WRITE_CCCD: {
      const uint8_t enable[2] = {0x01, 0x00};
      rc = ble_gattc_write_flat(connection, op.a, enable, sizeof(enable), onWrite, nullptr);
      break;
    }
    case OP_READ_BASICS:  // print the tree, then read the public basics: nothing in flight, go straight on
      printTree();
      for (int i = 0; i < charCount; i++)
        if ((chars[i].properties & PROP_READ) && inList(uuid16(chars[i].uuid), READ_BASICS, 6))
          appendOp(OP_READ, chars[i].valueHandle);
      return false;
    case OP_SUBSCRIBE:
      subscribe();
      return false;
  }
  if (rc != 0) {
    Serial.printf("GATT operation refused (rc=%d): skipped\n", rc);
    return false;
  }
  return true;
}

static void nextOp() {
  busy = false;
  while (opCount > 0 && !busy && connection >= 0) {
    Op op = ops[0];
    memmove(ops, ops + 1, (opCount - 1) * sizeof(Op));
    opCount--;
    busy = runOp(op);
  }
}

static void explore() {
  serviceCount = charCount = descCount = 0;
  opCount = 0;
  const Op first = {OP_DISC_SERVICES, 0, 0};
  queueOps(&first, 1);
}

static void onServicesDone() {
  for (int i = 0; i < serviceCount; i++) appendOp(OP_DISC_CHARS, services[i].start, services[i].end);
  appendOp(OP_READ_BASICS);
  nextOp();
}

static void printTree() {
  Serial.printf("%d services, %d characteristics:\n", serviceCount, charCount);
  bool isHid = false;
  for (int s = 0; s < serviceCount; s++) {
    Serial.printf("  %s [%u-%u]\n", uuidName(services[s].uuid).c_str(), services[s].start, services[s].end);
    if (uuid16(services[s].uuid) == HID) isHid = true;
    for (int c = 0; c < charCount; c++) {
      const Characteristic &chr = chars[c];
      if (chr.valueHandle < services[s].start || chr.valueHandle > services[s].end) continue;
      char flags[8] = "";
      const struct {
        uint8_t bit;
        char letter;
      } order[] = {{0x02, 'R'}, {0x08, 'W'}, {0x04, 'w'}, {0x10, 'N'}, {0x20, 'I'}};
      for (const auto &flag : order)
        if (chr.properties & flag.bit) strncat(flags, &flag.letter, 1);
      Serial.printf("    %5u %-4s %s\n", chr.valueHandle, flags, uuidName(chr.uuid).c_str());
    }
  }
  if (isHid) Serial.println("It is a HID device: type host to use it");
}

static void onReadResult(const Event &event) {
  const Uuid *uuid = nullptr;
  for (int i = 0; i < charCount; i++)
    if (chars[i].valueHandle == event.a) uuid = &chars[i].uuid;
  int number = uuid ? uuid16(*uuid) : -1;
  String shown;
  if (inList(number, TEXT_BASICS, 4)) {
    for (int i = 0; i < event.length; i++) shown += (char)event.data[i];
  } else if (number == 0x2A19 && event.length) {
    shown = String(event.data[0]) + " %";
  } else {
    char hex[200];
    hexBytes(hex, sizeof(hex), event.data, event.length, ' ');
    shown = hex;
  }
  Serial.printf("  read %s: %s\n", uuid ? uuidName(*uuid).c_str() : String(event.a).c_str(), shown.c_str());
}

// ---- Connect and host ------------------------------------------------------------------------------------

static void connectTo(const String &text) {
  table();
  if (text.length()) {
    long n;
    if (!parseInt(text, n) || n < 1 || n > rowCount) {
      Serial.println("connect <n>: a row number from list");
      return;
    }
    memcpy(picked, rows[n - 1].address, 6);
    havePicked = true;
  }
  Device device;
  if (!havePicked || !findDevice(picked, device)) {
    Serial.println("list, then connect <n>");
    return;
  }
  stopScan();
  Serial.println(String("Connecting to ") + (device.name[0] ? String(device.name) : mac(picked)));
  ble_addr_t address;
  address.type = device.type;
  for (int i = 0; i < 6; i++) address.val[i] = device.address[5 - i];
  if (ble_hs_id_infer_auto(0, &ownType) != 0) ownType = BLE_OWN_ADDR_PUBLIC;
  int rc = ble_gap_connect(ownType, &address, 2000, nullptr, onConnection, nullptr);  // gives up after 2 s
  if (rc != 0) {
    Serial.printf("! connect failed (rc=%d)\n", rc);
    startScan();
  }
}

static bool isEncrypted(int handle) {
  struct ble_gap_conn_desc desc;
  return ble_gap_conn_find(handle, &desc) == 0 && desc.sec_state.encrypted;
}

static void host() {
  if (connection < 0) {
    Serial.println("connect to a keyboard or mouse first");
    return;
  }
  const Service *hid = nullptr;
  for (int i = 0; i < serviceCount; i++)
    if (uuid16(services[i].uuid) == HID) {
      hid = &services[i];
      break;
    }
  if (!hid) {
    Serial.println("No HID service here: not a keyboard or mouse");
    return;
  }
  if (!isEncrypted(connection)) {
    Serial.println("Pairing first: HID devices only talk over an encrypted link");
    ble_gap_security_initiate(connection);
    return;  // the encryption event calls host() again
  }
  const Op list[2] = {{OP_DISC_DESCS, hid->start, hid->end}, {OP_SUBSCRIBE, 0, 0}};
  queueOps(list, 2);
}

// Write 01 00 into the CCCD after each input report: the device may now notify.
static void subscribe() {
  int reports = 0;
  for (int i = 0; i < charCount; i++) {
    if (uuid16(chars[i].uuid) != REPORT || !(chars[i].properties & PROP_NOTIFY)) continue;
    reports++;
    // the CCCD is the first Client Characteristic Configuration descriptor after the value and before
    // the next characteristic
    uint32_t limit = 0xFFFF;
    for (int k = 0; k < charCount; k++)
      if (chars[k].valueHandle > chars[i].valueHandle && chars[k].valueHandle < limit) limit = chars[k].valueHandle;
    uint32_t cccd = 0xFFFFFF;
    for (int d = 0; d < descCount; d++)
      if (uuid16(descs[d].uuid) == CCCD && descs[d].handle > chars[i].valueHandle && descs[d].handle < limit &&
          descs[d].handle < cccd)
        cccd = descs[d].handle;
    if (cccd != 0xFFFFFF) appendOp(OP_WRITE_CCCD, cccd);
  }
  Serial.printf("Subscribed to %d input report(s): type or move the mouse\n", reports);
}

// ---- What the keyboard or mouse says ---------------------------------------------------------------------

static bool isHeld(uint8_t code) {
  for (int i = 0; i < heldCount; i++)
    if (held[i] == code) return true;
  return false;
}

static void onNotify(const Event &event) {
  const uint8_t *report = event.data;
  size_t length = event.length;
  char hex[200];
  hexBytes(hex, sizeof(hex), report, length, ' ');
  if (length == 8 && report[1] == 0) {  // a keyboard: modifiers, reserved, six keys
    bool shift = report[0] & 0x22;
    uint8_t keys[6];
    int keyCount = 0;
    for (int i = 2; i < 8; i++) {
      bool duplicate = false;
      for (int k = 0; k < keyCount; k++)
        if (keys[k] == report[i]) duplicate = true;
      if (report[i] && !duplicate) keys[keyCount++] = report[i];
    }
    for (int k = 0; k < keyCount; k++) {  // only keys that just went down
      uint8_t code = keys[k];
      if (isHeld(code)) continue;
      if (code >= 0x04 && code <= 0x38 && code - 0x04 < (int)sizeof(US_KEYS) - 1) {
        char c = US_KEYS[code - 0x04];
        if (shift && c >= 'a' && c <= 'z') c -= 32;
        if (c == '\n') {
          Serial.println("< typed: " + typed);
          typed = "";
        } else if (c == '\b') {
          if (typed.length()) typed.remove(typed.length() - 1);
        } else if (isalnum(c) || strchr(" -=[]\\#;'`,./", c)) {
          typed += c;
        }
      }
    }
    memcpy(held, keys, keyCount);
    heldCount = keyCount;
    Serial.printf("< key report %s | %s\n", hex, typed.c_str());
  } else if (length >= 3 && length <= 7) {  // most mice: buttons, x, y, wheel (signed)
    int x = (int8_t)report[1], y = (int8_t)report[2];
    char buttons[4] = {(char)('0' + ((report[0] >> 2) & 1)), (char)('0' + ((report[0] >> 1) & 1)),
                       (char)('0' + (report[0] & 1)), 0};
    Serial.printf("< mouse buttons %s x %+d y %+d  (%s)\n", buttons, x, y, hex);
  } else {
    Serial.printf("< report %u: %s\n", event.a, hex);
  }
}

// ---- Pairing -------------------------------------------------------------------------------------------------

// Numeric comparison: the six digits match (true) or not (false).
static void answer(bool accept) {
  if (compareConnection < 0) {
    Serial.println("Nothing to confirm right now");
    return;
  }
  struct ble_sm_io io = {};
  io.action = BLE_SM_IOACT_NUMCMP;
  io.numcmp_accept = accept;
  uint16_t handle = compareConnection;
  compareConnection = -1;
  ble_sm_inject_io(handle, &io);
  Serial.printf("You answered %s\n", accept ? "yes: the numbers match" : "no: pairing refused");
}

// Passkey entry: the six digits the other device shows.
static void enterPasskey(const String &text) {
  if (inputConnection < 0) {
    Serial.println("Nobody asked for a passkey");
    return;
  }
  long passkey;
  if (!parseInt(text, passkey) || passkey < 0) {
    Serial.println("passkey wants six digits, e.g. passkey 123456");
    return;
  }
  struct ble_sm_io io = {};
  io.action = BLE_SM_IOACT_INPUT;
  io.passkey = passkey;
  uint16_t handle = inputConnection;
  inputConnection = -1;
  ble_sm_inject_io(handle, &io);
}

// ---- Commands --------------------------------------------------------------------------------------------

static void run(const String &text) {
  int space = text.indexOf(' ');
  String command = space < 0 ? text : text.substring(0, space);
  String rest = space < 0 ? "" : text.substring(space + 1);
  command.toLowerCase();
  rest.trim();

  if (command == "list") {
    cmdList();
  } else if (command == "scan") {
    if (scanning) stopScan();
    else startScan();
  } else if (command == "connect") {
    connectTo(rest);
  } else if (command == "explore" && connection >= 0) {
    explore();
  } else if (command == "pair" && connection >= 0) {
    ble_gap_security_initiate(connection);
  } else if (command == "host") {
    host();
  } else if (command == "passkey") {
    enterPasskey(rest);
  } else if (command == "yes" || command == "no") {
    answer(command == "yes");
  } else if (command == "disconnect" && connection >= 0) {
    ble_gap_terminate(connection, BLE_ERR_REM_USER_CONN_TERM);
  } else if (command == "forget") {
    ble_store_clear();
    Serial.println("Host bonds deleted");
  } else {
    Serial.println("list | scan | connect <n> | explore | pair | host | passkey <digits> | yes | no | disconnect | forget");
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

// Green when connected, blue blink while scanning, dark otherwise.
static void tickLed() {
  if (connection >= 0) {
    showLed(0, 255, 0);
  } else if (scanning) {
    bool blink = (millis() / 500) % 2 == 0;
    showLed(0, 0, blink ? 255 : 40);
  } else {
    showLed(0, 0, 0);
  }
}

// ---- Events from the BLE task ----------------------------------------------------------------------------

static void handleEvent(const Event &event) {
  switch (event.type) {
    case EV_CONNECTED:
      Serial.printf("+ connected: %s (%s)\n", mac(event.peer).c_str(), addressKind(event.peerType, event.peer));
      connection = event.handle;
      typed = "";
      heldCount = 0;
      explore();
      break;
    case EV_CONNECT_FAILED:
      Serial.printf("Could not connect (status %u)\n", event.a);
      startScan();
      break;
    case EV_DISCONNECTED:
      Serial.println("- disconnected");
      connection = -1;
      serviceCount = charCount = descCount = 0;
      opCount = 0;
      busy = false;
      compareConnection = -1;
      inputConnection = -1;
      startScan();
      break;
    case EV_MTU:
      Serial.printf("  MTU %u: up to %u bytes per packet\n", event.a, event.a - 3);
      break;
    case EV_ENCRYPTION: {
      bool encrypted = event.flags[0];
      Serial.printf("  link %s, %s, %s, %u-byte key\n", encrypted ? "encrypted" : "NOT encrypted",
                    event.flags[1] ? "authenticated (MITM-safe)" : "Just Works",
                    event.flags[2] ? "bonded" : "not bonded", event.flags[3]);
      if (encrypted && event.handle == connection) host();
      break;
    }
    case EV_PASSKEY_SHOW:
      Serial.printf("* PASSKEY %06lu: type it on the other device\n", (unsigned long)event.number);
      break;
    case EV_COMPARE:
      Serial.printf("* COMPARE %06lu: same number on the other screen? type yes or no\n", (unsigned long)event.number);
      break;
    case EV_PASSKEY_INPUT:
      Serial.println("* Type the passkey shown on the other device: passkey 123456");
      break;
    case EV_SERVICE:
      if (serviceCount < MAX_SERVICES) services[serviceCount++] = {event.a, event.b, event.uuid};
      break;
    case EV_SERVICES_DONE: onServicesDone(); break;
    case EV_CHAR:
      if (charCount < MAX_CHARS) chars[charCount++] = {event.a, event.props, event.uuid};
      break;
    case EV_DESC:
      if (descCount < MAX_DESCS) descs[descCount++] = {event.a, event.uuid};
      break;
    case EV_CHARS_DONE:
    case EV_DESCS_DONE:
    case EV_READ_DONE:
    case EV_WRITE_DONE:
      nextOp();
      break;
    case EV_READ: onReadResult(event); break;
    case EV_NOTIFY: onNotify(event); break;
  }
}

// ---- setup / loop ----------------------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  uint32_t waitedAt = millis();
  while (!Serial && since(millis(), waitedAt) < 3000) delay(10);  // USB serial: wait for the monitor

  tableLock = xSemaphoreCreateMutex();
  pinMode(PULSAR_BOOT_PIN, INPUT_PULLUP);

  BLEDevice::init(DEVICE_NAME);
  // Bonding + LE Secure Connections + MITM, with a display and yes/no: shows a passkey to a
  // keyboard, compares six digits with a phone.
  BLESecurity *security = new BLESecurity();
  security->setCapability(ESP_IO_CAP_IO);
  security->setAuthenticationMode(true, true, true);

  uint8_t own[6] = {};
  ble_addr_t ownAddr;
  if (ble_hs_id_copy_addr(BLE_ADDR_PUBLIC, ownAddr.val, nullptr) == 0)
    for (int i = 0; i < 6; i++) own[i] = ownAddr.val[5 - i];
  Serial.println("B8 Host on " + mac(own) + " (public)");
  startScan();
}

void loop() {
  String text;
  if (consoleLine(text)) run(text);

  Gesture gesture = pollButton();
  if (gesture == CLICK) {
    table();
    if (rowCount > 0) {
      int at = -1;
      if (havePicked)
        for (int i = 0; i < rowCount; i++)
          if (memcmp(rows[i].address, picked, 6) == 0) at = i;
      int next = at >= 0 ? (at + 1) % rowCount : 0;
      memcpy(picked, rows[next].address, 6);
      havePicked = true;
      Serial.println(String("Picked ") + (rows[next].name[0] ? String(rows[next].name) : mac(picked)));
    }
  } else if (gesture == DOUBLE) {
    if (connection >= 0) explore();
    else connectTo("");
  } else if (gesture == LONG && connection >= 0) {
    ble_gap_terminate(connection, BLE_ERR_REM_USER_CONN_TERM);
  }

  Event event;
  while (pop(event)) handleEvent(event);

  tickLed();
  delay(10);
}
