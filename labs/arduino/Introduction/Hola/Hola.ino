//Pulsar C5: cycle colors on the onboard WS2812

#include "DevLab_UANIMA.h"


#define RGB_PIN 0
#define NUMPIXELS 1 // Number of LEDs in your strip

#define BRIGHTNESS 40
#define DELAYVAL 50  // Delay in milliseconds

DevLab_UANIMA strip(NUMPIXELS, RGB_PIN); // NEO_GRB + NEO_KHZ800 by default

uint32_t wheelColor(uint8_t position){
    if (position < 85){
        return strip.Color(255 - position * 3 , position * 3, 0);
    }
    if(position < 170){
    position -= 85;
        return strip.Color(0 , 255 - position *3, position * 3);
    }
    position -= 170;
    return strip.Color(position * 3, 0, 255 - position * 3);
}

void setup() {
  strip.begin(BRIGHTNESS);
  

}

void loop(){
    for(int hue = 0; hue < 256; hue++) {
        uint32_t color = wheelColor(hue);
        strip.setPixelColor(0, color);
        strip.show();
        delay(DELAYVAL);
    }
}