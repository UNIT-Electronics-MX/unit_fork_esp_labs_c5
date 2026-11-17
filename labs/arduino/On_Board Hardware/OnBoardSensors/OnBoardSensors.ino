// Pulsar C5: scan the onboard sensor I2C bus.
// Equivalent of examples/micropython/pulsar-c5/i2c_scan.py

#include "Wire.h"

// The sensor bus uses GPIO2/GPIO3; QWIIC uses GPIO1/GPIO6.
constexpr int SDA_PIN = 2;
constexpr int SCL_PIN = 3;
constexpr uint32_t I2C_FREQ = 400000UL;

struct Addresses {
  uint8_t address;
  String name;
};

Addresses knownAddresses[5] = {
  {0x30, "MMC5603"},
  {0x36, "MAX17048"},
  {0x44, "FHT40"},
  {0x5C, "LPS22"},
  {0x60, "VCNL4040"}
};

void setup() {
  Serial.begin(115200);
  Wire.begin(SDA_PIN, SCL_PIN, I2C_FREQ);
  delay(1000);
}

void loop() {
  uint8_t foundDevices = 0;  // restart the count on every scan
  Serial.println("Scanning...");

  for (uint8_t address = 1; address < 127; ++address) {
    Wire.beginTransmission(address);
    uint8_t error = Wire.endTransmission();

    if (error == 0) {
      foundDevices += 1;

      // Look the address up in the table (like known.get() in Python).
      bool found = false;
      for (uint8_t i = 0; i < (sizeof(knownAddresses)/sizeof(knownAddresses[0])); i++) {
        if (knownAddresses[i].address == address) {
          Serial.printf("0x%02X  %s\n", address, knownAddresses[i].name.c_str());
          found = true;
          break;
        }
      }
      if (!found) {
        Serial.printf("0x%02X  other device\n", address);
      }
    }
  }

  Serial.printf("%d device(s)\n\n", foundDevices);
  delay(5000);
}
