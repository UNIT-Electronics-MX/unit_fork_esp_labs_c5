/*
 * W2 display: phone LED control plus live Wi-Fi signal on an ST7735 TFT.
 * Arduino port of examples/micropython/pulsar-c5/wifi_display.py
 *
 * Board: ESP32C5 Dev Module, core arduino-esp32 3.3.x. Libraries: WiFi, WebServer, SPI and
 * Preferences come with the core; Adafruit GFX Library (and its Adafruit BusIO) draws the picture.
 * Serial Monitor at 115200.
 *
 * Set WIFI_SSID and WIFI_PASSWORD, or join a network with W1 (scan_join) first: this sketch uses the
 * network W1 saved in NVS. Upload it, then open the printed http://IP on a phone on the same network.
 * Connect the 80x160 ST7735 as in O2 (wiring below).
 */

#include <Adafruit_GFX.h>
#include <Preferences.h>
#include <SPI.h>
#include <WebServer.h>
#include <WiFi.h>
#include <string.h>

// ---- Settings (same as the MicroPython script) ---------------------------------------------

static const char *WIFI_SSID = "UNIT_ELECTRONICS_5G";
static const char *WIFI_PASSWORD = "UNIT3l3ctr0n1cs";
static const int LED_PIN = 0;  // Pulsar C5 on-board WS2812
// ST7735 80x160 (same wiring as O2/O3)
static const int MOSI_PIN = 7;    // D11
static const int SCK_PIN = 27;    // D13
static const int SD_CS_PIN = 23;  // Deselect the on-board SD card
static const int CS_PIN = 9;      // D4
static const int DC_PIN = 4;      // A2
static const int RES_PIN = 5;     // A3
static const int WIDTH = 80;
static const int HEIGHT = 160;
static const int X_OFFSET = 26;  // the 0.96" panel sits at column 26, row 1 of the controller's RAM
static const int Y_OFFSET = 1;

static const int HISTORY = 24;
static const int NO_SIGNAL = -32768;  // an empty slot of the chart (the script keeps None there)

// ---- Types first: the Arduino builder puts function prototypes before the first function ------

struct Sample {
  bool connected;
  int rssi;  // NO_SIGNAL when not connected
  int bars;
};

// What the last drawing showed: draw again only when something changed.
struct Shown {
  bool valid;
  int rssi, bars;
  String ip, color;
};

// ---- State -----------------------------------------------------------------------------------------

static Preferences prefs;
static WebServer server(80);
static SPIClass tftSpi(FSPI);
static GFXcanvas16 canvas(WIDTH, HEIGHT);  // draw into a framebuffer and send one image: no flicker

static String ledColor = "ready";
static int history[HISTORY];
static int historyCount = 0;
static bool fatal = false;

static uint32_t since(uint32_t now, uint32_t then) {  // ticks_diff
  return now - then;
}

// ---- The ST7735 (green tab, 80x160) --------------------------------------------------------------------

static void tftCommand(uint8_t command) {
  digitalWrite(DC_PIN, LOW);
  digitalWrite(CS_PIN, LOW);
  tftSpi.write(command);
  digitalWrite(CS_PIN, HIGH);
}

static void tftData(const uint8_t *data, size_t length) {
  digitalWrite(DC_PIN, HIGH);
  digitalWrite(CS_PIN, LOW);
  tftSpi.writeBytes(data, length);
  digitalWrite(CS_PIN, HIGH);
}

static void tftCommandData(uint8_t command, std::initializer_list<uint8_t> data) {
  tftCommand(command);
  if (data.size()) tftData(data.begin(), data.size());
}

