#include <SPI.h>
#include "e1m1_ssec.h"
#include "trig_q7.h"
#include "wadgfx.h"
#include "soc/gpio_reg.h"

// going to be spamming inline comments incase you know how the driver works to edit it for YOUR LCD.. since no one reads READMEs
typedef struct
{
    const map_seg_t *sg;
    const seg_height_t *hh;
    int sh;
    int8_t x0, y0, x1, y1;
} segprep_t;

#define PIN_HI(p) REG_WRITE(GPIO_OUT_W1TS_REG, 1U << (p))
#define PIN_LO(p) REG_WRITE(GPIO_OUT_W1TC_REG, 1U << (p))
#define PIN1_HI(p) REG_WRITE(GPIO_OUT1_W1TS_REG, 1U << ((p) - 32))
#define PIN1_LO(p) REG_WRITE(GPIO_OUT1_W1TC_REG, 1U << ((p) - 32))


// Pins
static const int PIN_SCLK = 12;
static const int PIN_MOSI = 11;
static const int PIN_MISO = 13;
static const int PIN_CS = 9;
static const int PIN_CMD0 = 4;
static const int PIN_CMD1 = 5;
static const int PIN_CMD2 = 6;

static const int PIN_LCD_CS = 38;
static const int PIN_LCD_DC = 42;
static const int PIN_LCD_RES = 41;

// joystick
static const int PIN_JOY_X = 7;
static const int PIN_JOY_Y = 8;
static const int PIN_JOY_SW = 14;

// the stick never centres exactly, so anything inside this is treated as rest
#define JOY_MID 2048
#define JOY_DEAD 600

static const uint32_t SPI_HZ = 5000000;


// Clementine ISA
static const uint8_t CMD_EXEC = 0;
static const uint8_t CMD_GO = 1;
static const uint8_t CMD_BUFFER = 3;
static const uint8_t CMD_ACC0 = 4;

#define I_NOP 0x0000
#define I_CLRACC 0x8000
#define I_HALT 0xF000
#define I_LDI(rd, imm) (0x9000 | ((rd) << 9) | (((imm) & 0xFF) << 1))
#define I_MOVHOST(rd) (0x9001 | ((rd) << 9))
#define I_MAC(rs, rt) (0x7000 | ((rs) << 6) | ((rt) << 3))
#define I_MVAC(rd, hi) (0xC000 | ((rd) << 9) | ((hi) << 3))
#define I_LDAC(rs, hi) (0xD000 | ((rs) << 6) | ((hi) << 3))

// Screen
#define CELL 4
#define COLS 32
#define ROWS 40
#define SCRW (COLS * CELL)
#define HALFW (COLS / 2)
#define HALFR (ROWS / 2)

// 45 degree fov over 30 columns, 32 BAM total, around 1 BAM per column
#define FOV_BAM 32

// projection scale, tuned so a wall one local unit away fills the screen
#define FOCAL 26

// near plane in depth units (around 25 map units), segments closer get clipped
#define NEAR 400

// world height -> screen rows.  FOCAL * (127 / 2^K)
#define VSCALE 413

// DOOM's player view height above the floor
#define EYE_OFF 41

// centre the image 240x240 panel
#define OX ((128 - SCRW) / 2)
#define OY ((160 - ROWS * CELL) / 2)

// gpu_div returns Q4 so the low bits are sub-cell coverage
#define G_CEIL 1
#define G_FLOOR 2
#define G_LIT 3
#define G_DIM 4
#define G_BLIT 5
#define G_EDGE 6
#define G_BDIM 7
#define G_TOP 8
#define G_BOT 14

static const uint8_t font8[20][4] = {
    {0x0, 0x0, 0x0, 0x0}, // 0 empty
    {0xF, 0xF, 0xF, 0xF}, // 1 ceiling
    {0xF, 0xF, 0xF, 0xF}, // 2 floor
    {0xF, 0xF, 0xF, 0xF}, // 3 wall lit
    {0xF, 0xF, 0xF, 0xF}, // 4 wall dim
    {0xF, 0xF, 0xF, 0xF}, // 5 band lit
    {0xF, 0xF, 0xF, 0xF}, // 6 corner edge
    {0xF, 0xF, 0xF, 0xF}, // 7 band dim
    {0x0, 0xF, 0xF, 0xF}, // 8 lit, 1/4 down
    {0x0, 0x0, 0xF, 0xF}, // 9 lit, 2/4 down
    {0x0, 0x0, 0x0, 0xF}, // 10 lit, 3/4 down
    {0x0, 0xF, 0xF, 0xF}, // 11 dim, 1/4 down
    {0x0, 0x0, 0xF, 0xF}, // 12 dim, 2/4 down
    {0x0, 0x0, 0x0, 0xF}, // 13 dim, 3/4 down
    {0xF, 0x0, 0x0, 0x0}, // 14 lit, 1/4 tall
    {0xF, 0xF, 0x0, 0x0}, // 15 lit, 2/4 tall
    {0xF, 0xF, 0xF, 0x0}, // 16 lit, 3/4 tall
    {0xF, 0x0, 0x0, 0x0}, // 17 dim, 1/4 tall
    {0xF, 0xF, 0x0, 0x0}, // 18 dim, 2/4 tall
    {0xF, 0xF, 0xF, 0x0}, // 19 dim, 3/4 tall
};

