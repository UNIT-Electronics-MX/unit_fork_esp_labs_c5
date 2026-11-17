/*
 * Pulsar C5: a Bluetooth LE peripheral that notifies when a TTP223B touch pad changes.
 * Arduino port of examples/micropython/pulsar-c5/ble_touch.py
 *
 * Board: ESP32C5 Dev Module, core arduino-esp32 3.3.x. No extra libraries: the BLE
 * library bundled with the core (BLEServer, BLEService, BLECharacteristic; NimBLE inside).
 * Serial Monitor at 115200.
 *
 * Wire the TTP223B output to A3 (GPIO5). Connect from a phone (nRF Connect, LightBlue), subscribe
 * to the characteristic beb5483e-... and touch the pad: it notifies "touched" and "released".
 */

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <string.h>

static const char *DEVICE_NAME = "PulsarC5-BLE";
static const char *SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b";
static const char *TOUCH_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8";
static const uint8_t SERVICE_BYTES[16] = {0x4F, 0xAF, 0xC2, 0x01, 0x1F, 0xB5, 0x45, 0x9E,
                                          0x8F, 0xCC, 0xC5, 0xC9, 0xC3, 0x31, 0x91, 0x4B};
static const int TOUCH_PIN = 5;  // A3

static BLECharacteristic *touchCharacteristic = nullptr;
static volatile bool phoneConnected = false, phoneDisconnected = false;

// Connection events come from the NimBLE host task; loop() prints and advertises again.
class ServerEvents : public BLEServerCallbacks {
  void onConnect(BLEServer *) override { phoneConnected = true; }
  void onDisconnect(BLEServer *) override { phoneDisconnected = true; }
};
static ServerEvents serverEvents;

// The 128-bit service UUID fills most of the 31-byte advertisement, so the name goes in the scan response.
static void advertise() {
  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->stop();
  uint8_t advBytes[3 + 18] = {0x02, 0x01, 0x06, 0x11, 0x07};  // flags, then the 128-bit UUID list
  for (int i = 0; i < 16; i++) advBytes[5 + i] = SERVICE_BYTES[15 - i];  // little-endian on the air
  size_t nameLength = strlen(DEVICE_NAME);
  uint8_t rspBytes[2 + 12] = {(uint8_t)(1 + nameLength), 0x09};
  memcpy(rspBytes + 2, DEVICE_NAME, nameLength);
  BLEAdvertisementData advData, scanData;
  advData.addData((char *)advBytes, sizeof(advBytes));
  scanData.addData((char *)rspBytes, 2 + nameLength);
  adv->setAdvertisementData(advData);
  adv->setScanResponseData(scanData);
  adv->setMinInterval(100 * 1000 / 625);  // 100 ms, in units of 0.625 ms
  adv->setMaxInterval(100 * 1000 / 625);
  adv->start();
}

void setup() {
  Serial.begin(115200);
  uint32_t waitedAt = millis();
  while (!Serial && millis() - waitedAt < 3000) delay(10);  // USB serial: wait for the monitor

  pinMode(TOUCH_PIN, INPUT);

  BLEDevice::init(DEVICE_NAME);
  BLEServer *server = BLEDevice::createServer();
  server->setCallbacks(&serverEvents);
  BLEService *service = server->createService(BLEUUID(SERVICE_UUID));
  touchCharacteristic = service->createCharacteristic(
    BLEUUID(TOUCH_UUID), BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  touchCharacteristic->setValue(String("released"));
  service->start();

  advertise();
  Serial.printf("Advertising as %s\n", DEVICE_NAME);
}

void loop() {
  static bool last = false;

  if (phoneConnected) {
    phoneConnected = false;
    Serial.println("Phone connected");
  }
  if (phoneDisconnected) {
    phoneDisconnected = false;
    Serial.println("Phone disconnected, advertising again");
    advertise();
  }

  bool touched = digitalRead(TOUCH_PIN) == HIGH;
  if (touched != last) {  // notify on change only: less radio time
    last = touched;
    String state = touched ? "touched" : "released";
    touchCharacteristic->setValue(state);
    touchCharacteristic->notify();  // to the phones that subscribed
    Serial.println(state);
  }
  delay(20);
}
