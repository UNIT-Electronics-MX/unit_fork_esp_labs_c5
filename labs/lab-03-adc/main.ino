// P3 · ADC: read a potentiometer on A2 (GPIO4) in raw counts and calibrated millivolts.
#include <Arduino.h>

constexpr uint8_t POT_PIN = 4;  // A2, ADC1 channel 3

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  Serial.println("raw\tmV");
}

void loop() {
  const int raw = analogRead(POT_PIN);
  const uint32_t mv = analogReadMilliVolts(POT_PIN);  // uses the chip's factory calibration
  Serial.printf("%d\t%lu\n", raw, (unsigned long)mv);
  delay(200);
}
