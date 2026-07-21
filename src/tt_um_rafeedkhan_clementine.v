`default_nettype none

// wrapper to test mac multiply, should have 160 cell bloat
module tt_um_rafeedkhan_clementine (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);

  reg [63:0] a, b;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      a <= 64'd0;
      b <= 64'd0;
    end else begin
      a <= {a[55:0], ui_in};
      b <= {b[55:0], uio_in};
    end
  end

  wire [87:0] prod;
  wire [23:0] pexp;
  wire [3:0]  psign;

  clm_mac_multiply u_mul (
    .a     (a),
    .b     (b),
    .prod  (prod),
    .pexp  (pexp),
    .psign (psign)
  );

  wire [115:0] out_bus = {prod, pexp, psign};
  reg  [7:0]   fold;
  integer k;
  always @(*) begin
    fold = 8'd0;
    for (k = 0; k < 116; k = k + 1)
      fold[k[2:0]] = fold[k[2:0]] ^ out_bus[k];
  end

  reg [7:0] out_r;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) out_r <= 8'd0;
    else        out_r <= fold;
  end

  assign uo_out  = out_r;
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;

  wire _unused = &{ena, 1'b0};

endmodule

`default_nettype wire