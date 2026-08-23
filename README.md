![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

https://github.com/user-attachments/assets/2a935765-3c63-42a5-b818-157d14847543

## What do you have, Clementine?

- 4 lanes, int8 datapath, 16-bit accumulator per lane
- Custom 16-bit fixed-width ISA, 16 opcodes
- Hardware branch divergence: depth-2 mask stack with automatic reconvergence
- 8-instruction stored-program buffer, reruns without reloading
- Per-lane host data staging over SPI (MOV_HOST)
- SPI interface (Mode 0), command on sideband pins

- **How to Use:**
  - [Assembler + Loader](bringup/host/README.md)
  - [DOOM Setup](bringup/demo/doom/README.md)
- **Design Docs:**
- **Pre-Silicon Validation/Bringup:**
  - [Bringup and Validation](bringup/README.md)


## ISA

16-bit fixed-width instructions, 16 opcodes.

### Fields

| field | bits | used by |
|---|---|---|
| opcode | [15:12] | all |
| rd | [11:9] | ADD, SUB, AND, OR, XOR, SHL/SHR, LDI, MOV, MVAC |
| rs | [8:6] | ADD, SUB, AND, OR, XOR, SHL/SHR, MAC, CMP, MOV, LDAC |
| rt | [5:3] | ADD, SUB, AND, OR, XOR, SHL/SHR, MAC, CMP |
| imm8 | [8:1] | LDI only, full 8-bit value |
| cond | [11:10] | CMP only. 00 LT, 01 GT, 10 LE, 11 GE |
| sel | [3] | MVAC and LDAC. 0 low byte, 1 high byte |
| subop | [4] | opcode 1110 only. 0 IFP, 1 ELSE |
| dir | [0] | SHL/SHR only. 0 left, 1 right logical |
| target | [3:0] | opcode 1110 only. Reconvergence address |

### Opcodes

| opcode | mnemonic | operation |
|---|---|---|
| 0000 | NOP | no operation |
| 0001 | ADD | rd = rs + rt |
| 0010 | SUB | rd = rs - rt |
| 0011 | AND | rd = rs & rt |
| 0100 | OR | rd = rs \| rt |
| 0101 | XOR | rd = rs ^ rt |
| 0110 | SHL / SHR | rd = rs shifted by rt[2:0], bit[0] picks direction |
| 0111 | MAC | acc += rs * rt |
| 1000 | CLRACC | acc = 0 |
| 1001 | LDI / MOV_HOST | bit[0]=0: rd = imm8, broadcast to all lanes. bit[0]=1: each lane loads its own staged byte |
| 1010 | MOV / LANEID | bit[0]=0: rd = rs. bit[0]=1: rd = this lane's index |
| 1011 | CMPLT / CMPGT / CMPLE / CMPGE | set per-lane predicate from rs vs rt, cond field picks the test |
| 1100 | MVAC | rd = accumulator half |
| 1101 | LDAC | accumulator half = rs |
| 1110 | IFP / ELSE | divergence control |
| 1111 | HALT | signal done |

Registers are R0-R7. R0 is hardwired to zero and cannot be written.
Operands are signed int8. The accumulator is 16-bit signed.

CMP is one opcode with four mnemonics. The cond field selects less-than,
greater-than, less-or-equal, or greater-or-equal.

SUBREV, CMPREV, SHLREV, SHRREV are internal reversed-highway routing forms of SUB, CMP, SHL, SHR. They fire when rs is odd and rt is even, share the parent opcode, and produce the same logical result.

### Bank-conflict stall

`bank_conflict = uses_both_sources & same_bank_parity`. Fires whenever a two-source instruction has rs and rt of the same parity. No exclusions. Resolved by operand-hold capture/replay, one extra cycle.

### SIMT divergence

Depth-2 mask stack. IFP narrows the mask to true lanes, ELSE reconstructs the parent and pushes RECONVERGE with the ENDIF address. ENDIF is an assembler-resolved address, not an opcode; reconvergence fires when logical_pc equals the top RECONVERGE token's target. MOV_HOST obeys the active mask (a masked-off lane is not written).

## Architecture Diagram

## Repo Structure

## GDS 2D Preview 
