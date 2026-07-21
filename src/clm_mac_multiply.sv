// clm_mac_multiply: 4 fully parallel FP16 significand multipliers,
// parallel exponent adders + sign XORs. Multiplier trees are structural
// SKY130 cell instantiations (see clm_wallace11.sv, generated); only the
// glue below goes through ABC.
module clm_mac_multiply (
  input  logic [63:0] a,          // 4x FP16 packed, lane i at [i*16 +: 16]
  input  logic [63:0] b,
  output logic [87:0] prod_flat,  // lane i at [i*22 +: 22]
  output logic [23:0] pexp_flat,  // double biased (ea + eb), 0 if flushed
  output logic [3:0]  psign
);

  generate
  for (genvar i = 0; i < 4; i++) begin : lane
    logic [4:0] ea, eb;
    assign ea = a[i*16+10 +: 5];
    assign eb = b[i*16+10 +: 5];

    // flush to zero: exp == 0 means denormal or zero
    logic ftz;
    assign ftz = (ea == 5'd0) | (eb == 5'd0);

    // structural Dadda tree; FTZ masks one operand inside (0 * x == 0)
    clm_wallace11 u_mult (
      .ma  (a[i*16 +: 10]),
      .mb  (b[i*16 +: 10]),
      .ftz (ftz),
      .prod(prod_flat[i*22 +: 22])
    );

    // double biased: true exponent is pexp - 30, corrected once at
    // accumulate (+97 folded into the accumulator alignment subtractor).
    // Clamped to 0 on flush so this lane can never win emax.
    assign pexp_flat[i*6 +: 6] = ftz ? 6'd0 : ({1'b0, ea} + {1'b0, eb});

    assign psign[i] = a[i*16+15] ^ b[i*16+15];
  end
  endgenerate

endmodule