// player
static int16_t px = 1056, py = -3616; // raw map units
static uint8_t pa = 64; // BAM, 64 = north

// Framebuffers
static uint8_t cell_now[ROWS][COLS];
static uint8_t cell_was[ROWS][COLS];
static uint32_t col_depth[COLS]; // Q4 depth, 0 = untouched
static uint8_t col_glyph[COLS];

static int16_t ceil_clip[COLS]; // topmost row still open
static int16_t floor_clip[COLS]; // bottommost row still open
static uint8_t col_closed[COLS]; // a solid wall has sealed this column
static int16_t eye_h; // player eye height, raw map units

static inline void set_cmd(uint8_t c)
{
    if (c & 1)
        PIN_HI(PIN_CMD0);
    else
        PIN_LO(PIN_CMD0);
    if (c & 2)
        PIN_HI(PIN_CMD1);
    else
        PIN_LO(PIN_CMD1);
    if (c & 4)
        PIN_HI(PIN_CMD2);
    else
        PIN_LO(PIN_CMD2);
}

static void clm_frame(uint8_t cmd, uint16_t word)
{
    set_cmd(cmd);
    PIN_LO(PIN_CS);
    SPI.transfer16(word);
    PIN_HI(PIN_CS);
}

static uint16_t clm_read(uint8_t lane)
{
    set_cmd(CMD_ACC0 + lane);
    PIN_LO(PIN_CS);
    uint16_t v = SPI.transfer16(0x0000);
    PIN_HI(PIN_CS);
    return v;
}

static void clm_buffer(const int8_t v[4])
{
    set_cmd(CMD_BUFFER);
    PIN_LO(PIN_CS);
    SPI.transfer((uint8_t)v[0]);
    SPI.transfer((uint8_t)v[1]);
    SPI.transfer((uint8_t)v[2]);
    SPI.transfer((uint8_t)v[3]);
    PIN_HI(PIN_CS);
}

static void clm_run(const uint16_t *prog, int n)
{
    for (int i = 0; i < n; i++)
        clm_frame(CMD_EXEC, prog[i]);
    clm_frame(CMD_GO, 0x0000);
}

static void clm_load(uint16_t v)
{
    uint16_t p[7] = {
        I_LDI(1, v >> 8), I_LDI(2, v & 0xFF), I_CLRACC,
        I_LDAC(1, 1), I_LDAC(2, 0), I_HALT, I_NOP};
    clm_run(p, 6);
    set_cmd(CMD_ACC0);
}

static int32_t clm_cached = -1;

static void clm_load_cached(uint16_t v)
{
    if ((int32_t)v == clm_cached)
        return;
    clm_load(v);
    clm_cached = v;
}

static inline void push16(void)
{
    PIN_LO(PIN_CS);
    SPI.transfer16(0x0000);
    PIN_HI(PIN_CS);
}
static inline void push8(void)
{
    PIN_LO(PIN_CS);
    SPI.transfer(0x00);
    PIN_HI(PIN_CS);
}

static void clm_load_byte(uint8_t b)
{
    uint16_t p[5] = {I_LDI(1, b), I_CLRACC, I_LDAC(1, 1), I_HALT, I_NOP};
    clm_run(p, 4);
    set_cmd(CMD_ACC0);
}

static void lcd_cmd(uint8_t c)
{
    clm_load_byte(c);
    PIN1_LO(PIN_LCD_DC);
    PIN1_LO(PIN_LCD_CS);
    push8();
    PIN1_HI(PIN_LCD_CS);
    clm_cached = -1;
}
static void lcd_data(uint8_t d)
{
    clm_load_byte(d);
    PIN1_HI(PIN_LCD_DC);
    PIN1_LO(PIN_LCD_CS);
    push8();
    PIN1_HI(PIN_LCD_CS);
    clm_cached = -1;
}
static void lcd_fill(uint16_t colour, long count)
{
    clm_load(colour);
    PIN1_HI(PIN_LCD_DC);
    PIN1_LO(PIN_LCD_CS);
    for (long i = 0; i < count; i++)
        push16();
    PIN1_HI(PIN_LCD_CS);
    clm_cached = -1;
}

#define XOFF 0
#define YOFF 0

static void lcd_colspan(int x0, int x1)
{
    x0 += XOFF;
    x1 += XOFF;
    lcd_cmd(0x2A);
    lcd_data(x0 >> 8);
    lcd_data(x0 & 0xFF);
    lcd_data(x1 >> 8);
    lcd_data(x1 & 0xFF);
}

static void lcd_rowspan(int y0, int y1)
{
    y0 += YOFF;
    y1 += YOFF;
    lcd_cmd(0x2B);
    lcd_data(y0 >> 8);
    lcd_data(y0 & 0xFF);
    lcd_data(y1 >> 8);
    lcd_data(y1 & 0xFF);
    lcd_cmd(0x2C);
}

static void lcd_window(int x0, int y0, int x1, int y1)
{
    lcd_colspan(x0, x1);
    lcd_rowspan(y0, y1);
}

static void lcd_init(void)
{
    digitalWrite(PIN_LCD_RES, LOW);
    delay(20);
    digitalWrite(PIN_LCD_RES, HIGH);
    delay(150);
    lcd_cmd(0x01);
    delay(150);
    lcd_cmd(0x11);
    delay(150);
    lcd_cmd(0x3A);
    lcd_data(0x05);
    lcd_cmd(0x36);
    lcd_data(0x00);
    lcd_cmd(0x20);
    lcd_cmd(0x13);
    lcd_cmd(0x29);
    delay(50);
}

