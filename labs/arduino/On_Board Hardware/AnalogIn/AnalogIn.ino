#define ADC_PIN 4

void setup(){
    analogReadResolution(12);
    analogSetPinAttenuation(ADC_PIN, ADC_11db);
    Serial.begin(115200);
}

void loop(){
    uint16_t raw = analogRead(ADC_PIN);
    uint16_t mV = analogReadMilliVolts(ADC_PIN);
    Serial.printf("Raw: %d mV: %d\n", raw,mV);
    delay(200);
}