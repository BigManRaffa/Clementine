#define _DEFAULT_SOURCE

// clmasm.c - Clementine assembler + loader

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <stdint.h>
#include <stdarg.h>
#include <errno.h>

#ifndef _WIN32
#include <fcntl.h>
#include <unistd.h>
#include <termios.h>
#endif

// machine limits
#define SLOTS 8
#define LANES 4
#define MASK_DEPTH 2

enum {
    OP_NOP = 0x0, OP_ADD = 0x1, OP_SUB = 0x2, OP_AND = 0x3,
    OP_OR = 0x4, OP_XOR = 0x5, OP_SHIFT = 0x6, OP_MAC = 0x7,
    OP_CLRACC = 0x8, OP_LDI = 0x9, OP_MOV = 0xA, OP_CMP = 0xB,
    OP_MVAC = 0xC, OP_LDAC = 0xD, OP_IFP = 0xE, OP_HALT = 0xF
};

enum { CC_LT = 0, CC_GT = 1, CC_LE = 2, CC_GE = 3 };

enum {
    CMD_EXEC = 0, CMD_GO = 1, CMD_STATUS = 2, CMD_BUFFER = 3,
    CMD_ACC0 = 4, CMD_ACC1 = 5, CMD_ACC2 = 6, CMD_ACC3 = 7
};

// operand shapes, one per encoding format
enum {
    F_RRR, // rd, rs, rt
    F_SHIFT, // rd, rs, rt plus a direction bit
    F_RR, // rs, rt
    F_CMP, // rs, rt plus a condition
    F_LDI, // rd, imm
    F_MOV, // rd, rs
    F_RD, // rd only
    F_RS_HALF, // rs, LO or HI
    F_RD_HALF, // rd, LO or HI
    F_NONE, // no operands
    F_BLOCK // IFP or ELSE, target resolved by the assembler
};

typedef struct {
    const char *name;
    int opcode;
    int form;
    int extra; // shift direction, CMP condition, or the IFP/ELSE subop
} Mnemonic;

static const Mnemonic mnemonics[] = {
    { "ADD", OP_ADD, F_RRR, 0 },
    { "SUB", OP_SUB, F_RRR, 0 },
    { "AND", OP_AND, F_RRR, 0 },
    { "OR", OP_OR, F_RRR, 0 },
    { "XOR", OP_XOR, F_RRR, 0 },
    { "SHL", OP_SHIFT, F_SHIFT, 0 },
    { "SHR", OP_SHIFT, F_SHIFT, 1 },
    { "MAC", OP_MAC, F_RR, 0 },
    { "CMPLT", OP_CMP, F_CMP, CC_LT },
    { "CMPGT", OP_CMP, F_CMP, CC_GT },
    { "CMPLE", OP_CMP, F_CMP, CC_LE },
    { "CMPGE", OP_CMP, F_CMP, CC_GE },
    { "LDI", OP_LDI, F_LDI, 0 },
    { "MOV_HOST", OP_LDI, F_RD, 1 },
    { "MOV", OP_MOV, F_MOV, 0 },
    { "LANEID", OP_MOV, F_RD, 1 },
    { "MVAC", OP_MVAC, F_RD_HALF, 0 },
    { "LDAC", OP_LDAC, F_RS_HALF, 0 },
    { "CLRACC", OP_CLRACC, F_NONE, 0 },
    { "NOP", OP_NOP, F_NONE, 0 },
    { "HALT", OP_HALT, F_NONE, 0 },
    { "IFP", OP_IFP, F_BLOCK, 0 },
    { "ELSE", OP_IFP, F_BLOCK, 1 }
};

#define MNEMONIC_COUNT ((int)(sizeof mnemonics / sizeof mnemonics[0]))

static const Mnemonic *lookup(const char *name)
{
    for (int i = 0; i < MNEMONIC_COUNT; i++) {
        if (strcmp(mnemonics[i].name, name) == 0) {
            return &mnemonics[i];
        }
    }
    return NULL;
}

static int verbose = 0;
static int error_count = 0;
static int warn_count = 0;

static const char *plural(int n)
{
    if (n == 1) {
        return "";
    }
    return "s";
}

static void err_at(int line, const char *fmt, ...)
{
    va_list ap;
    fprintf(stderr, "error: line %d: ", line);
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    error_count++;
}

static void warn_at(int line, const char *fmt, ...)
{
    va_list ap;
    fprintf(stderr, "warning: line %d: ", line);
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    warn_count++;
}