// Marshalling

// constants load once into R4-R7. x/y persist in R1/R2 so pass two restages nothing
static void rot_regs_init(void)
{
    int8_t c = cos_q7(pa), sn = sin_q7[pa];
    // s duplicated in R5/R6 so every MAC pairs odd with even, no bank conflicts yay
    uint16_t p[6] = {I_LDI(4, c), I_LDI(5, sn), I_LDI(6, sn), I_LDI(7, -c), I_HALT, I_NOP};
    clm_run(p, 5);
}

static void gpu_rot4(const int8_t xs[4], const int8_t ys[4], int8_t c, int8_t s, int16_t out_fwd[4], int16_t out_side[4])
{
    (void)c;
    (void)s;

    clm_buffer(ys);
    {
        uint16_t p[3] = {I_MOVHOST(2), I_HALT, I_NOP};
        clm_run(p, 2);
    }
    clm_buffer(xs);
    { // forward = x*c + y*s
        uint16_t p[6] = {I_CLRACC, I_MOVHOST(1), I_MAC(1, 4), I_MAC(2, 5), I_HALT, I_NOP};
        clm_run(p, 5);
    }
    for (int l = 0; l < 4; l++)
        out_fwd[l] = (int16_t)clm_read(l);

    { // side = x*s - y*c, no staging because x and y are still in R1 and R2
        uint16_t p[5] = {I_CLRACC, I_MAC(1, 6), I_MAC(2, 7), I_HALT, I_NOP};
        clm_run(p, 4);
    }
    for (int l = 0; l < 4; l++)
        out_side[l] = (int16_t)clm_read(l);
}

static int8_t normalize(int32_t v, int *shift)
{
    int32_t a = v < 0 ? -v : v;
    if (a <= 127)
    {
        *shift = 0;
        return (int8_t)v;
    }
    int bits = 0;
    while ((a >> bits) > 127)
        bits++;
    *shift = bits;
    int32_t m = a >> bits;
    return (int8_t)(v < 0 ? -m : m);
}

static int32_t rshift(int32_t v, int s)
{
    if (s <= 0)
        return v << (-s);
    return (v + (1 << (s - 1))) >> s;
}

// more marshalling, per-lane reciprocal via MOV_HOST, so four independent divides in one GO
static void gpu_div_vec4(const int32_t num[4], const int32_t den[4], int32_t out[4])
{
    int8_t n8[4], r8[4];
    int e[4], f[4], live[4];

    for (int i = 0; i < 4; i++)
    {
        int32_t ad = den[i] < 0 ? -den[i] : den[i];
        live[i] = (ad >= 9);
        if (!live[i])
        {
            n8[i] = 0;
            r8[i] = 0;
            e[i] = f[i] = 0;
            continue;
        }
        n8[i] = normalize(num[i], &e[i]);
        int8_t d8 = normalize(den[i], &f[i]);
        int32_t a = d8 < 0 ? -d8 : d8;
        int32_t r = (8192 + a / 2) / a;
        if (r > 127)
            r = 127;
        r8[i] = (int8_t)(d8 < 0 ? -r : r);
    }

    clm_buffer(n8);
    {
        uint16_t p[3] = {I_MOVHOST(1), I_HALT, I_NOP};
        clm_run(p, 2);
    }
    clm_buffer(r8);
    {
        uint16_t p[5] = {I_MOVHOST(2), I_CLRACC, I_MAC(1, 2), I_HALT, I_NOP};
        clm_run(p, 4);
    }
    for (int i = 0; i < 4; i++)
        out[i] = live[i] ? rshift((int16_t)clm_read(i), 9 + f[i] - e[i]) : (clm_read(i), 0);
}

// scalar fallback. gpu_div_vec4 handles the batched case
static int32_t gpu_div(int32_t num, int32_t den)
{
    int32_t ad = den < 0 ? -den : den;
    if (ad < 9)
        return 0;

    int e, f;
    int8_t n8 = normalize(num, &e);
    int8_t d8 = normalize(den, &f);

    int32_t a = d8 < 0 ? -d8 : d8;
    int32_t r = (8192 + a / 2) / a;
    if (r > 127)
        r = 127;
    int8_t recip = (int8_t)(d8 < 0 ? -r : r);

    int8_t lane[4] = {n8, n8, n8, n8};
    clm_buffer(lane);
    uint16_t p[6] = {I_CLRACC, I_MOVHOST(1), I_LDI(2, recip),
                     I_MAC(1, 2), I_HALT, I_NOP};
    clm_run(p, 5);
    int16_t prod = (int16_t)clm_read(0);

    // n8 = num/2^e, d8 = den/2^f, recip around 2^13/d8, so
    // prod = (num/den) * 2^(13+f-e). Q4 wants 2^4, hence >> (9+f-e)
    return rshift(prod, 9 + f - e);
}

// BSP/Visibility
static int point_on_side(int px_, int py_, const map_node_t *nd)
{
    long left = (long)nd->dy * (px_ - nd->x);
    long right = (long)(py_ - nd->y) * nd->dx;
    return right < left ? 0 : 1;
}

