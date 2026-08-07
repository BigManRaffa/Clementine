`default_nettype none

module clm_regfile (
    input wire clk,

    input wire write_enable,
    input wire [2:0] write_address,
    input wire [7:0] write_data,

    input wire [1:0] read_row_even,
    output wire [7:0] read_data_even,

    input wire [1:0] read_row_odd,
    output wire [7:0] read_data_odd
);

    // no r0 because grounded
    reg [7:0] reg_r2;
    reg [7:0] reg_r4;
    reg [7:0] reg_r6;

    reg [7:0] reg_r1;
    reg [7:0] reg_r3;
    reg [7:0] reg_r5;
    reg [7:0] reg_r7;

    // decoding logic
    wire write_bank_even;
    wire write_bank_odd;

    assign write_bank_even = ~write_address[0];
    assign write_bank_odd = write_address[0];

    wire write_row_0;
    wire write_row_1;
    wire write_row_2;
    wire write_row_3;

    assign write_row_0 = (~write_address[2]) & (~write_address[1]);
    assign write_row_1 = (~write_address[2]) & ( write_address[1]);
    assign write_row_2 = ( write_address[2]) & (~write_address[1]);
    assign write_row_3 = ( write_address[2]) & ( write_address[1]);

    // one hot write enables, one line per stored register
    wire write_enable_r2;
    wire write_enable_r4;
    wire write_enable_r6;

    wire write_enable_r1;
    wire write_enable_r3;
    wire write_enable_r5;
    wire write_enable_r7;

    assign write_enable_r2 = write_enable & write_bank_even & write_row_1;
    assign write_enable_r4 = write_enable & write_bank_even & write_row_2;
    assign write_enable_r6 = write_enable & write_bank_even & write_row_3;

    assign write_enable_r1 = write_enable & write_bank_odd & write_row_0;
    assign write_enable_r3 = write_enable & write_bank_odd & write_row_1;
    assign write_enable_r5 = write_enable & write_bank_odd & write_row_2;
    assign write_enable_r7 = write_enable & write_bank_odd & write_row_3;

    // stored rows one block per register
    always @(posedge clk) begin
        if (write_enable_r1) reg_r1 <= write_data;
        if (write_enable_r2) reg_r2 <= write_data;
        if (write_enable_r3) reg_r3 <= write_data;
        if (write_enable_r4) reg_r4 <= write_data;
        if (write_enable_r5) reg_r5 <= write_data;
        if (write_enable_r6) reg_r6 <= write_data;
        if (write_enable_r7) reg_r7 <= write_data;
    end

    / even bank read tree
    wire [7:0] even_lower_pair;
    wire [7:0] even_upper_pair;

    assign even_lower_pair = read_row_even[0] ? reg_r2 : 8'h00;
    assign even_upper_pair = read_row_even[0] ? reg_r6 : reg_r4;
    assign read_data_even = read_row_even[1] ? even_upper_pair : even_lower_pair;

    // odd bank read tree
    wire [7:0] odd_lower_pair;
    wire [7:0] odd_upper_pair;

    assign odd_lower_pair = read_row_odd[0] ? reg_r3 : reg_r1;
    assign odd_upper_pair = read_row_odd[0] ? reg_r7 : reg_r5;
    assign read_data_odd = read_row_odd[1] ? odd_upper_pair : odd_lower_pair;

endmodule