// source model
#define MAX_OPERANDS 4
#define MAX_TOKEN 64
#define MAX_ITEMS 512

typedef struct {
    char mnemonic[MAX_TOKEN];
    char operand[MAX_OPERANDS][MAX_TOKEN];
    int operands;
    int line;
    int slot;
    int emits;
    int target;
} Item;

static Item items[MAX_ITEMS];
static int item_count = 0;
static uint8_t host_bytes[LANES];
static int host_given = 0;
static uint16_t words[SLOTS];
static int word_count = 0;

static char *trim(char *s)
{
    while (*s != '\0' && isspace((unsigned char)*s)) {
        s++;
    }
    if (*s == '\0') {
        return s;
    }
    char *end = s + strlen(s) - 1;
    while (end > s && isspace((unsigned char)*end)) {
        *end = '\0';
        end--;
    }
    return s;
}

static void upper(char *s)
{
    while (*s != '\0') {
        *s = (char)toupper((unsigned char)*s);
        s++;
    }
}

static int parse_line(char *raw, int line, Item *out)
{
    char *comment = strstr(raw, "//");
    if (comment != NULL) {
        *comment = '\0';
    }
    char *s = trim(raw);
    if (*s == '\0') {
        return 0;
    }
    memset(out, 0, sizeof *out);
    out->line = line;
    char *tok = strtok(s, " \t,");
    if (tok == NULL) {
        return 0;
    }
    snprintf(out->mnemonic, MAX_TOKEN, "%s", tok);
    upper(out->mnemonic);
    tok = strtok(NULL, " \t,");
    while (tok != NULL) {
        if (out->operands >= MAX_OPERANDS) {
            err_at(line, "too many operands");
            return 0;
        }
        snprintf(out->operand[out->operands], MAX_TOKEN, "%s", tok);
        upper(out->operand[out->operands]);
        out->operands++;
        tok = strtok(NULL, " \t,");
    }
    return 1;
}

static int parse_reg(const char *s, int line)
{
    if (s[0] == 'R' && isdigit((unsigned char)s[1]) && s[2] == '\0') {
        int n = s[1] - '0';
        if (n <= 7) {
            return n;
        }
    }
    err_at(line, "expected a register R0-R7, got '%s'", s);
    return 0;
}

static int parse_imm(const char *s, int line, long lo, long hi)
{
    const char *p = s;
    int negative = 0;
    long value;
    char *endp;

    if (*p == '#') {
        p++;
    }
    if (*p == '-') {
        negative = 1;
        p++;
    }
    if (p[0] == '0' && p[1] == 'X') {
        value = strtol(p + 2, &endp, 16);
    } else if (*p == '$') {
        value = strtol(p + 1, &endp, 16);
    } else {
        value = strtol(p, &endp, 10);
    }
    if (endp == p || *endp != '\0') {
        err_at(line, "malformed number '%s'", s);
        return 0;
    }
    if (negative) {
        value = -value;
    }
    if (value < lo || value > hi) {
        err_at(line, "value %ld out of range %ld..%ld", value, lo, hi);
        return 0;
    }
    return (int)value;
}

static int parse_half(const char *s, int line)
{
    if (strcmp(s, "LO") == 0 || strcmp(s, "L") == 0) {
        return 0;
    }
    if (strcmp(s, "HI") == 0 || strcmp(s, "H") == 0) {
        return 1;
    }
    err_at(line, "expected LO or HI, got '%s'", s);
    return 0;
}

static int operands_needed(int form)
{
    switch (form) {
    case F_RRR:
    case F_SHIFT:
        return 3;
    case F_RR:
    case F_CMP:
    case F_LDI:
    case F_MOV:
    case F_RS_HALF:
    case F_RD_HALF:
        return 2;
    case F_RD:
        return 1;
    default:
        return 0;
    }
}

static int uses_two_sources(int opcode)
{
    switch (opcode) {
    case OP_ADD:
    case OP_SUB:
    case OP_AND:
    case OP_OR:
    case OP_XOR:
    case OP_SHIFT:
    case OP_MAC:
    case OP_CMP:
        return 1;
    default:
        return 0;
    }
}