static uint8_t glyph_for(int32_t d)
{
    if (d <= 0)
        return 0;
    if (d < 1500)
        return 5;
    if (d < 2500)
        return 4;
    if (d < 4000)
        return 3;
    if (d < 6000)
        return 2;
    return 1;
}

// one shared denominator, so all four numerators share a normalise shift
static void gpu_div4(const int32_t num[4], int32_t den, int32_t out[4])
{
    int32_t ad = den < 0 ? -den : den;
    if (ad < 9)
    {
        out[0] = out[1] = out[2] = out[3] = 0;
        return;
    }

    int32_t big = 0;
    for (int i = 0; i < 4; i++)
    {
        int32_t a = num[i] < 0 ? -num[i] : num[i];
        if (a > big)
            big = a;
    }
    int e = 0;
    while ((big >> e) > 127)
        e++;

    int f;
    int8_t d8 = normalize(den, &f);
    int32_t a = d8 < 0 ? -d8 : d8;
    int32_t r = (8192 + a / 2) / a;
    if (r > 127)
        r = 127;
    int8_t recip = (int8_t)(d8 < 0 ? -r : r);

    int8_t lane[4];
    for (int i = 0; i < 4; i++)
        lane[i] = (int8_t)(num[i] >> e);
    clm_buffer(lane);
    uint16_t p[6] = {I_CLRACC, I_MOVHOST(1), I_LDI(2, recip),
                     I_MAC(1, 2), I_HALT, I_NOP};
    clm_run(p, 5);
    for (int i = 0; i < 4; i++)
        out[i] = rshift((int16_t)clm_read(i), 9 + f - e);
}

// the four wall heights of a seg, projected to screen rows at one depth
static void rows4(const seg_height_t *hh, int32_t depth, int r[4])
{
    int32_t num[4], q[4];
    num[0] = (hh->fceil - eye_h) * VSCALE;
    num[1] = (hh->ffloor - eye_h) * VSCALE;
    num[2] = (hh->bceil == SEG_NOBACK ? 0 : (hh->bceil - eye_h)) * VSCALE;
    num[3] = (hh->bfloor == SEG_NOBACK ? 0 : (hh->bfloor - eye_h)) * VSCALE;
    gpu_div4(num, depth, q);
    for (int i = 0; i < 4; i++)
    {
        int32_t v = ((int32_t)HALFR << 4) - q[i]; // Q4 row, fraction kept
        if (v < -16000)
            v = -16000;
        if (v > 16000)
            v = 16000;
        r[i] = (int)v;
    }
}

// screen row for a world height at this depth. one GPU divide.
static int row_of(int32_t world_h, int32_t depth)
{
    int32_t off = gpu_div((world_h - eye_h) * VSCALE, depth) >> 4;
    int32_t r = HALFR - off;
    if (r < -1000)
        r = -1000;
    if (r > 1000)
        r = 1000;
    return (int)r;
}

static void fill_span(int x, int top, int bot, uint8_t g)
{
    if (top < 0)
        top = 0;
    if (bot > ROWS - 1)
        bot = ROWS - 1;
    for (int y = top; y <= bot; y++)
        cell_now[y][x] = g;
}

static int prep_seg(const map_seg_t *sg, const map_subsector_t *ss,
                    const seg_height_t *hh, segprep_t *o)
{
    int32_t rx1 = ((int32_t)sg->x1 << E1M1_SCALE_SHIFT) + ss->ox - px;
    int32_t ry1 = ((int32_t)sg->y1 << E1M1_SCALE_SHIFT) + ss->oy - py;
    int32_t rx2 = ((int32_t)sg->x2 << E1M1_SCALE_SHIFT) + ss->ox - px;
    int32_t ry2 = ((int32_t)sg->y2 << E1M1_SCALE_SHIFT) + ss->oy - py;

    int sh = E1M1_SCALE_SHIFT;
    int32_t m = 0;
    if (labs(rx1) > m)
        m = labs(rx1);
    if (labs(ry1) > m)
        m = labs(ry1);
    if (labs(rx2) > m)
        m = labs(rx2);
    if (labs(ry2) > m)
        m = labs(ry2);
    while ((m >> sh) > 127)
        sh++;
    if (sh > E1M1_SCALE_SHIFT + 4)
        return 0;

    int8_t ax = (int8_t)(rx1 >> sh), ay = (int8_t)(ry1 >> sh);
    int8_t bx = (int8_t)(rx2 >> sh), by = (int8_t)(ry2 >> sh);

    int8_t c = cos_q7(pa), s = sin_q7[pa];
    int32_t q1 = (int32_t)ax * c + (int32_t)ay * s;
    int32_t q2 = (int32_t)bx * c + (int32_t)by * s;
    if (q1 <= 0 && q2 <= 0)
        return 0;

    o->sg = sg;
    o->hh = hh;
    o->sh = sh;
    o->x0 = ax;
    o->y0 = ay;
    o->x1 = bx;
    o->y1 = by;
    return 1;
}

