#include <SPI.h>

static const int PIN_SCLK = 12;
static const int PIN_MOSI = 11;
static const int PIN_MISO = 13;
static const int PIN_CS   = 9;
static const int PIN_CMD0 = 4;
static const int PIN_CMD1 = 5;
static const int PIN_CMD2 = 6;

static const int PIN_LCD_CS  = 38;
static const int PIN_LCD_DC  = 42;
static const int PIN_LCD_RES = 41;

static const uint32_t SPI_HZ = 5000000;

static const uint8_t CMD_EXEC     = 0;
static const uint8_t CMD_GO       = 1;
static const uint8_t CMD_READ_ACC = 4;

static inline void set_cmd(uint8_t c) {
    digitalWrite(PIN_CMD0, (c >> 0) & 1);
    digitalWrite(PIN_CMD1, (c >> 1) & 1);
    digitalWrite(PIN_CMD2, (c >> 2) & 1);
}

static void clm_frame(uint8_t cmd, uint16_t word) {
    set_cmd(cmd);
    digitalWrite(PIN_CS, LOW);
    SPI.transfer16(word);
    digitalWrite(PIN_CS, HIGH);
}

// load a 16-bit value into the accumulator (7 frames, once per value)
static void clm_load(uint16_t v) {
    clm_frame(CMD_EXEC, 0x9200 | ((uint16_t)(v >> 8) << 1));   // LDI R1 hi
    clm_frame(CMD_EXEC, 0x9400 | ((uint16_t)(v & 0xFF) << 1)); // LDI R2 lo
    clm_frame(CMD_EXEC, 0x8000);                               // CLRACC
    clm_frame(CMD_EXEC, 0xD048);                               // LDAC R1 HI
    clm_frame(CMD_EXEC, 0xD080);                               // LDAC R2 LO
    clm_frame(CMD_EXEC, 0xF000);                               // HALT
    clm_frame(CMD_GO,   0x0000);
    set_cmd(CMD_READ_ACC);
    delayMicroseconds(1);
}

// clock the accumulator out to the panel (1 frame)
static inline void push16() {
    digitalWrite(PIN_CS, LOW);
    SPI.transfer16(0x0000);
    digitalWrite(PIN_CS, HIGH);
}
static inline void push8() {
    digitalWrite(PIN_CS, LOW);
    SPI.transfer(0x00);
    digitalWrite(PIN_CS, HIGH);
}

static void lcd_cmd(uint8_t c) {
    clm_load((uint16_t)c << 8);
    digitalWrite(PIN_LCD_DC, LOW);
    digitalWrite(PIN_LCD_CS, LOW);
    push8();
    digitalWrite(PIN_LCD_CS, HIGH);
}
static void lcd_data(uint8_t d) {
    clm_load((uint16_t)d << 8);
    digitalWrite(PIN_LCD_DC, HIGH);
    digitalWrite(PIN_LCD_CS, LOW);
    push8();
    digitalWrite(PIN_LCD_CS, HIGH);
}

// one colour, many pixels — the whole point
static void lcd_fill(uint16_t colour, long count) {
    clm_load(colour);
    digitalWrite(PIN_LCD_DC, HIGH);
    digitalWrite(PIN_LCD_CS, LOW);
    for (long i = 0; i < count; i++) push16();
    digitalWrite(PIN_LCD_CS, HIGH);
}

static void lcd_init() {
    digitalWrite(PIN_LCD_RES, LOW);  delay(20);
    digitalWrite(PIN_LCD_RES, HIGH); delay(150);
    lcd_cmd(0x01); delay(150);
    lcd_cmd(0x11); delay(150);
    lcd_cmd(0x3A); lcd_data(0x55);
    lcd_cmd(0x36); lcd_data(0x00);
    lcd_cmd(0x21);
    lcd_cmd(0x13);
    lcd_cmd(0x29); delay(50);
}

static void lcd_window(int x0, int y0, int x1, int y1) {
    lcd_cmd(0x2A);
    lcd_data(x0 >> 8); lcd_data(x0 & 0xFF);
    lcd_data(x1 >> 8); lcd_data(x1 & 0xFF);
    lcd_cmd(0x2B);
    lcd_data(y0 >> 8); lcd_data(y0 & 0xFF);
    lcd_data(y1 >> 8); lcd_data(y1 & 0xFF);
    lcd_cmd(0x2C);
}

// 8x8 font, only the ramp characters we need
// index: 0=' ' 1='.' 2='-' 3='=' 4='#' 5='@'
static const uint8_t font8[6][8] = {
  {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00}, // space
  {0x00,0x00,0x00,0x00,0x00,0x18,0x18,0x00}, // .
  {0x00,0x00,0x00,0x7E,0x00,0x00,0x00,0x00}, // -
  {0x00,0x00,0x7E,0x00,0x7E,0x00,0x00,0x00}, // =
  {0x24,0x24,0x7E,0x24,0x7E,0x24,0x24,0x00}, // #
  {0x3C,0x42,0x9D,0xA5,0x9E,0x40,0x3C,0x00}, // @
};

static void draw_char(int cx, int cy, uint8_t glyph,
                      uint16_t fg, uint16_t bg) {
    lcd_window(cx * 8, cy * 8, cx * 8 + 7, cy * 8 + 7);
    digitalWrite(PIN_LCD_DC, HIGH);

    int cur = -1;                       // no colour loaded yet
    for (int row = 0; row < 8; row++) {
        uint8_t bits = font8[glyph][row];
        for (int col = 0; col < 8; col++) {
            int on = (bits & (0x80 >> col)) ? 1 : 0;
            if (on != cur) {
                digitalWrite(PIN_LCD_CS, HIGH);
                clm_load(on ? fg : bg);
                digitalWrite(PIN_LCD_CS, LOW);
                cur = on;
            }
            push16();
        }
    }
    digitalWrite(PIN_LCD_CS, HIGH);
}

void setup() {
    Serial.begin(115200);
    pinMode(PIN_CS, OUTPUT);      digitalWrite(PIN_CS, HIGH);
    pinMode(PIN_CMD0, OUTPUT);
    pinMode(PIN_CMD1, OUTPUT);
    pinMode(PIN_CMD2, OUTPUT);
    pinMode(PIN_LCD_CS, OUTPUT);  digitalWrite(PIN_LCD_CS, HIGH);
    pinMode(PIN_LCD_DC, OUTPUT);
    pinMode(PIN_LCD_RES, OUTPUT); digitalWrite(PIN_LCD_RES, HIGH);

    SPI.begin(PIN_SCLK, PIN_MISO, PIN_MOSI, -1);
    SPI.beginTransaction(SPISettings(SPI_HZ, MSBFIRST, SPI_MODE0));

    delay(500);
    lcd_init();
    Serial.println("filling");

    unsigned long t0 = millis();
    lcd_window(0, 0, 239, 239);
    lcd_fill(0x0000, 240L * 240L);
    Serial.printf("black fill: %lu ms\n", millis() - t0);

    unsigned long t1 = millis();
    for (int y = 0; y < 30; y++)
        for (int x = 0; x < 30; x++)
            draw_char(x, y, (x + y) % 6, 0xFFFF, 0x0000);
    Serial.printf("full grid: %lu ms\n", millis() - t1);
    Serial.println("done");
}

void loop() {}