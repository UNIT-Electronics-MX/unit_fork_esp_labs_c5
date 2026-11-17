// Pulsar C5: mix colours on the on-board WS2812 from the Serial Monitor.
// Equivalent of examples/micropython/pulsar-c5/rgb_mixer.py
//
// Commands (set the Serial Monitor to 115200 baud and "Newline"):
//   rgb R G B    -> colour, each channel 0-255   (example: rgb 255 0 0)
//   level N      -> brightness 0-255             (example: level 100)
//   off          -> turn the LED off

#include "DevLab_UANIMA.h"

#define RGB_PIN 0
#define NUMPIXELS 1

DevLab_UANIMA strip(NUMPIXELS, RGB_PIN);

int level = 40;                 // brightness 0-255
int red = 0, green = 0, blue = 0;  // current colour


void showColor() {
  strip.setPixelColor(0, strip.Color(red * level / 255,
                                     green * level / 255,
                                     blue * level / 255));
  strip.show();
}

void setup() {
  Serial.begin(115200);
  strip.begin(255);  // full hardware brightness; "level" does the scaling
  delay(1000);
  Serial.println("P2 RGB mixer ready: rgb R G B, level 0-255, or off");
  showColor();
  Serial.print("rgb> ");
}

void loop() {
  if (!Serial.available()) {
    return;
  }

  String line = Serial.readStringUntil('\n');
  line.trim();
  line.toLowerCase();


  // n = how many items were read (the same as len(parts)).
  char command[8] = "";
  int a = 0, b = 0, c = 0;
  int n = sscanf(line.c_str(), "%7s %d %d %d", command, &a, &b, &c);

  if (n == 4 && strcmp(command, "rgb") == 0 &&
      a >= 0 && a <= 255 && b >= 0 && b <= 255 && c >= 0 && c <= 255) {
    red = a;
    green = b;
    blue = c;
    showColor();
    Serial.printf("RGB %d %d %d at level %d\n", red, green, blue, level);

  } else if (n == 2 && strcmp(command, "level") == 0 && a >= 0 && a <= 255) {
    level = a;
    showColor();
    Serial.printf("Level %d\n", level);

  } else if (n == 1 && strcmp(command, "off") == 0) {
    red = green = blue = 0;
    showColor();
    Serial.println("RGB off");

  } else {
    Serial.println("Use: rgb R G B, level 0-255, or off");
  }

  Serial.print("rgb> ");
}
