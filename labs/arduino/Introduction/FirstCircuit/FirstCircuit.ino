#define LED_PIN 27

void setup() {
  ledcAttach(LED_PIN, 1000, 10);  // 16 bits is not possible at 1 kHz
}

void loop() {
  for (int value = 0; value <= 1023; value += 16) {
    ledcWrite(LED_PIN, value);
    delay(20);
  }
  for (int value = 1023; value >= 0; value -= 16) {
    ledcWrite(LED_PIN, value);
    delay(20);
  }
}
