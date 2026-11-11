// P6 · UART: send what you type over UART0 (TX0 GPIO11 → RX0 GPIO12 with one jumper) and print
// what comes back. The USB port stays the console because USB CDC On Boot is enabled.
#include <Arduino.h>

constexpr int8_t TX_PIN = 11;
constexpr int8_t RX_PIN = 12;

void setup() {
  Serial.begin(115200);
  Serial1.begin(115200, SERIAL_8N1, RX_PIN, TX_PIN);
  Serial.println("P6 ready: type a line and press Enter");
}

void loop() {
  while (Serial.available()) Serial1.write(Serial.read());
  if (Serial1.available()) {
    String line = Serial1.readStringUntil('\n');
    Serial.printf("loopback: %s\n", line.c_str());
  }
}
