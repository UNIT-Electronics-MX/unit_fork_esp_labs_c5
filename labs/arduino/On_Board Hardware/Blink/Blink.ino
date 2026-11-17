// Pulsar C5: blink the LED and read the BOOT button.
// Equivalent of examples/micropython/pulsar-c5/gpio.py

#define LED_PIN 27
#define BOOT_PIN 28

bool lastPressed = false;
bool ledState = false;

void setup() {
  pinMode(LED_PIN, OUTPUT);
  pinMode(BOOT_PIN, INPUT_PULLUP);

  Serial.begin(115200);
  delay(1000);
  Serial.println("GPIO ready. Hold BOOT to blink faster.");
}

void loop() {
  bool pressed = digitalRead(BOOT_PIN) == LOW;  // BOOT is active low

  if (pressed != lastPressed) {
    Serial.println(pressed ? "BOOT pressed" : "BOOT released");
    lastPressed = pressed;
  }

  ledState = !ledState;
  digitalWrite(LED_PIN, ledState);

  delay(pressed ? 100 : 500);
}
