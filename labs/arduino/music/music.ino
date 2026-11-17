/*
 * B7 Music: the Pulsar C5 as a Bluetooth MIDI instrument.
 * Arduino port of examples/micropython/pulsar-c5/ble_b7_midi.py
 *
 * Board: ESP32C5 Dev Module, core arduino-esp32 3.3.x. No extra libraries: the BLE
 * library bundled with the core (BLEServer, BLEService, BLECharacteristic; NimBLE inside).
 * Serial Monitor at 115200, "Both NL & CR". Type help in the serial console.
 *
 * MIDI carries notes, not sound: connect "Bench B7" from GarageBand (Mac, iPhone, iPad), an
 * Android synth app or the ble-app Music tab in Chrome, and that app plays them.
 * BOOT: click = next note of the scale, double-click = a chord, long press = all notes off.
 * Notes played on the other side light the RGB LED. Otherwise the LED blinks blue while
 * advertising and is green while connected.
 */

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <host/ble_gap.h>
#include <host/ble_hs.h>
#include <stdlib.h>
#include <string.h>

// ---- Settings (same as the MicroPython script) ---------------------------------------------

static const char *DEVICE_NAME = "Bench B7";
static const int PULSAR_BOOT_PIN = 28;
static const int PULSAR_RGB_PIN = 0;  // Some UNIT illustrations label it GPIO8: try 8 if the LED stays dark.

// The BLE-MIDI service and its one I/O characteristic (fixed by the MIDI spec).
static const char *MIDI_SERVICE = "03B80E5A-EDE8-4B33-A751-6CE34EC4C700";
static const char *MIDI_IO = "7772E5DB-3868-4112-A1A9-F2669D106BF3";
static const uint8_t MIDI_SERVICE_BYTES[16] = {0x03, 0xB8, 0x0E, 0x5A, 0xED, 0xE8, 0x4B, 0x33,
                                               0xA7, 0x51, 0x6C, 0xE3, 0x4E, 0xC4, 0xC7, 0x00};

// ---- Types first: the Arduino builder puts function prototypes before the first function ------

static const char *const NOTE_NAMES[12] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};

struct Scale {
  const char *name;
  uint8_t count;
  uint8_t steps[7];
};
static const Scale SCALES[] = {{"pent", 5, {0, 2, 4, 7, 9}},
                               {"minor", 5, {0, 3, 5, 7, 10}},
                               {"major", 7, {0, 2, 4, 5, 7, 9, 11}},
                               {"blues", 6, {0, 3, 5, 6, 7, 10}}};

// Public-domain tunes as (note name, beats); "-" is a rest.
struct Tune {
  const char *name;
  const char *notes;
};
static const Tune TUNES[] = {
  {"twinkle", "C4 1 C4 1 G4 1 G4 1 A4 1 A4 1 G4 2 F4 1 F4 1 E4 1 E4 1 D4 1 D4 1 C4 2"},
  {"ode", "E4 1 E4 1 F4 1 G4 1 G4 1 F4 1 E4 1 D4 1 C4 1 C4 1 D4 1 E4 1 E4 1.5 D4 .5 D4 2"},
  {"frere", "C4 1 D4 1 E4 1 C4 1 C4 1 D4 1 E4 1 C4 1 E4 1 F4 1 G4 2 E4 1 F4 1 G4 2"},
};

// 16-step drum patterns on channel 10: kick, snare, closed hat (General MIDI 36, 38, 42).
struct Beat {
  const char *name;
  const char *rows[3];
};
static const Beat BEATS[] = {
  {"rock", {"x...x...x...x...", "....x.......x...", "x.x.x.x.x.x.x.x."}},
  {"house", {"x...x...x...x...", "................", "..x...x...x...x."}},
  {"funk", {"x..x..x...x.....", "....x..x....x...", "xxxxxxxxxxxxxxxx"}},
};
static const uint8_t DRUMS[3] = {36, 38, 42};

