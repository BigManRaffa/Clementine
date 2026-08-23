# Running kernels on Clementine

## 1. Build the assembler

```bash
cc -std=c99 -O2 -Wall -Wextra -o clmasm assembler.c
```

## 2. Flash the FPGA

Program the Tang Nano 20K with the Clementine bitstream in Gowin Programmer.
The bitstream is an SRAM load, so repeat this after any power cycle.

## 3. Flash the bridge

Open `esp32/UART-to-SPI.ino` in the Arduino IDE, select the ESP32-S3, and
upload.

## 4. Kernel Command Format

```bash
./clmasm kernels/[.s file] /dev/ttyUSB0
```

If you are using WSL, attach the port first:

```powershell
usbipd list # find your MCU's BUSID
usbipd attach --wsl --busid <your-esp32-busid>
```

## Writing kernels

Eight instructions maximum.

Use `.host b0, b1, b2, b3` to stage one byte per lane, lane 0 first, for
`MOV_HOST` to pick up. It is a directive, not an instruction, so it costs no
slot.

Use `ENDIF` to mark where a divergence block finishes, so the assembler knows
the address to put in the `ELSE` instruction. It is an address the assembler
resolves, not an opcode, so it costs no slot either. `IFP` and `ELSE` do take a slot however.

Every kernel needs a `HALT` (which also takes up a slot) or the chip never reports done. It can sit
anywhere, not just last.

Registers and the accumulator persist across runs, so a sequence of kernels
can carry state between them.

## Expected results from each test

| kernel | operation | lane 0 | lane 1 | lane 2 | lane 3 |
|---|---|---|---|---|---|
| mac | acc += host * 6 | 42 | 48 | 54 | 60 |
| add | host + 30 | 130 | 131 | 132 | 133 |
| sub | host - 5 | 95 | 85 | 75 | 65 |
| and | host & 0x3F | 0x3C | 0x1E | 0x2A | 0x17 |
| or | host \| 0x0C | 0x8C | 0x4C | 0x2C | 0x1C |
| xor | host ^ 0x3C | 0xCC | 0xDC | 0xFC | 0xBC |
| shl | host << 4 | 16 | 32 | 48 | 64 |
| shr | host >> 3 | 16 | 8 | 4 | 2 |
| mov | copy host to R3 | 0x5A | 0x5B | 0x5C | 0x5D |
| laneid | lanes know their index | 0 | 1 | 2 | 3 |
| movhost | arbitrary host data per lane | 0x11 | 0x22 | 0x33 | 0x44 |
| diverge | lanes take different paths | 0 | 66 | 66 | 66 |
| diverge_le | a different split point | 2 | 2 | 2 | 0 |
| diverge_nested | depth-2 mask stack, nested push/pop | 0 | 1 | 2 | 3 |
| diverge_movhost | mask gates MOV_HOST | 0 | 0x22 | 0x33 | 0x44 |
| bankconflict | same-parity sources still correct | 70 | 71 | 72 | 73 |
| acc_halves | both accumulator bytes reachable | 0xCDA0 | 0xCDA1 | 0xCDA2 | 0xCDA3 |
| dotproduct | a real MAC chain on per-lane data | 40 | 60 | 80 | 100 |
| signed | sign extension through the multiplier | 0xFFFF | 0xFFFE | 0xFF80 | 0x007F |
| overflow | accumulator wrap is defined | 0xBD03 | 0x94D4 | 0x5F40 | 0x2FA0 |
| halt_early | HALT need not be last | 0 | 1 | 2 | 3 |
| r0_grounded | R0 cannot be written | 0 | 0 | 0 | 0 |
| cmpgt | CMPGT splits lanes | 0 | 0 | 1 | 1 |
| cmpge | CMPGE splits lanes | 0 | 0 | 2 | 2 |
| cmprev | CMP with rs odd, rt even (reverse form) | 3 | 3 | 3 | 3 |
| mvac | accumulator moved to a register, then read | 21 | 28 | 35 | 42 |
| subrev | SUB with rs odd, rt even (SUBREV) | 75 | 65 | 55 | 45 |
| shlrev | SHL with rs odd, rt even (SHLREV) | 16 | 32 | 48 | 64 |
| persist_write / persist_read | registers survive across GO | 42 | 42 | 42 | 42 |
| ray_p1a | cross(r, P1) | 45 | 45 | 45 | 45 |
| ray_p1b | cross(r, P2), opposite sign means a valid hit | -147 | -147 | -147 | -147 |
| ray_p2a | n · P | 60 | 60 | 60 | 60 |
| ray_p2b | d_rel = D − n·P | 3 | 3 | 3 | 3 |
| ray_p3a | n · r | 24 | 24 | 24 | 24 |
| ray_p3b | d_rel × reciprocal | 129 | 129 | 129 | 129 |
| ray_p3c | t, the division complete | 16 | 16 | 16 | 16 |
| ray_p3a_norm | n · r, normalised operands | 2720 | 2720 | 2720 | 2720 |
| ray_p3b_norm | d8 × reciprocal, normalised operands | 8160 | 8160 | 8160 | 8160 |