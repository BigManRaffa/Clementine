`default_nettype none

// spi slave and command interpreter, 19 clocks under one cs, mode 0, msb first
// first 3 rising edges are the command, next 16 are the data phase
module clm_spi_host (
    input wire clk,
    input wire rst_n,

    input wire spi_sclk,
    input wire spi_mosi,
    input wire spi_cs_n,
    output wire spi_miso,

    input wire sequencer_done,

    input wire [15:0] lane0_accumulator,
    input wire [15:0] lane1_accumulator,
    input wire [15:0] lane2_accumulator,
    input wire [15:0] lane3_accumulator,

    output wire instruction_shift_enable,
    output wire [15:0] instruction_shift_data,
    output reg go
);

    localparam CMD_LOAD = 3'b000;
    localparam CMD_GO = 3'b001;
    localparam CMD_STATUS = 3'b010;
    localparam CMD_NOP = 3'b011;

    localparam COMMAND_BITS = 5'd3;
    localparam TRANSACTION_END = 5'd18;

    // edges read from the settled stages
    reg [1:0] sclk_sync;
    reg [1:0] cs_n_sync;
    reg [1:0] mosi_sync;

    always @(posedge clk) begin
        if (!rst_n) begin
            sclk_sync <= 2'b00;
            cs_n_sync <= 2'b11;
            mosi_sync <= 2'b00;
        end
        else begin
            sclk_sync <= {sclk_sync[0], spi_sclk};
            cs_n_sync <= {cs_n_sync[0], spi_cs_n};
            mosi_sync <= {mosi_sync[0], spi_mosi};
        end
    end

    wire sclk_rising;
    wire sclk_falling;
    wire cs_active;
    wire cs_falling;

    assign sclk_rising = sclk_sync[0] & (~sclk_sync[1]);
    assign sclk_falling = (~sclk_sync[0]) & sclk_sync[1];
    assign cs_active = ~cs_n_sync[0];
    assign cs_falling = (~cs_n_sync[0]) & cs_n_sync[1];

    // counter walks 0-18 across one cs. 0-2 command, 3-18 data
    reg [4:0] transaction_counter;
    reg [2:0] command_register;

    wire in_command_phase;
    assign in_command_phase = (transaction_counter < COMMAND_BITS);

    // falling edge after the 3rd command bit, response settles before it's sampled
    wire at_phase_boundary;
    assign at_phase_boundary = (transaction_counter == COMMAND_BITS) & sclk_falling & cs_active;

    wire at_transaction_end;
    assign at_transaction_end = (transaction_counter == TRANSACTION_END) & sclk_rising & cs_active;

    always @(posedge clk) begin
        if (!rst_n) begin
            transaction_counter <= 5'd0;
            command_register <= CMD_NOP;
        end
        else begin
            if (cs_falling) begin
                transaction_counter <= 5'd0;
            end
            else if (cs_active & sclk_rising) begin
                transaction_counter <= transaction_counter + 5'd1;

                if (in_command_phase) begin
                    command_register <= {command_register[1:0], mosi_sync[1]};
                end
            end
        end
    end

    wire command_is_accumulator_read;
    wire command_is_load;
    wire command_is_go;

    assign command_is_accumulator_read = command_register[2];
    assign command_is_load = (command_register == CMD_LOAD);
    assign command_is_go = (command_register == CMD_GO);

    // visibility only, never gates, saturates at 16 so a stuck loader reads as 16
    reg [4:0] load_counter;

    wire load_counter_at_full;
    assign load_counter_at_full = load_counter[4];

    // [0] halted, [1] active, [6:2] word count since last go
    wire [15:0] status_word;

    assign status_word[0] = sequencer_done;
    assign status_word[1] = ~sequencer_done;
    assign status_word[6:2] = load_counter;
    assign status_word[15:7] = 9'd0;

    wire [15:0] lane_pair_low;
    wire [15:0] lane_pair_high;
    wire [15:0] lane_selected;
    wire [15:0] response_value;

    assign lane_pair_low = command_register[0] ? lane1_accumulator : lane0_accumulator;
    assign lane_pair_high = command_register[0] ? lane3_accumulator : lane2_accumulator;
    assign lane_selected = command_register[1] ? lane_pair_high : lane_pair_low;

    assign response_value[6:0] = command_is_accumulator_read ? lane_selected[6:0] : status_word[6:0];
    assign response_value[15:7] = lane_selected[15:7] & {9{command_is_accumulator_read}};

    // one register both directions, parallel loaded on a read, shifted through on a load
    reg [15:0] data_register;

    assign spi_miso = data_register[15];

    always @(posedge clk) begin
        if (at_phase_boundary) begin
            data_register <= response_value;
        end
        else if (cs_active & sclk_rising & (~in_command_phase)) begin
            data_register <= {data_register[14:0], mosi_sync[1]};
        end
    end

    assign instruction_shift_data = data_register;

    reg instruction_shift_pulse;

    assign instruction_shift_enable = instruction_shift_pulse;

    always @(posedge clk) begin
        if (!rst_n) begin
            instruction_shift_pulse <= 1'b0;
            go <= 1'b0;
        end
        else begin
            instruction_shift_pulse <= at_transaction_end & command_is_load;
            go <= at_phase_boundary & command_is_go;
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            load_counter <= 5'd0;
        end
        else if (go) begin
            load_counter <= 5'd0;
        end
        else if (instruction_shift_pulse & (~load_counter_at_full)) begin
            load_counter <= load_counter + 5'd1;
        end
    end

endmodule
`default_nettype wire