// The initg() sequence of st7735.py, then rgb(False) and invertcolor(True) as the script does.
static void tftInit() {
  pinMode(SD_CS_PIN, OUTPUT);
  digitalWrite(SD_CS_PIN, HIGH);  // deselect the on-board SD card
  pinMode(DC_PIN, OUTPUT);
  pinMode(RES_PIN, OUTPUT);
  pinMode(CS_PIN, OUTPUT);
  digitalWrite(CS_PIN, HIGH);
  tftSpi.begin(SCK_PIN, -1, MOSI_PIN, -1);
  tftSpi.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));

  digitalWrite(DC_PIN, LOW);  // hardware reset
  digitalWrite(RES_PIN, HIGH);
  delayMicroseconds(500);
  digitalWrite(RES_PIN, LOW);
  delayMicroseconds(500);
  digitalWrite(RES_PIN, HIGH);
  delay(5);

  tftCommand(0x01);  // SWRESET
  delay(120);
  tftCommand(0x11);  // SLPOUT
  delay(120);
  tftCommandData(0xB1, {0x01, 0x2C, 0x2D});                    // FRMCTR1: fastest refresh
  tftCommandData(0xB2, {0x01, 0x2C, 0x2D});                    // FRMCTR2
  tftCommandData(0xB3, {0x01, 0x2C, 0x2D, 0x01, 0x2C, 0x2D});  // FRMCTR3
  delayMicroseconds(10);
  tftCommandData(0xB4, {0x07});               // INVCTR
  tftCommandData(0xC0, {0xA2, 0x02, 0x84});   // PWCTR1
  tftCommandData(0xC1, {0xC5});               // PWCTR2
  tftCommandData(0xC2, {0x0A, 0x00});         // PWCTR3
  tftCommandData(0xC3, {0x8A, 0x2A});         // PWCTR4
  tftCommandData(0xC4, {0x8A, 0xEE});         // PWCTR5
  tftCommandData(0xC5, {0x0E});               // VMCTR1
  tftCommand(0x20);                           // INVOFF
  tftCommandData(0x36, {0x08});               // MADCTL: BGR, no rotation
  tftCommandData(0x3A, {0x05});               // COLMOD: 16 bits per pixel
  tftCommandData(0x2A, {0x00, 0x01, 0x00, WIDTH - 1});   // CASET
  tftCommandData(0x2B, {0x00, 0x01, 0x00, HEIGHT - 1});  // RASET
  tftCommandData(0xE0, {0x02, 0x1C, 0x07, 0x12, 0x37, 0x32, 0x29, 0x2D, 0x29, 0x25, 0x2B, 0x39, 0x00, 0x01,
                        0x03, 0x10});  // GMCTRP1
  tftCommandData(0xE1, {0x03, 0x1D, 0x07, 0x06, 0x2E, 0x2C, 0x29, 0x2D, 0x2E, 0x2E, 0x37, 0x3F, 0x00, 0x00,
                        0x02, 0x10});  // GMCTRN1
  tftCommand(0x13);  // NORON
  delay(10);
  tftCommand(0x29);  // DISPON
  delay(100);
  tftCommandData(0x36, {0x08});  // rgb(False)
  tftCommand(0x21);              // invertcolor(True)
}

// Send the whole framebuffer: set the window, then stream the pixels.
static void tftShow() {
  uint8_t x0 = X_OFFSET, x1 = X_OFFSET + WIDTH - 1, y0 = Y_OFFSET, y1 = Y_OFFSET + HEIGHT - 1;
  tftCommandData(0x2A, {0x00, x0, 0x00, x1});  // CASET
  tftCommandData(0x2B, {0x00, y0, 0x00, y1});  // RASET
  tftCommand(0x2C);                            // RAMWR
  digitalWrite(DC_PIN, HIGH);
  digitalWrite(CS_PIN, LOW);
  tftSpi.writePixels(canvas.getBuffer(), WIDTH * HEIGHT * 2);  // swaps each pixel: high byte first
  digitalWrite(CS_PIN, HIGH);
}

// ---- Wi-Fi ---------------------------------------------------------------------------------------------

// Short text values in NVS, stored as blobs like MicroPython's esp32.NVS.set_blob: the network W1 saved.
static String settingGet(const char *key) {
  size_t length = prefs.getBytesLength(key);
  if (length == 0 || length > 512) return "";
  uint8_t buffer[513];
  prefs.getBytes(key, buffer, length);
  buffer[length] = 0;
  return String((char *)buffer);
}

