// P2 · RGB mixer: control the on-board WS2812 from the serial monitor.
// Its data line is GPIO0, the D2/RGB pad.
#include <Arduino.h>

constexpr uint8_t RGB_PIN = 0;
uint8_t red = 0;
uint8_t green = 0;
uint8_t blue = 0;
uint8_t level = 40;  // of 255: the LED is bright at close range

void showColour() {
  rgbLedWrite(RGB_PIN, red * level / 255, green * level / 255, blue * level / 255);
}

bool validChannel(int value) {
  return value >= 0 && value <= 255;
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(100);
  Serial.println("P2 RGB mixer ready. Send: rgb R G B, level 0-255, or off");
}

void loop() {
  if (!Serial.available()) return;
  String command = Serial.readStringUntil('\n');
  command.trim();
  int r, g, b, value;
  char extra;
  if (sscanf(command.c_str(), "rgb %d %d %d %c", &r, &g, &b, &extra) == 3 &&
      validChannel(r) && validChannel(g) && validChannel(b)) {
    red = r;
    green = g;
    blue = b;
    showColour();
    Serial.printf("RGB %d %d %d at level %d\n", red, green, blue, level);
  } else if (sscanf(command.c_str(), "level %d %c", &value, &extra) == 1 && validChannel(value)) {
    level = value;
    showColour();
    Serial.printf("Level %d\n", level);
  } else if (command == "off") {
    red = green = blue = 0;
    showColour();
    Serial.println("RGB off");
  } else {
    Serial.println("Use: rgb R G B, level 0-255, or off");
  }
}
