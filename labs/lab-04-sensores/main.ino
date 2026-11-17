// P4 · I2C: scan the on-board sensor bus (GPIO2, 9 SDA, GPIO3, 10 SCL, 4.7 kΩ pull-ups on the board).
#include <Arduino.h>
#include <Wire.h>

constexpr uint8_t SDA_PIN = 2;
constexpr uint8_t SCL_PIN = 3;

struct Known { uint8_t address; const char* name; };
constexpr Known KNOWN[] = {
  {0x30, "MMC5603 magnetometer"},
  {0x36, "MAX17048 fuel gauge"},
  {0x44, "FHT40 temperature & humidity"},
  {0x5C, "LPS22 pressure"},
  {0x60, "VCNL4040 proximity & light"},
};

const char* nameOf(uint8_t address) {
  for (const Known& k : KNOWN) if (k.address == address) return k.name;
  return "unknown device";
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Wire.begin(SDA_PIN, SCL_PIN, 400000);
}

void loop() {
  Serial.println("Scanning 0x08–0x77…");
  uint8_t found = 0;
  for (uint8_t address = 0x08; address < 0x78; address++) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) {
      Serial.printf("  0x%02X  %s\n", address, nameOf(address));
      found++;
    }
  }
  Serial.printf("%u device(s)\n\n", found);
  delay(5000);
}
