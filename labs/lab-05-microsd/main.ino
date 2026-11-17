// P5 · SPI: mount the microSD card, append a line, read the file back.
// SCK 27 (shared with the user LED), MISO 26, MOSI 7, CS 23.
#include <Arduino.h>
#include <SD.h>
#include <SPI.h>

constexpr int8_t SCK_PIN = 27;
constexpr int8_t MISO_PIN = 26;
constexpr int8_t MOSI_PIN = 7;
constexpr int8_t CS_PIN = 23;
constexpr char LOG_FILE[] = "/pulsar.txt";

void setup() {
  Serial.begin(115200);
  delay(1000);
  SPI.begin(SCK_PIN, MISO_PIN, MOSI_PIN, CS_PIN);
  if (!SD.begin(CS_PIN, SPI, 20000000)) {
    Serial.println("No card: is it FAT32 and fully inserted?");
    return;
  }
  Serial.printf("Card: %llu MB\n", SD.cardSize() / (1024ULL * 1024ULL));

  File out = SD.open(LOG_FILE, FILE_APPEND);
  out.printf("boot at %lu ms\n", (unsigned long)millis());
  out.close();

  File in = SD.open(LOG_FILE);
  Serial.printf("%s:\n", LOG_FILE);
  while (in.available()) Serial.write(in.read());
  in.close();
}

void loop() {}
