// P1 · GPIO: blink the user LED and read the BOOT button (Pulsar C5).
// The LED shares GPIO27 with SPI SCK; BOOT is GPIO28 with an external pull-up.
#include <Arduino.h>

constexpr uint8_t LED_PIN = 27;
constexpr uint8_t BOOT_BUTTON = 28;

uint32_t interval = 500;

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(BOOT_BUTTON, INPUT_PULLUP);
  Serial.println("P1 ready: hold BOOT to blink faster");
}

void loop() {
  const bool pressed = digitalRead(BOOT_BUTTON) == LOW;
  const uint32_t wanted = pressed ? 100 : 500;
  if (wanted != interval) {
    interval = wanted;
    Serial.printf("button %s, interval %lu ms\n", pressed ? "pressed" : "released", (unsigned long)interval);
  }
  digitalWrite(LED_PIN, !digitalRead(LED_PIN));
  delay(interval);
}