// screen-x divides are batched by the caller across a seg pair
static int seg_clip(const segprep_t *pr, int16_t fa, int16_t fb,
                    int16_t sa, int16_t sb, int32_t f[2], int32_t d[2])
{
    int sh = pr->sh;
    f[0] = (int32_t)fa << (sh - E1M1_SCALE_SHIFT);
    f[1] = (int32_t)fb << (sh - E1M1_SCALE_SHIFT);
    d[0] = (int32_t)sa << (sh - E1M1_SCALE_SHIFT);
    d[1] = (int32_t)sb << (sh - E1M1_SCALE_SHIFT);

    if (f[0] < NEAR && f[1] < NEAR)
        return 0;
    if (f[0] < NEAR || f[1] < NEAR)
    {
        int n = (f[0] < NEAR) ? 0 : 1;
        int o = 1 - n;
        int32_t t = gpu_div((NEAR - f[n]) * 16, f[o] - f[n]);
        if (t < 0)
            t = 0;
        if (t > 16)
            t = 16;
        d[n] = d[n] + (((d[o] - d[n]) * t) >> 4);
        f[n] = NEAR;
    }
    return 1;
}

static void finish_seg(const segprep_t *pr, const int32_t f[2],
                       const int32_t d[2], const int sx_in[2])
{
    const map_seg_t *sg = pr->sg;
    const seg_height_t *hh = pr->hh;

    int sx[2] = {sx_in[0], sx_in[1]};

    int a = sx[0], b = sx[1];
    int32_t da = f[0], db = f[1];
    if (a > b)
    {
        int t2 = a;
        a = b;
        b = t2;
        da = f[1];
        db = f[0];
    }
    if (b < 0 || a >= COLS)
        return;
    int a_true = a, b_true = b;
    if (a < 0)
        a = 0;
    if (b >= COLS)
        b = COLS - 1;

    int any_open = 0;
    for (int x = a; x <= b; x++)
        if (!col_closed[x])
        {
            any_open = 1;
            break;
        }
    if (!any_open)
        return;

    int solid = (hh->bceil == SEG_NOBACK);
    int32_t dxw = (int32_t)sg->x2 - sg->x1;
    int32_t dyw = (int32_t)sg->y2 - sg->y1;
    (void)dxw;
    uint8_t lit = (labs(dyw) > labs(dxw)) ? 1 : 0;

    int span = b_true - a_true;
    int denom = span ? span : 1;

    int ra[4], rb[4];
    rows4(hh, da, ra);
    rows4(hh, db, rb);

    if (solid)
    {
        int t0 = ra[0], t1 = rb[0];
        int u0 = ra[1], u1 = rb[1];
        for (int x = a; x <= b; x++)
        {
            if (col_closed[x])
                continue;
            int k = x - a_true;
            int topq = t0 + ((t1 - t0) * k) / denom; // Q4 
            int botq = u0 + ((u1 - u0) * k) / denom; // Q4
            int top = topq >> 4, bot = (botq >> 4) - 1;
            int tf = (topq >> 2) & 3; // 0-3 coverage
            int bf = (botq >> 2) & 3;
            if (top < ceil_clip[x])
            {
                top = ceil_clip[x];
                tf = 0;
            }
            if (bot > floor_clip[x])
            {
                bot = floor_clip[x];
                bf = 3;
            }
            fill_span(x, top + 1, bot - 1, lit ? G_LIT : G_DIM);
            if (top >= 0 && top < ROWS)
                cell_now[top][x] = tf ? (uint8_t)((lit ? G_TOP : G_TOP + 3) + tf - 1)
                                      : (lit ? G_LIT : G_DIM);
            if (bot >= 0 && bot < ROWS && bot != top)
                cell_now[bot][x] = bf ? (uint8_t)((lit ? G_BOT : G_BOT + 3) + bf - 1)
                                      : (lit ? G_LIT : G_DIM);
            if (top > ceil_clip[x])
                ceil_clip[x] = (int16_t)top;
            if (bot < floor_clip[x])
                floor_clip[x] = (int16_t)bot;
            col_closed[x] = 1;
        }
        return;
    }

    int c0 = ra[0] >> 4, c1 = rb[0] >> 4;
    int e0 = ra[2] >> 4, e1 = rb[2] >> 4;
    int g0 = ra[3] >> 4, g1 = rb[3] >> 4;
    int h0 = (ra[1] >> 4) - 1, h1 = (rb[1] >> 4) - 1;

    for (int x = a; x <= b; x++)
    {
        if (col_closed[x])
            continue;
        int k = x - a_true;
        int rc = c0 + ((c1 - c0) * k) / denom;
        int re = e0 + ((e1 - e0) * k) / denom;
        int rg = g0 + ((g1 - g0) * k) / denom;
        int rh = h0 + ((h1 - h0) * k) / denom;

        if (hh->fceil > hh->bceil)
        {
            int top = rc, bot = re - 1;
            if (top < ceil_clip[x])
                top = ceil_clip[x];
            if (bot > floor_clip[x])
                bot = floor_clip[x];
            fill_span(x, top, bot, lit ? 5 : 7);
            if (bot + 1 > ceil_clip[x])
                ceil_clip[x] = (int16_t)(bot + 1);
        }
        else if (re > ceil_clip[x])
        {
            ceil_clip[x] = (int16_t)re;
        }

        if (hh->bfloor > hh->ffloor)
        {
            int top = rg, bot = rh;
            if (top < ceil_clip[x])
                top = ceil_clip[x];
            if (bot > floor_clip[x])
                bot = floor_clip[x];
            fill_span(x, top, bot, lit ? 5 : 7);
            if (top - 1 < floor_clip[x])
                floor_clip[x] = (int16_t)(top - 1);
        }
        else if (rg - 1 < floor_clip[x])
        {
            floor_clip[x] = (int16_t)(rg - 1);
        }

        if (ceil_clip[x] > floor_clip[x])
            col_closed[x] = 1;
    }
}