struct State {
  const Scale *scale;
  int octave;
  int channel;
  int walk;
  int bpm;
};

struct PendingOff {
  bool used;
  uint32_t at;
  uint8_t note, channel;
};

struct TuneNote {
  uint32_t at;
  int note;  // -1 = rest
  uint32_t length;
};

struct Drums {
  bool on;
  const Beat *beat;
  int step;
  uint32_t at;
};

// One MIDI message: status byte and up to two data bytes.
struct Message {
  uint8_t bytes[3];
  uint8_t size;
};

enum Gesture { NONE = 0, CLICK = 1, DOUBLE = 2, LONG = 3 };

// What the BLE task tells loop(): connections and the MIDI the other side sends. loop() prints.
enum EventType { EV_CONNECT, EV_DISCONNECT, EV_MTU, EV_MIDI };
struct Event {
  uint8_t type;
  uint8_t peerType;
  uint8_t peer[6];  // big-endian: the way it is written
  uint16_t handle;  // connection handle
  uint16_t value;   // MTU
  uint32_t number;  // ms the connection lasted
  uint8_t length;
  uint8_t data[64];
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
static BLECharacteristic *midiIo = nullptr;
static volatile uint32_t connectedAt[8];
static bool advertising = false;

static State state = {&SCALES[0], 4, 0, 0, 100};
static PendingOff pendingOffs[64];
static const int TUNE_MAX = 64;
static TuneNote tune[TUNE_MAX];
static int tuneHead = 0, tuneCount = 0;
static Drums drums = {};
static bool ledClaimed = false;
static uint8_t claimed[3];

// ticks_diff(now, then) >= 0: now has reached then (works across the 32-bit wrap)
static bool reached(uint32_t now, uint32_t then) {
  return (int32_t)(now - then) >= 0;
}

static uint32_t since(uint32_t now, uint32_t then) {
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

// The other side plays notes: they come in as writes to the MIDI characteristic.
class WriteEvents : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *chr, ble_gap_conn_desc *) override {
    Event event = {};
    event.type = EV_MIDI;
    size_t length = chr->getLength();
    event.length = length > sizeof(event.data) ? sizeof(event.data) : length;
    memcpy(event.data, chr->getData(), event.length);
    push(event);
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

// ---- Notes -----------------------------------------------------------------------------------------------

// 'C4' -> 60, 'F#3' -> 54. Middle C is C4. -1 when it is not a note.
static int noteNumber(String name) {
  name.toUpperCase();
  if (name.length() < 2 || !isdigit(name[name.length() - 1])) return -1;
  String letter = name.substring(0, name.length() - 1);
  for (int base = 0; base < 12; base++)
    if (letter == NOTE_NAMES[base]) return 12 * (name[name.length() - 1] - '0' + 1) + base;
  return -1;
}

static String noteName(int number) {
  return String(NOTE_NAMES[number % 12]) + String(number / 12 - 1);
}

// ---- Sending MIDI -----------------------------------------------------------------------------------------

// One BLE-MIDI packet: a header with the top 6 bits of a 13-bit ms clock, then a
// timestamp byte (the low 7 bits) before each message. Goes to the devices that subscribed.
static void sendMessages(const Message *messages, int count) {
  uint32_t now = millis() & 0x1FFF;
  uint8_t packet[1 + 8 * 4];
  size_t n = 0;
  packet[n++] = 0x80 | (now >> 7);
  for (int i = 0; i < count && n + 1 + messages[i].size <= sizeof(packet); i++) {
    packet[n++] = 0x80 | (now & 0x7F);
    memcpy(packet + n, messages[i].bytes, messages[i].size);
    n += messages[i].size;
  }
  midiIo->setValue(packet, n);
  midiIo->notify();
}

static void sendOne(uint8_t status, uint8_t data1, uint8_t data2) {
  Message message = {{status, data1, data2}, 3};
  sendMessages(&message, 1);
}

static void addOff(uint32_t at, uint8_t note, uint8_t channel) {
  for (PendingOff &off : pendingOffs)
    if (!off.used) {
      off = {true, at, note, channel};
      return;
    }
  // all 64 are waiting: this note off is lost (the note rings until a stop)
}

static void play(int note, int velocity = 100, uint32_t lengthMs = 300, int channel = -1) {
  if (channel < 0) channel = state.channel;
  sendOne(0x90 | channel, note & 127, velocity & 127);
  addOff(millis() + lengthMs, note, channel);
}

static void allOff() {
  tuneCount = 0;
  drums.on = false;
  for (PendingOff &off : pendingOffs) off.used = false;
  Message messages[2] = {{{(uint8_t)(0xB0 | state.channel), 123, 0}, 3}, {{0xB9, 123, 0}, 3}};  // CC 123
  sendMessages(messages, 2);
  Serial.println("All notes off");
}

// Step 0, 1, 2... of the current scale, climbing octaves.
static int degree(int step) {
  int octave = step / state.scale->count, index = step % state.scale->count;
  return 12 * (state.octave + 1 + octave) + state.scale->steps[index];
}

static void nextNote() {
  state.walk = (state.walk + 1) % (state.scale->count * 2);
  int note = degree(state.walk);
  play(note);
  Serial.println("note " + noteName(note));
}

static void chord() {
  int root = state.walk;
  int notes[3];
  Message messages[3];
  for (int k = 0; k < 3; k++) {
    notes[k] = degree(root + k * 2);
    messages[k] = {{(uint8_t)(0x90 | state.channel), (uint8_t)notes[k], 96}, 3};
  }
  sendMessages(messages, 3);
  for (int note : notes) addOff(millis() + 600, note, state.channel);
  Serial.println("chord " + noteName(notes[0]) + " " + noteName(notes[1]) + " " + noteName(notes[2]));
}

static void startTune(const Tune &song) {
  uint32_t beat = 60000 / state.bpm;
  uint32_t at = millis();
  tuneHead = tuneCount = 0;
  String words = song.notes;
  int start = 0;
  while (start < (int)words.length() && tuneCount < TUNE_MAX) {
    int space = words.indexOf(' ', start);
    String name = words.substring(start, space);
    start = space + 1;
    space = words.indexOf(' ', start);
    String beats = words.substring(start, space < 0 ? words.length() : space);
    start = space < 0 ? words.length() : space + 1;
    uint32_t length = (uint32_t)(atof(beats.c_str()) * beat);
    tune[tuneCount++] = {at, name == "-" ? -1 : noteNumber(name), length * 9 / 10};
    at += length;
  }
  Serial.println(String("Playing ") + song.name);
}

// ---- The other side plays: parse the BLE-MIDI packet ---------------------------------------------------------

// Walk the packet: skip the header and each timestamp, keep running status (a message may
// leave out its status byte).
static void onMidiIn(const uint8_t *packet, size_t length) {
  size_t i = 1;
  uint8_t status = 0;
  bool expectTimestamp = true;
  while (i < length) {
    if (packet[i] & 0x80) {
      if (expectTimestamp) {
        i++;
        expectTimestamp = false;
        continue;
      }
      status = packet[i];
      i++;
    }
    if (status < 0x80 || status >= 0xF0) break;  // system messages (SysEx, clock) are not handled here
    size_t size = ((status & 0xF0) == 0xC0 || (status & 0xF0) == 0xD0) ? 1 : 2;
    const uint8_t *message = packet + i;
    size_t have = i + size <= length ? size : length - i;
    i += size;
    expectTimestamp = true;
    if (have == 2 && (status & 0xF0) == 0x90 && message[1] > 0) {
      Serial.printf("< note on %s velocity %u\n", noteName(message[0]).c_str(), message[1]);
      claimed[0] = 255;
      claimed[1] = 60 + message[0] % 12 * 16;
      claimed[2] = 160;
      ledClaimed = true;
    } else if (have == 2 && ((status & 0xF0) == 0x80 || (status & 0xF0) == 0x90)) {
      ledClaimed = false;
    }
  }
}

// ---- Commands --------------------------------------------------------------------------------------------

static bool isNumber(const String &text) {
  if (text.length() == 0) return false;
  for (size_t i = 0; i < text.length(); i++)
    if (!isdigit(text[i])) return false;
  return true;
}

static void run(const String &text) {
  int space = text.indexOf(' ');
  String command = space < 0 ? text : text.substring(0, space);
  String rest = space < 0 ? "" : text.substring(space + 1);
  command.toLowerCase();
  rest.trim();
  String lower = rest;
  lower.toLowerCase();

  const Scale *scale = nullptr;
  for (const Scale &known : SCALES)
    if (lower == known.name) scale = &known;
  const Tune *song = nullptr;
  for (const Tune &known : TUNES)
    if (lower == known.name) song = &known;
  const Beat *beat = nullptr;
  for (const Beat &known : BEATS)
    if (lower == known.name) beat = &known;

  if (command == "play" && rest.length()) {
    int start = 0;
    while (start < (int)rest.length()) {
      while (start < (int)rest.length() && rest[start] == ' ') start++;
      if (start >= (int)rest.length()) break;
      int end = rest.indexOf(' ', start);
      String word = rest.substring(start, end < 0 ? rest.length() : end);
      start = end < 0 ? rest.length() : end + 1;

      int notes[8], count = 0;
      if (word != "-") {  // chords: C4+E4+G4
        int partStart = 0;
        while (partStart <= (int)word.length() && count < 8) {
          int plus = word.indexOf('+', partStart);
          String part = word.substring(partStart, plus < 0 ? word.length() : plus);
          partStart = plus < 0 ? word.length() + 1 : plus + 1;
          int note = noteNumber(part);
          if (note < 0) {
            Serial.println("Unknown note " + word + " (write C4, F#3, chords as C4+E4+G4, rests as -)");
            return;
          }
          notes[count++] = note;
        }
      }
      Message messages[8];
      if (count) {
        for (int k = 0; k < count; k++) messages[k] = {{(uint8_t)(0x90 | state.channel), (uint8_t)notes[k], 100}, 3};
        sendMessages(messages, count);
      }
      delay(250);
      if (count) {
        for (int k = 0; k < count; k++) messages[k] = {{(uint8_t)(0x80 | state.channel), (uint8_t)notes[k], 0}, 3};
        sendMessages(messages, count);
      }
    }
  } else if (command == "tune" && song) {
    startTune(*song);
  } else if (command == "seq") {
    if (beat || ((lower == "" || lower == "on") && !drums.on)) {
      drums = {true, beat ? beat : &BEATS[0], 0, millis()};
      Serial.printf("Drums: %s %d bpm\n", drums.beat->name, state.bpm);
    } else {
      drums.on = false;
      Serial.println("Drums off");
    }
  } else if (command == "bpm" && isNumber(rest) && rest.toInt() >= 40 && rest.toInt() <= 220) {
    state.bpm = rest.toInt();
  } else if (command == "scale" && scale) {
    state.scale = scale;
    state.walk = 0;
  } else if (command == "octave" && isNumber(rest) && rest.toInt() >= 1 && rest.toInt() <= 7) {
    state.octave = rest.toInt();
  } else if (command == "channel" && isNumber(rest) && rest.toInt() >= 1 && rest.toInt() <= 16) {
    state.channel = rest.toInt() - 1;
  } else if (command == "chord") {
    chord();
  } else if (command == "stop") {
    allOff();
  } else {
    Serial.println("play C4 E4 G4 | play C4+E4+G4 | tune twinkle|ode|frere | seq [rock|house|funk|off] |"
                   " bpm <40-220> | scale pent|minor|major|blues | octave <1-7> | channel <1-16> | chord | stop");
  }
}

// Note offs, the tune and the drum machine, all from the main loop.
static void tick() {
  uint32_t now = millis();
  for (PendingOff &off : pendingOffs) {
    if (off.used && reached(now, off.at)) {
      off.used = false;
      sendOne(0x80 | off.channel, off.note, 0);
    }
  }
  while (tuneCount > 0 && reached(now, tune[tuneHead].at)) {
    TuneNote item = tune[tuneHead];
    tuneHead = (tuneHead + 1) % TUNE_MAX;
    tuneCount--;
    if (item.note >= 0) play(item.note, 100, item.length);
  }
  if (drums.on && reached(now, drums.at)) {
    Message hits[3];
    int count = 0;
    for (int row = 0; row < 3; row++)
      if (drums.beat->rows[row][drums.step] == 'x') hits[count++] = {{0x99, DRUMS[row], 110}, 3};
    if (count) sendMessages(hits, count);
    drums.step = (drums.step + 1) % 16;
    drums.at += 15000 / state.bpm;  // a sixteenth note
  }
}

// ---- Advertising ------------------------------------------------------------------------------------------

// GarageBand and Audio MIDI Setup look for the MIDI service UUID in the advertisement.
static void advertise() {
  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->stop();
  uint8_t advBytes[21] = {0x02, 0x01, 0x06, 0x11, 0x07};  // flags, then the 128-bit UUID list
  for (int i = 0; i < 16; i++) advBytes[5 + i] = MIDI_SERVICE_BYTES[15 - i];  // little-endian on the air
  uint8_t rspBytes[2 + 8] = {1 + 8, 0x09};
  memcpy(rspBytes + 2, DEVICE_NAME, 8);
  BLEAdvertisementData advData, scanData;
  advData.addData((char *)advBytes, sizeof(advBytes));
  scanData.addData((char *)rspBytes, sizeof(rspBytes));
  adv->setAdvertisementData(advData);
  adv->setScanResponseData(scanData);
  adv->setMinInterval(100 * 1000 / 625);  // 100 ms, in units of 0.625 ms
  adv->setMaxInterval(100 * 1000 / 625);
  if (adv->start()) {
    advertising = true;
    Serial.println("Advertising as MIDI instrument Bench B7");
  } else {
    Serial.println("! advertising failed");
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

// A note played on the other side wins; else green when connected, blue blink while advertising.
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
      if (server->getConnectedCount() == 0 && !advertising) advertise();  // readvertise
      break;
    case EV_MTU:
      Serial.printf("  MTU %u: up to %u bytes per packet\n", event.value, event.value - 3);
      break;
    case EV_MIDI:
      onMidiIn(event.data, event.length);
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
  server = BLEDevice::createServer();
  server->setCallbacks(&serverEvents);

  BLEService *service = server->createService(BLEUUID(MIDI_SERVICE));
  midiIo = service->createCharacteristic(
    BLEUUID(MIDI_IO),
    BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_WRITE_NR | BLECharacteristic::PROPERTY_NOTIFY);
  midiIo->setCallbacks(&writeEvents);
  midiIo->setValue((uint8_t *)"", 0);  // a read returns an empty packet (the spec says so)
  service->start();

  uint8_t own[6] = {};
  ble_addr_t ownAddr;
  if (ble_hs_id_copy_addr(BLE_ADDR_PUBLIC, ownAddr.val, nullptr) == 0)
    for (int i = 0; i < 6; i++) own[i] = ownAddr.val[5 - i];
  Serial.println("B7 Music on " + mac(own) + " (public)");
  advertise();
}

void loop() {
  String text;
  if (consoleLine(text)) run(text);

  switch (pollButton()) {
    case CLICK: nextNote(); break;
    case DOUBLE: chord(); break;
    case LONG: allOff(); break;
    default: break;
  }

  Event event;
  while (pop(event)) handleEvent(event);

  tick();
  tickLed();
  delay(5);
}