// IFP carries the address of its ELSE, ELSE carries the ENDIF address. A
// wrong target here is not cosmetic: a fully masked THEN block scans forward
// until the PC matches, so a bad target never terminates.
static void resolve_blocks(void)
{
    int stack[MAX_ITEMS];
    int sp = 0;
    int depth = 0;
    int deepest = 0;

    for (int i = 0; i < item_count; i++) {
        const char *m = items[i].mnemonic;

        if (strcmp(m, "IFP") == 0) {
            stack[sp] = i;
            sp++;
            depth++;
            if (depth > deepest) {
                deepest = depth;
            }
        } else if (strcmp(m, "ELSE") == 0) {
            if (sp == 0) {
                err_at(items[i].line, "ELSE without a matching IFP");
                continue;
            }
            items[stack[sp - 1]].target = items[i].slot;
            stack[sp - 1] = i;
        } else if (strcmp(m, "ENDIF") == 0) {
            if (sp == 0) {
                err_at(items[i].line, "ENDIF without a matching IFP");
                continue;
            }
            sp--;
            depth--;
            int open = stack[sp];
            if (strcmp(items[open].mnemonic, "IFP") == 0) {
                err_at(items[open].line,
                       "IFP has no ELSE; the false lanes never wake");
            } else {
                items[open].target = items[i].slot;
            }
        }
    }
    if (sp > 0) {
        err_at(items[stack[sp - 1]].line, "unterminated IFP block");
    }
    if (deepest > MASK_DEPTH) {
        err_at(0, "divergence nested %d deep, the mask stack holds %d",
               deepest, MASK_DEPTH);
    }
}

static uint16_t encode(const Item *it, const Mnemonic *mn, int *rs_out, int *rt_out)
{
    uint16_t w = (uint16_t)(mn->opcode << 12);
    int rd, rs, rt, imm, half;

    *rs_out = -1;
    *rt_out = -1;

    switch (mn->form) {
    case F_RRR:
    case F_SHIFT:
        rd = parse_reg(it->operand[0], it->line);
        rs = parse_reg(it->operand[1], it->line);
        rt = parse_reg(it->operand[2], it->line);
        w |= (uint16_t)((rd << 9) | (rs << 6) | (rt << 3));
        if (mn->form == F_SHIFT) {
            w |= (uint16_t)mn->extra;
        }
        *rs_out = rs;
        *rt_out = rt;
        break;

    case F_RR:
        rs = parse_reg(it->operand[0], it->line);
        rt = parse_reg(it->operand[1], it->line);
        w |= (uint16_t)((rs << 6) | (rt << 3));
        *rs_out = rs;
        *rt_out = rt;
        break;

    case F_CMP:
        rs = parse_reg(it->operand[0], it->line);
        rt = parse_reg(it->operand[1], it->line);
        w |= (uint16_t)((mn->extra << 10) | (rs << 6) | (rt << 3));
        *rs_out = rs;
        *rt_out = rt;
        break;

    case F_LDI:
        // the immediate sits in bits 8:1, so bit 0 stays clear or this
        // decodes as MOV_HOST instead
        rd = parse_reg(it->operand[0], it->line);
        imm = parse_imm(it->operand[1], it->line, -128, 255);
        w |= (uint16_t)((rd << 9) | ((imm & 0xFF) << 1));
        break;

    case F_MOV:
        rd = parse_reg(it->operand[0], it->line);
        rs = parse_reg(it->operand[1], it->line);
        w |= (uint16_t)((rd << 9) | (rs << 6));
        break;

    case F_RD:
        rd = parse_reg(it->operand[0], it->line);
        w |= (uint16_t)((rd << 9) | mn->extra);
        break;

    case F_RD_HALF:
        rd = parse_reg(it->operand[0], it->line);
        half = parse_half(it->operand[1], it->line);
        w |= (uint16_t)((rd << 9) | (half << 3));
        break;

    case F_RS_HALF:
        rs = parse_reg(it->operand[0], it->line);
        half = parse_half(it->operand[1], it->line);
        w |= (uint16_t)((rs << 6) | (half << 3));
        break;

    case F_BLOCK:
        w |= (uint16_t)((mn->extra << 4) | (it->target & 0xF));
        break;

    case F_NONE:
    default:
        break;
    }
    return w;
}

