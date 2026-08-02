`default_nettype none

module clm_regfile (
    input  wire       clk,

    input  wire       write_enable,
    input  wire [2:0] write_address,
    input  wire [7:0] write_data,

    input  wire       read_enable_a,
    input  wire [2:0] read_address_a,
    output wire [7:0] read_data_a,

    input  wire       read_enable_b,
    input  wire [2:0] read_address_b,
    output wire [7:0] read_data_b
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
    assign write_bank_odd  =  write_address[0];

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

    assign write_enable_r1 = write_enable & write_bank_odd  & write_row_0;
    assign write_enable_r3 = write_enable & write_bank_odd  & write_row_1;
    assign write_enable_r5 = write_enable & write_bank_odd  & write_row_2;
    assign write_enable_r7 = write_enable & write_bank_odd  & write_row_3;

    // stored rows one block per register
    always @(posedge clk) begin
        if (write_enable_r2) begin
            reg_r2 <= write_data;
        end
    end

    always @(posedge clk) begin
        if (write_enable_r4) begin
            reg_r4 <= write_data;
        end
    end

    always @(posedge clk) begin
        if (write_enable_r6) begin
            reg_r6 <= write_data;
        end
    end

    always @(posedge clk) begin
        if (write_enable_r1) begin
            reg_r1 <= write_data;
        end
    end

    always @(posedge clk) begin
        if (write_enable_r3) begin
            reg_r3 <= write_data;
        end
    end

    always @(posedge clk) begin
        if (write_enable_r5) begin
            reg_r5 <= write_data;
        end
    end

    always @(posedge clk) begin
        if (write_enable_r7) begin
            reg_r7 <= write_data;
        end
    end

    wire [7:0] mux_a_lower;
    wire [7:0] mux_a_upper;
    wire [7:0] mux_a_selected;

    assign mux_a_lower =
        read_address_a[1] ? reg_r2 : 8'h00;

    assign mux_a_upper =
        read_address_a[1] ? reg_r6 : reg_r4;

    assign mux_a_selected =
        read_address_a[2] ? mux_a_upper : mux_a_lower;

    assign read_data_a =
        mux_a_selected & {8{read_enable_a}};

    wire [7:0] mux_b_lower;
    wire [7:0] mux_b_upper;
    wire [7:0] mux_b_selected;

    assign mux_b_lower = read_address_b[1] ? reg_r3 : reg_r1;

    assign mux_b_upper = read_address_b[1] ? reg_r7 : reg_r5;

    assign mux_b_selected = read_address_b[2] ? mux_b_upper : mux_b_lower;

    assign read_data_b =mux_b_selected & {8{read_enable_b}};


endmodule