// subsectors are convex, so segs in one never occlude each other
static void flush_pair(segprep_t *q, int n)
{
    if (n == 0)
        return;
    int8_t xs[4] = {0, 0, 0, 0}, ys[4] = {0, 0, 0, 0};
    for (int i = 0; i < n; i++)
    {
        xs[2 * i] = q[i].x0;
        ys[2 * i] = q[i].y0;
        xs[2 * i + 1] = q[i].x1;
        ys[2 * i + 1] = q[i].y1;
    }
    int16_t fwd[4], side[4];
    gpu_rot4(xs, ys, cos_q7(pa), sin_q7[pa], fwd, side);

    int32_t f[2][2], d[2][2], num[4], den[4], q4[4];
    int live[2];
    for (int i = 0; i < 2; i++)
    {
        live[i] = (i < n) && seg_clip(&q[i], fwd[2 * i], fwd[2 * i + 1], side[2 * i], side[2 * i + 1], f[i], d[i]);
        for (int j = 0; j < 2; j++)
        {
            num[2 * i + j] = live[i] ? d[i][j] * FOCAL : 0;
            den[2 * i + j] = live[i] ? f[i][j] : 0;
        }
    }
    gpu_div_vec4(num, den, q4);

    for (int i = 0; i < n; i++)
    {
        if (!live[i])
            continue;
        int sx[2] = {HALFW + (int)(q4[2 * i] >> 4), HALFW + (int)(q4[2 * i + 1] >> 4)};
        finish_seg(&q[i], f[i], d[i], sx);
    }
}

static void draw_subsector(int idx)
{
    const map_subsector_t *ss = &e1m1_subsectors[idx];
    segprep_t q[2];
    int n = 0;
    for (int i = 0; i < ss->num_segs; i++)
    {
        if (!prep_seg(&e1m1_segs[ss->first_seg + i], ss,
                      &e1m1_seg_h[ss->first_seg + i], &q[n]))
            continue;
        if (++n == 2)
        {
            flush_pair(q, 2);
            n = 0;
        }
    }
    flush_pair(q, n);
}

static int screen_full(void)
{
    for (int x = 0; x < COLS; x++)
        if (!col_closed[x])
            return 0;
    return 1;
}

static void render_node(unsigned c)
{
    if (screen_full())
        return;
    if (c & NF_SUBSECTOR)
    {
        draw_subsector(c & 0x7FFF);
        return;
    }
    const map_node_t *nd = &e1m1_nodes[c];
    int side = point_on_side(px, py, nd);
    render_node(side ? nd->left : nd->right);
    render_node(side ? nd->right : nd->left);
}

// frame
static int16_t floor_under_player(void)
{
    unsigned c = E1M1_BSP_ROOT;
    while (!(c & NF_SUBSECTOR))
    {
        const map_node_t *nd = &e1m1_nodes[c];
        c = point_on_side(px, py, nd) ? nd->left : nd->right;
    }
    const map_subsector_t *ss = &e1m1_subsectors[c & 0x7FFF];
    return e1m1_seg_h[ss->first_seg].ffloor;
}

static void build_frame(void)
{
    eye_h = floor_under_player() + EYE_OFF;
    rot_regs_init();

    for (int x = 0; x < COLS; x++)
    {
        ceil_clip[x] = 0;
        floor_clip[x] = ROWS - 1;
        col_closed[x] = 0;
    }
    for (int y = 0; y < ROWS; y++)
        for (int x = 0; x < COLS; x++)
            cell_now[y][x] = 0;

    render_node(E1M1_BSP_ROOT);

    // a column with no wall still splits at the horizon, or it renders as void
    for (int x = 0; x < COLS; x++)
    {
        int touched = 0;
        for (int y = 0; y < ROWS; y++)
            if (cell_now[y][x] != 0)
            {
                touched = 1;
                break;
            }
        int cc = touched ? ceil_clip[x] : HALFR;
        int fc = touched ? floor_clip[x] : HALFR - 1;
        if (cc > ROWS)
            cc = ROWS;
        if (fc < -1)
            fc = -1;
        for (int y = 0; y < cc; y++)
            if (cell_now[y][x] == 0)
                cell_now[y][x] = 1;
        for (int y = (fc + 1 > 0 ? fc + 1 : 0); y < ROWS; y++)
            if (cell_now[y][x] == 0)
                cell_now[y][x] = 2;
    }
}

static uint16_t bg_color(uint8_t g)
{
    if (g >= G_TOP && g < G_BOT)
        return 0x0864; // ceiling above a wall top
    if (g >= G_BOT && g < 20)
        return 0x31C8; // floor below a wall bottom
    return 0x0000;
}

static uint16_t fg_color(uint8_t g)
{
    switch (g)
    {
    case 1:
        return 0x0864; // ceiling very dark
    case 2:
        return 0x31C8; // floor clearly lighter
    case 3:
        return 0x3CFD; // wall lit face
    case 4:
        return 0x19CD; // wall dim face
    case 5:
        return 0x663F; // step/band lit
    case 6:
        return 0xD75F; // corner edge
    case 7:
        return 0x2CB9; // step/band dim
    }
    if (g >= G_TOP && g < G_TOP + 3)
        return 0x3CFD; // lit, partial top
    if (g >= G_TOP + 3 && g < G_BOT)
        return 0x19CD; // dim, partial top
    if (g >= G_BOT && g < G_BOT + 3)
        return 0x3CFD; // lit, partial bottom
    if (g >= G_BOT + 3 && g < 20)
        return 0x19CD; // dim, partial bottom
    return 0x0000;
}