static bool connectWifi() {
  WiFi.mode(WIFI_STA);
  String ssid = WIFI_SSID, password = WIFI_PASSWORD;
  if (ssid.length() == 0) {
    prefs.begin("mpwifi", true);  // the saved network W1 shares with W2 and W3
    ssid = settingGet("ssid");
    password = settingGet("pass");
    prefs.end();
  }
  if (ssid.length() == 0) {
    Serial.println("Set WIFI_SSID/WIFI_PASSWORD, or connect with W1 first");
    return false;
  }
  Serial.printf("Joining '%s'...\n", ssid.c_str());
  WiFi.begin(ssid.c_str(), password.length() ? password.c_str() : nullptr);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (since(millis(), start) > 15000) {
      Serial.println("Wi-Fi connection timed out; check SSID and password");
      return false;
    }
    delay(250);
  }
  WiFi.setSleep(false);  // a server must stay awake to be reachable
  return true;
}

static Sample signalNow() {
  if (WiFi.status() != WL_CONNECTED) return {false, NO_SIGNAL, 0};
  int rssi = WiFi.RSSI();
  int bars = rssi >= -55 ? 4 : rssi >= -67 ? 3 : rssi >= -78 ? 2 : 1;
  return {true, rssi, bars};
}

// ---- The web page --------------------------------------------------------------------------------------

static String page() {
  String html = R"HTML(<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>Pulsar C5 W2</title><style>body{font:18px sans-serif;max-width:440px;margin:2rem auto;padding:0 1rem;background:#101827;color:#edf4ff}section{background:#202d42;padding:1rem;border-radius:14px;margin:1rem 0}a{display:inline-block;padding:.8rem;margin:.25rem;border-radius:9px;background:#d6e9ff;color:#101827;text-decoration:none}#track{height:16px;background:#46556b;border-radius:10px}#fill{height:100%;width:0;background:#64e7a5;border-radius:10px}</style></head><body><h1>Pulsar C5 · W2</h1><section><h2>Wi-Fi signal</h2><p id='rssi'>Reading…</p><div id='track'><div id='fill'></div></div><small>Signal from the connected access point, in dBm.</small></section><section><h2>RGB LED</h2><p>Colour: )HTML";
  html += ledColor;
  html += R"HTML(</p><a href='/red'>Red</a><a href='/green'>Green</a><a href='/blue'>Blue</a><a href='/off'>Off</a></section><script>async function refresh(){try{const r=await fetch('/api/signal',{cache:'no-store'});const s=await r.json();document.getElementById('rssi').textContent=s.connected?s.rssi+' dBm · '+s.bars+'/4 bars':'Wi-Fi disconnected';document.getElementById('fill').style.width=(s.bars*25)+'%'}catch(e){document.getElementById('rssi').textContent='Signal unavailable'}}refresh();setInterval(refresh,2000)</script></body></html>)HTML";
  return html;
}

static void sendPage() {
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "text/html; charset=utf-8", page());
}

static void sendSignal() {
  Sample sample = signalNow();
  String json = String("{\"connected\": ") + (sample.connected ? "true" : "false") + ", \"rssi\": " +
                (sample.connected ? String(sample.rssi) : String("null")) + ", \"bars\": " + String(sample.bars) + "}";
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json", json);
}

// The LED button: set the on-board WS2812, remember the colour name, answer with the page.
static void setColor(const char *name, uint8_t red, uint8_t green, uint8_t blue) {
  rgbLedWrite(LED_PIN, red, green, blue);
  ledColor = name;
  sendPage();
}

// ---- The display ---------------------------------------------------------------------------------------

// A 16-bit RGB565 colour from 0-255 red, green and blue, like st7735.TFTColor().
static uint16_t color565(uint8_t red, uint8_t green, uint8_t blue) {
  return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3);
}

static void text(const String &message, int x, int y, uint16_t color) {
  canvas.setCursor(x, y);
  canvas.setTextColor(color);
  canvas.print(message);
}