static int assemble(const char *path)
{
    FILE *f = fopen(path, "r");
    if (f == NULL) {
        fprintf(stderr, "error: cannot open %s: %s\n", path, strerror(errno));
        return 0;
    }

    char raw[512];
    int line = 0;
    int slot = 0;

    while (fgets(raw, sizeof raw, f) != NULL) {
        line++;
        Item item;
        if (parse_line(raw, line, &item) == 0) {
            continue;
        }
        if (strcmp(item.mnemonic, ".HOST") == 0) {
            if (item.operands != LANES) {
                err_at(line, ".host needs exactly %d bytes", LANES);
                continue;
            }
            for (int i = 0; i < LANES; i++) {
                host_bytes[i] = (uint8_t)parse_imm(item.operand[i], line, -128, 255);
            }
            host_given = 1;
            continue;
        }
        if (item_count >= MAX_ITEMS) {
            err_at(line, "source too long");
            break;
        }
        // ENDIF is an address the assembler resolves, not an instruction
        item.emits = 1;
        if (strcmp(item.mnemonic, "ENDIF") == 0) {
            item.emits = 0;
        }
        item.slot = slot;
        if (item.emits == 1) {
            slot++;
        }
        items[item_count] = item;
        item_count++;
    }
    fclose(f);

    if (slot > SLOTS) {
        err_at(0, "kernel is %d instructions, the buffer holds %d", slot, SLOTS);
    }
    resolve_blocks();

    int seen_cmp = 0;
    int seen_halt = 0;
    word_count = 0;

    for (int i = 0; i < item_count; i++) {
        Item *it = &items[i];
        if (it->emits == 0) {
            continue;
        }
        const Mnemonic *mn = lookup(it->mnemonic);
        if (mn == NULL) {
            err_at(it->line, "unknown mnemonic '%s'", it->mnemonic);
            continue;
        }
        int needed = operands_needed(mn->form);
        if (it->operands != needed) {
            err_at(it->line, "%s takes %d operand%s, got %d",
                   mn->name, needed, plural(needed), it->operands);
            continue;
        }

        int rs, rt;
        uint16_t w = encode(it, mn, &rs, &rt);

        if (mn->opcode == OP_CMP) {
            seen_cmp = 1;
        }
        if (mn->opcode == OP_HALT) {
            seen_halt = 1;
        }
        if (strcmp(mn->name, "IFP") == 0 && seen_cmp == 0) {
            err_at(it->line, "IFP before any CMP, the predicate would be stale");
        }
        if (uses_two_sources(mn->opcode) == 1 && (rs & 1) == (rt & 1)) {
            warn_at(it->line, "R%d and R%d share a bank, one extra cycle", rs, rt);
        }
        if (word_count < SLOTS) {
            words[word_count] = w;
        }
        word_count++;
    }

    if (seen_halt == 0) {
        err_at(0, "no HALT, the chip would never raise DONE");
    }
    if (error_count > 0) {
        return 0;
    }
    return 1;
}

#ifndef _WIN32

static int serial_open(const char *dev)
{
    int fd = open(dev, O_RDWR | O_NOCTTY);
    if (fd < 0) {
        fprintf(stderr, "error: open %s: %s\n", dev, strerror(errno));
        return -1;
    }
    struct termios tio;
    if (tcgetattr(fd, &tio) < 0) {
        perror("tcgetattr");
        close(fd);
        return -1;
    }
    cfmakeraw(&tio);
    cfsetispeed(&tio, B115200);
    cfsetospeed(&tio, B115200);
    tio.c_cflag |= CLOCAL | CREAD;
    tio.c_cflag &= (unsigned)~CRTSCTS;
    tio.c_cc[VMIN] = 0;
    tio.c_cc[VTIME] = 20;
    if (tcsetattr(fd, TCSANOW, &tio) < 0) {
        perror("tcsetattr");
        close(fd);
        return -1;
    }
    tcflush(fd, TCIOFLUSH);
    return fd;
}

static int read_exact(int fd, uint8_t *buf, size_t n)
{
    size_t got = 0;
    while (got < n) {
        ssize_t r = read(fd, buf + got, n - got);
        if (r <= 0) {
            return 0;
        }
        got += (size_t)r;
    }
    return 1;
}

// one bridge transaction: command byte, payload, then two bytes of MISO back
static int frame(int fd, int cmd, const uint8_t *payload, int n, uint16_t *resp)
{
    uint8_t out[1 + LANES];
    out[0] = (uint8_t)cmd;
    memcpy(out + 1, payload, (size_t)n);

    if (write(fd, out, (size_t)n + 1) != (ssize_t)(n + 1)) {
        fprintf(stderr, "error: short write on serial port\n");
        return 0;
    }
    uint8_t in[2];
    if (read_exact(fd, in, 2) == 0) {
        fprintf(stderr, "error: no reply from bridge, command %d\n", cmd);
        return 0;
    }
    uint16_t value = (uint16_t)((in[0] << 8) | in[1]);
    if (resp != NULL) {
        *resp = value;
    }
    if (verbose == 1) {
        fprintf(stderr, "  cmd %d  %d bytes out  ->  %04X\n", cmd, n, value);
    }
    return 1;
}