// HUD is drawn once and never repainted again, reserved() keeps renderer off it
#define BAR_CELLS_H (gfx_bar_H / CELL)
#define GUN_CELLS_W (gfx_gun_W / CELL)
#define GUN_CELLS_H (gfx_gun_H / CELL)
#define BAR_ROW0 (ROWS - BAR_CELLS_H)
#define GUN_COL0 ((COLS - GUN_CELLS_W) / 2)
#define GUN_ROW0 (BAR_ROW0 - GUN_CELLS_H)

// 0 = no gun here, 1 = gun with holes in it, 2 = solid gun
static inline int gun_cell(int x, int y)
{
    if (y < GUN_ROW0 || y >= BAR_ROW0)
        return 0;
    if (x < GUN_COL0 || x >= GUN_COL0 + GUN_CELLS_W)
        return 0;
    return gfx_gun_cell[y - GUN_ROW0][x - GUN_COL0];
}

// only fully-opaque gun cells. partial ones repaint so they can composite
static inline int reserved(int x, int y)
{
    if (y >= BAR_ROW0)
        return 1;
    return gun_cell(x, y) == 2;
}

// push an RGB565 block straight out of the accumulator
static void draw_gfx(int x0, int y0, int w, int h, const uint16_t *src)
{
    lcd_window(x0, y0, x0 + w - 1, y0 + h - 1);
    PIN1_HI(PIN_LCD_DC);
    PIN1_LO(PIN_LCD_CS);
    for (long i = 0; i < (long)w * h; i++)
    {
        if ((int32_t)src[i] != clm_cached)
        {
            PIN1_HI(PIN_LCD_CS);
            clm_load_cached(src[i]);
            PIN1_LO(PIN_LCD_CS);
        }
        push16();
    }
    PIN1_HI(PIN_LCD_CS);
}

// solid cells only because the rest composites live
static void draw_hud_once(void)
{
    for (int cy = 0; cy < GUN_CH; cy++)
        for (int cx = 0; cx < GUN_CW; cx++)
        {
            if (gfx_gun_cell[cy][cx] != 2)
                continue;
            int run = 1;
            while (cx + run < GUN_CW && gfx_gun_cell[cy][cx + run] == 2)
                run++;
            lcd_window(OX + (GUN_COL0 + cx) * CELL, OY + (GUN_ROW0 + cy) * CELL,
                       OX + (GUN_COL0 + cx + run) * CELL - 1,
                       OY + (GUN_ROW0 + cy + 1) * CELL - 1);
            PIN1_HI(PIN_LCD_DC);
            PIN1_LO(PIN_LCD_CS);
            for (int r = 0; r < CELL; r++)
                for (int c = 0; c < run * CELL; c++)
                {
                    uint16_t v = gfx_gun[(cy * CELL + r) * gfx_gun_W + cx * CELL + c];
                    if ((int32_t)v != clm_cached)
                    {
                        PIN1_HI(PIN_LCD_CS);
                        clm_load_cached(v);
                        PIN1_LO(PIN_LCD_CS);
                    }
                    push16();
                }
            PIN1_HI(PIN_LCD_CS);
            cx += run - 1;
        }
    draw_gfx(OX, OY + BAR_ROW0 * CELL, gfx_bar_W, gfx_bar_H, gfx_bar);
}

// Display
static void blit_run(int x, int y0, int y1)
{
    lcd_rowspan(OY + y0 * CELL, OY + (y1 + 1) * CELL - 1);
    PIN1_HI(PIN_LCD_DC);
    PIN1_LO(PIN_LCD_CS);

    for (int y = y0; y <= y1; y++)
    {
        uint8_t g = cell_now[y][x];
        cell_was[y][x] = g;
        uint16_t fg = fg_color(g);
        int mixed = (gun_cell(x, y) == 1);
        const uint16_t *gsrc = mixed
                                   ? &gfx_gun[((y - GUN_ROW0) * CELL) * gfx_gun_W + (x - GUN_COL0) * CELL]
                                   : 0;
        for (int row = 0; row < CELL; row++)
        {
            uint8_t bits = font8[g][row * 4 / CELL];
            for (int col = 0; col < CELL; col++)
            {
                int on = (bits & (0x08 >> (col * 4 / CELL))) ? 1 : 0;
                uint16_t want = on ? fg : bg_color(g);
                if (mixed)
                {
                    uint16_t gv = gsrc[row * gfx_gun_W + col];
                    if (gv != GFX_KEY)
                        want = gv;
                }
                if ((int32_t)want != clm_cached)
                {
                    PIN1_HI(PIN_LCD_CS);
                    clm_load_cached(want);
                    PIN1_LO(PIN_LCD_CS);
                }
                push16();
            }
        }
    }
    PIN1_HI(PIN_LCD_CS);
}