static void drawDisplay(const Sample &sample, const String &ip) {
  uint16_t black = color565(0, 0, 0);
  uint16_t white = color565(245, 245, 245);
  uint16_t gray = color565(95, 112, 130);
  uint16_t green = color565(60, 220, 110);
  uint16_t yellow = color565(250, 190, 60);
  uint16_t red = color565(245, 80, 75);
  canvas.fillScreen(black);
  text("W2 SIGNAL", 2, 2, white);
  canvas.drawFastHLine(2, 14, 76, gray);
  text("LED " + ledColor.substring(0, 6), 2, 19, white);

  int bars;
  uint16_t accent;
  if (sample.connected) {
    bars = sample.bars;
    accent = bars >= 3 ? green : bars == 2 ? yellow : red;
    text(String(sample.rssi) + " dBm", 2, 34, accent);
    text(bars >= 3 ? "GOOD" : bars == 2 ? "FAIR" : "WEAK", 2, 47, accent);
  } else {
    bars = 0;
    accent = red;
    text("NO WIFI", 2, 34, accent);
  }

  // Four fixed-height bars. Fill only those reached by the current RSSI.
  for (int index = 0; index < 4; index++) {
    int height = 7 + index * 5;
    int x = 10 + index * 17;
    int y = 80 - height;
    canvas.drawRect(x, y, 12, height, gray);
    if (index < bars) canvas.fillRect(x + 1, y + 1, 10, height - 2, accent);
  }

  text("LAST 24 S", 2, 87, white);
  canvas.drawFastHLine(2, 122, 76, gray);
  bool haveLast = false;
  int lastX = 0, lastY = 0;
  for (int index = 0; index < historyCount; index++) {
    int x = 3 + index * 3;
    if (history[index] == NO_SIGNAL) {
      haveLast = false;
      continue;
    }
    // -95 dBm is the bottom of the chart and -40 dBm is the top.
    int level = max(0, min(55, history[index] + 95));
    int y = 119 - (level * 24 / 55);
    if (haveLast) canvas.drawLine(lastX, lastY, x, y, green);
    lastX = x;
    lastY = y;
    haveLast = true;
  }

  text("IP", 2, 127, gray);
  if (ip.length()) {
    int firstDot = ip.indexOf('.');
    int secondDot = ip.indexOf('.', firstDot + 1);
    text(ip.substring(0, secondDot), 2, 138, white);
    text(ip.substring(secondDot + 1), 2, 149, white);
  } else {
    text("DISCONNECTED", 2, 138, red);
  }
  tftShow();
}

// ---- setup / loop ----------------------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  uint32_t waitedAt = millis();
  while (!Serial && since(millis(), waitedAt) < 3000) delay(10);  // USB serial: wait for the monitor

  if (!connectWifi()) {
    fatal = true;
    return;
  }
  tftInit();
  canvas.setTextWrap(false);

  server.on("/api/signal", sendSignal);
  server.on("/red", []() { setColor("red", 24, 0, 0); });
  server.on("/green", []() { setColor("green", 0, 24, 0); });
  server.on("/blue", []() { setColor("blue", 0, 0, 24); });
  server.on("/off", []() { setColor("off", 0, 0, 0); });
  server.onNotFound(sendPage);  // the page, whatever the path
  server.begin();
  Serial.printf("W2 display: open http://%s on a phone on the same Wi-Fi\n", WiFi.localIP().toString().c_str());
}

void loop() {
  static Shown previous = {};
  static uint32_t lastSample = 0;

  if (fatal) {
    delay(1000);
    return;
  }

  uint32_t now = millis();
  if (!previous.valid || since(now, lastSample) >= 1000) {
    Sample sample = signalNow();
    if (historyCount == HISTORY) {
      memmove(history, history + 1, (HISTORY - 1) * sizeof(int));
      historyCount--;
    }
    history[historyCount++] = sample.rssi;
    String ip = sample.connected ? WiFi.localIP().toString() : String("");
    drawDisplay(sample, ip);
    previous = {true, sample.rssi, sample.bars, ip, ledColor};
    lastSample = now;
  }

  server.handleClient();

  // A colour button should be reflected on the TFT immediately.
  if (ledColor != previous.color) {
    Sample sample = signalNow();
    String ip = sample.connected ? WiFi.localIP().toString() : String("");
    drawDisplay(sample, ip);
    previous = {true, sample.rssi, sample.bars, ip, ledColor};
  }
  delay(5);
}
