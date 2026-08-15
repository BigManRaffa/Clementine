`default_nettype none

module fpga_top (
    input wire clk,
    input wire rst_n,

    input wire sclk,
    input wire cs_n,
    input wire mosi,
    output wire miso,

    input wire [2:0] ui_in,

    output wire [3:0] led
);

    wire [7:0] core_ui_in;
    wire [7:0] core_uio_in;
    wire [7:0] core_uio_out;
    wire [7:0] core_uio_oe;
    wire [7:0] core_uo_out;

    assign core_ui_in = {5'b0, ui_in};

    assign core_uio_in[0] = cs_n;
    assign core_uio_in[1] = mosi;
    assign core_uio_in[2] = 1'b0;
    assign core_uio_in[3] = sclk;
    assign core_uio_in[7:4] = 4'b0;

    assign miso = core_uio_out[2];

    reg [3:0] por_shift;

    always @(posedge clk) begin
        por_shift <= {por_shift[2:0], 1'b1};
    end

    wire core_rst_n;
    assign core_rst_n = por_shift[3] & ~rst_n;

    tt_um_bigmanraffa_clm tt_dut (
        .clk (clk),
        .rst_n (core_rst_n),
        .ena (1'b1),
        .ui_in (core_ui_in),
        .uo_out (core_uo_out),
        .uio_in (core_uio_in),
        .uio_out (core_uio_out),
        .uio_oe (core_uio_oe)
    );

    reg [3:0] commit_count;
    always @(posedge clk) begin
        if (!core_rst_n) begin
            commit_count <= 4'd0;
        end
        else if (core_uo_out[2]) begin
            commit_count <= commit_count + 4'd1;
        end
    end

    assign led = ~commit_count;

endmodule
`default_nettype wire