static void blit_frame(void)
{
    for (int x = 0; x < COLS; x++)
    {
        int col_set = 0;
        int y = 0;
        while (y < ROWS)
        {
            if (reserved(x, y))
            {
                y++;
                continue;
            }
            if (cell_now[y][x] == cell_was[y][x])
            {
                y++;
                continue;
            }
            int start = y;
            while (y < ROWS && !reserved(x, y) &&
                   cell_now[y][x] != cell_was[y][x])
                y++;
            if (!col_set)
            {
                lcd_colspan(OX + x * CELL, OX + x * CELL + CELL - 1);
                col_set = 1;
            }
            blit_run(x, start, y - 1);
        }
    }
}

// sampled once at boot since every stick's potentiometer rests at a
// slightly different centre; a fixed JOY_MID assumption is what was
// causing the drift
static int jx_center = JOY_MID;
static int jy_center = JOY_MID;

static void calibrate_joystick(void)
{
    // readings hold at a flatlined wrong value for close to a second
    // right after boot before jumping to the true rest value, so the
    // window a stability check requires has to comfortably outlast that
    // -- 300ms wasn't enough (the flatlined artifact itself looked
    // "stable"), so require 1.2s of no real movement instead
    int prev_x = analogRead(PIN_JOY_X);
    int prev_y = analogRead(PIN_JOY_Y);
    unsigned long stable_since = millis();
    unsigned long start = millis();
    while (millis() - start < 6000)
    {
        delay(20);
        int x = analogRead(PIN_JOY_X);
        int y = analogRead(PIN_JOY_Y);
        if (abs(x - prev_x) > 6 || abs(y - prev_y) > 6)
        {
            stable_since = millis();
            prev_x = x;
            prev_y = y;
        }
        if (millis() - stable_since > 1200)
            break;
    }

    long sx = 0, sy = 0;
    const int N = 32;
    for (int i = 0; i < N; i++)
    {
        sx += analogRead(PIN_JOY_X);
        sy += analogRead(PIN_JOY_Y);
        delay(2);
    }
    jx_center = (int)(sx / N);
    jy_center = (int)(sy / N);
    Serial.printf("joystick calibrated after %lums: x_center=%d y_center=%d\n",
                  millis() - start, jx_center, jy_center);
}

static void handle_input(void)
{
    // the ADC is noisy enough that a single sample swings tens of counts on a
    // stick that has not moved, so average a burst of them
    int rx = 0, ry = 0;
    for (int i = 0; i < 8; i++)
    {
        rx += analogRead(PIN_JOY_X);
        ry += analogRead(PIN_JOY_Y);
    }
    rx /= 8;
    ry /= 8;

    // then a low pass on top, so one bad burst cannot produce a frame of
    // movement by itself
    static int fx = 0, fy = 0, primed = 0;
    if (!primed)
    {
        fx = rx;
        fy = ry;
        primed = 1;
    }
    fx += (rx - fx) >> 2;
    fy += (ry - fy) >> 2;

    int dx = fx - jx_center;
    int dy = fy - jy_center;
    int ax = dx < 0 ? -dx : dx;
    int ay = dy < 0 ? -dy : dy;

    if (ax >= ay)
    {
        if (ay < JOY_DEAD * 2)
            dy = 0;
    }
    else
    {
        if (ax < JOY_DEAD * 2)
            dx = 0;
    }

    int8_t c = cos_q7(pa), s = sin_q7[pa];

    if (dy < -JOY_DEAD)
    {
        px += (c * 24) >> 7;
        py += (s * 24) >> 7;
    }
    else if (dy > JOY_DEAD)
    {
        px -= (c * 24) >> 7;
        py -= (s * 24) >> 7;
    }

    if (dx < -JOY_DEAD)
        pa = (uint8_t)(pa + 4);
    else if (dx > JOY_DEAD)
        pa = (uint8_t)(pa - 4);
}


// main
void setup(void)
{
    Serial.begin(115200);

    pinMode(PIN_JOY_SW, INPUT_PULLUP);
    analogSetAttenuation(ADC_11db); // full 0-3.3V swing, default only reads to 1.1V

    pinMode(PIN_CS, OUTPUT);
    digitalWrite(PIN_CS, HIGH);
    pinMode(PIN_CMD0, OUTPUT);
    pinMode(PIN_CMD1, OUTPUT);
    pinMode(PIN_CMD2, OUTPUT);
    pinMode(PIN_LCD_CS, OUTPUT);
    digitalWrite(PIN_LCD_CS, HIGH);
    pinMode(PIN_LCD_DC, OUTPUT);
    pinMode(PIN_LCD_RES, OUTPUT);
    digitalWrite(PIN_LCD_RES, HIGH);

    SPI.begin(PIN_SCLK, PIN_MISO, PIN_MOSI, -1);
    SPI.beginTransaction(SPISettings(SPI_HZ, MSBFIRST, SPI_MODE0));

    delay(500);
    lcd_init();
    lcd_window(0, 0, 127, 159);
    lcd_fill(0x0000, 128L * 160L);

    for (int y = 0; y < ROWS; y++)
        for (int x = 0; x < COLS; x++)
            cell_was[y][x] = 0;

    draw_hud_once();

    calibrate_joystick(); // don't touch the stick while this runs
    Serial.println("CLEMENTINE IS RUNNING DOOM!!");
}

void loop(void)
{
    handle_input();
    build_frame();
    blit_frame();

    static unsigned long last = 0, frames = 0;
    frames++;
    if (millis() - last > 2000)
    {
        Serial.printf("%lu fps\n", frames / 2);
        frames = 0;
        last = millis();
    }
}