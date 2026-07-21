<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

The design under test is the FP16 multiplier datapath (`clm_mac_multiply`).
It runs four lanes in parallel, one per packed FP16 element. Each lane feeds
its 11-bit significands into a structural 11x11 Dadda multiplier
(`clm_wallace11`, built from SKY130 standard cells), adds the two (double-
biased) exponents, and XORs the sign bits. The four products, exponents, and
signs are produced fully in parallel.

## How to test

Shift the operands in one byte per clock: drive successive bytes of the A
operand on `ui_in` and the B operand on `uio_in` — 8 clocks each fills the
64-bit `a`/`b` registers. The full 116-bit result (product, exponent, sign
for all four lanes) is XOR-folded down to 8 bits and read back on `uo_out`.

## External hardware

List external hardware used in your project (e.g. PMOD, LED display, etc), if any
