#include <SPI.h>

static const int PIN_SCLK = 12;
static const int PIN_MOSI = 11;
static const int PIN_MISO = 13;

static const int PIN_CS = 9;

static const int PIN_CMD0 = 4;
static const int PIN_CMD1 = 5;
static const int PIN_CMD2 = 6;

static const uint32_t SPI_HZ = 500000;
static const uint32_t UART_BAUD = 115200;

static const uint8_t SYNC_REQUEST = 0xA5;
static const uint8_t SYNC_REPLY = 0x5A;

static const uint8_t OP_PING = 0xFF;
static const uint16_t PING_ANSWER = 0xC1E0;

static const uint8_t ST_OK = 0x00;
static const uint8_t ST_BAD_CHECKSUM = 0x01;
static const uint8_t ST_BAD_LENGTH = 0x02;
static const uint8_t ST_BAD_COMMAND = 0x03;
static const uint8_t ST_TIMEOUT = 0x04;

static const uint8_t CMD_BUFFER = 3;
static const uint8_t MAX_PAYLOAD = 4;

static const uint32_t BYTE_TIMEOUT_MS = 200;

static bool read_byte(uint8_t *out)
{
    uint32_t start = millis();
    while (millis() - start < BYTE_TIMEOUT_MS) {
        if (Serial.available() > 0) {
            *out = (uint8_t)Serial.read();
            return true;
        }
    }
    return false;
}

static uint8_t checksum(uint8_t len, uint8_t op, const uint8_t *data)
{
    uint8_t sum = len ^ op;
    for (uint8_t i = 0; i < len; i++) {
        sum ^= data[i];
    }
    return sum;
}

static void send_reply(uint8_t status, uint16_t value)
{
    uint8_t pkt[6];
    pkt[0] = SYNC_REPLY;
    pkt[1] = status;
    pkt[2] = 2;
    pkt[3] = (uint8_t)(value >> 8);
    pkt[4] = (uint8_t)(value & 0xFF);
    pkt[5] = pkt[1] ^ pkt[2] ^ pkt[3] ^ pkt[4];
    Serial.write(pkt, 6);
}

static void spi_transaction(uint8_t cmd, uint8_t *data, uint8_t len)
{
    digitalWrite(PIN_CMD0, (cmd >> 0) & 1);
    digitalWrite(PIN_CMD1, (cmd >> 1) & 1);
    digitalWrite(PIN_CMD2, (cmd >> 2) & 1);
    delayMicroseconds(2);

    SPI.beginTransaction(SPISettings(SPI_HZ, MSBFIRST, SPI_MODE0));
    digitalWrite(PIN_CS, LOW);
    delayMicroseconds(50);

    for (uint8_t i = 0; i < len; i++) {
        data[i] = SPI.transfer(data[i]);
    }

    delayMicroseconds(1);
    digitalWrite(PIN_CS, HIGH);
    SPI.endTransaction();
}

void setup()
{
    Serial.begin(UART_BAUD);

    pinMode(PIN_CS, OUTPUT);
    digitalWrite(PIN_CS, HIGH);

    pinMode(PIN_CMD0, OUTPUT);
    pinMode(PIN_CMD1, OUTPUT);
    pinMode(PIN_CMD2, OUTPUT);
    digitalWrite(PIN_CMD0, LOW);
    digitalWrite(PIN_CMD1, LOW);
    digitalWrite(PIN_CMD2, LOW);

    SPI.begin(PIN_SCLK, PIN_MISO, PIN_MOSI, -1);

}

void loop()
{
    uint8_t byte_in;

    if (!read_byte(&byte_in)) {
        return;
    }
    if (byte_in != SYNC_REQUEST) {
        return;
    }

    uint8_t len;
    uint8_t op;
    uint8_t payload[MAX_PAYLOAD];
    uint8_t sum;

    if (!read_byte(&len)) {
        send_reply(ST_TIMEOUT, 0);
        return;
    }
    if (!read_byte(&op)) {
        send_reply(ST_TIMEOUT, 0);
        return;
    }
    if (len > MAX_PAYLOAD) {
        send_reply(ST_BAD_LENGTH, 0);
        return;
    }
    for (uint8_t i = 0; i < len; i++) {
        if (!read_byte(&payload[i])) {
            send_reply(ST_TIMEOUT, 0);
            return;
        }
    }
    if (!read_byte(&sum)) {
        send_reply(ST_TIMEOUT, 0);
        return;
    }
    if (sum != checksum(len, op, payload)) {
        send_reply(ST_BAD_CHECKSUM, 0);
        return;
    }

    if (op == OP_PING) {
        send_reply(ST_OK, PING_ANSWER);
        return;
    }
    if (op > 7) {
        send_reply(ST_BAD_COMMAND, 0);
        return;
    }

    uint8_t expected = 2;
    if (op == CMD_BUFFER) {
        expected = 4;
    }
    if (len != expected) {
        send_reply(ST_BAD_LENGTH, 0);
        return;
    }

    spi_transaction(op, payload, len);

    uint16_t value = ((uint16_t)payload[0] << 8) | (uint16_t)payload[1];
    send_reply(ST_OK, value);
}