static int send_word(int fd, int cmd, uint16_t w, uint16_t *resp)
{
    uint8_t payload[2];
    payload[0] = (uint8_t)(w >> 8);
    payload[1] = (uint8_t)(w & 0xFF);
    return frame(fd, cmd, payload, 2, resp);
}

static int run_on_chip(const char *dev)
{
    int fd = serial_open(dev);
    if (fd < 0) {
        return 0;
    }

    // lane 0's byte travels furthest down the chain, so it goes out first
    if (host_given == 1) {
        printf("staging %02X %02X %02X %02X\n",
               host_bytes[0], host_bytes[1], host_bytes[2], host_bytes[3]);
        if (frame(fd, CMD_BUFFER, host_bytes, LANES, NULL) == 0) {
            close(fd);
            return 0;
        }
    }
    for (int i = 0; i < word_count; i++) {
        if (send_word(fd, CMD_EXEC, words[i], NULL) == 0) {
            close(fd);
            return 0;
        }
    }
    if (send_word(fd, CMD_GO, 0, NULL) == 0) {
        close(fd);
        return 0;
    }

    int halted = 0;
    for (int i = 0; i < 100 && halted == 0; i++) {
        uint16_t status;
        if (send_word(fd, CMD_STATUS, 0, &status) == 0) {
            close(fd);
            return 0;
        }
        halted = status & 1;
    }
    if (halted == 0) {
        fprintf(stderr, "error: chip never reported DONE\n");
        close(fd);
        return 0;
    }

    printf("\nresults\n");
    for (int lane = 0; lane < LANES; lane++) {
        uint16_t acc;
        if (send_word(fd, CMD_ACC0 + lane, 0, &acc) == 0) {
            close(fd);
            return 0;
        }
        printf("  lane %d  %04X  %6u  %6d\n", lane, acc, acc, (int16_t)acc);
    }
    close(fd);
    return 1;
}

#else

static int run_on_chip(const char *dev)
{
    (void)dev;
    fprintf(stderr, "error: loading needs a POSIX serial port, "
                    "please use Linux or WSL\n");
    return 0;
}

#endif

static void usage(const char *argv0)
{
    fprintf(stderr,
        "usage: %s [-v] kernel.s [serial-port]\n"
        "\n"
        "  ADD SUB AND OR XOR   rd, rs, rt\n"
        "  SHL SHR              rd, rs, rt\n"
        "  MAC                  rs, rt\n"
        "  CMPLT CMPGT CMPLE CMPGE  rs, rt\n"
        "  LDI                  rd, imm        broadcast, -128 to 255\n"
        "  MOV_HOST             rd             each lane takes its staged byte\n"
        "  MOV                  rd, rs\n"
        "  LANEID               rd\n"
        "  MVAC                 rd, LO|HI\n"
        "  LDAC                 rs, LO|HI\n"
        "  CLRACC NOP HALT\n"
        "  IFP ELSE ENDIF                      targets resolved here\n"
        "  .host b0, b1, b2, b3                one byte per lane\n",
        argv0);
}

int main(int argc, char **argv)
{
    int argi = 1;
    if (argc > 1 && strcmp(argv[1], "-v") == 0) {
        verbose = 1;
        argi = 2;
    }
    if (argc - argi < 1) {
        usage(argv[0]);
        return 2;
    }

    const char *src = argv[argi];
    const char *port = NULL;
    if (argc - argi >= 2) {
        port = argv[argi + 1];
    }

    if (assemble(src) == 0) {
        fprintf(stderr, "\nassembly failed, %d error%s\n",
                error_count, plural(error_count));
        return 1;
    }

    printf("assembled %d instruction%s", word_count, plural(word_count));
    if (warn_count > 0) {
        printf(", %d warning%s", warn_count, plural(warn_count));
    }
    printf("\n\n");

    int k = 0;
    for (int i = 0; i < item_count; i++) {
        if (items[i].emits == 0) {
            continue;
        }
        printf("  %d  %04X   %s", items[i].slot, words[k], items[i].mnemonic);
        for (int j = 0; j < items[i].operands; j++) {
            printf(" %s", items[i].operand[j]);
        }
        if (strcmp(items[i].mnemonic, "IFP") == 0 ||
            strcmp(items[i].mnemonic, "ELSE") == 0) {
            printf("   -> slot %d", items[i].target);
        }
        printf("\n");
        k++;
    }
    printf("\n");

    if (port == NULL) {
        return 0;
    }
    if (run_on_chip(port) == 0) {
        return 1;
    }
    return 0;
}