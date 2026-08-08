`default_nettype none

// plain spi slave. the host holds the command on ui_in[2:0] before
// asserting cs, then clocks 16 bits. cs delimits the transaction, so
// nothing here counts clocks or reconstructs host state.
//
//   000 exec   the data phase is one instruction, execute it
//   001 go     clear pc, mask and halted
//   010 status
//   1xx read lane accumulator, command[1:0] is the lane
//
module clm_spi_host (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [2:0]  command,        // ui_in[2:0], stable before cs falls
    input  wire        spi_sclk,
    input  wire        spi_mosi,
    input  wire        spi_cs_n,
    output wire        spi_miso,

    input  wire        sequencer_done,

    input  wire [15:0] lane0_accumulator,
    input  wire [15:0] lane1_accumulator,
    input  wire [15:0] lane2_accumulator,
    input  wire [15:0] lane3_accumulator,

    output reg         instruction_valid,
    output wire [15:0] instruction_data,
    output reg         go
);

    reg [2:0] sclk_sync;
    reg [2:0] cs_n_sync;
    reg [1:0] mosi_sync;

    always @(posedge clk) begin
        if (!rst_n) begin
            sclk_sync <= 3'b000;
            cs_n_sync <= 3'b111;
            mosi_sync <= 2'b00;
        end
        else begin
            sclk_sync <= {sclk_sync[1:0], spi_sclk};
            cs_n_sync <= {cs_n_sync[1:0], spi_cs_n};
            mosi_sync <= {mosi_sync[0],   spi_mosi};
        end
    end

    wire sclk_rising = sclk_sync[1] & (~sclk_sync[2]);
    wire cs_active   = ~cs_n_sync[1];
    wire cs_falling  = (~cs_n_sync[1]) &   cs_n_sync[2];
    wire cs_rising   =   cs_n_sync[1]  & (~cs_n_sync[2]);

    // one stable copy of the pin command for the whole transaction. the
    // host sets it long before cs falls, so this is a capture, not a
    // synchronizer.
    reg [2:0] command_latched;

    always @(posedge clk) begin
        if (cs_falling) command_latched <= command;
    end

    wire read_acc  = command_latched[2];
    wire is_status = (command_latched == 3'b010);
    wire is_exec   = (command_latched == 3'b000);
    wire is_go     = (command_latched == 3'b001);

    // one-hot, zero-idle, so abc folds these into aoi cells instead of
    // leaving a mux2 per bit
    wire lane0_sel = read_acc & (~command_latched[1]) & (~command_latched[0]);
    wire lane1_sel = read_acc & (~command_latched[1]) & ( command_latched[0]);
    wire lane2_sel = read_acc & ( command_latched[1]) & (~command_latched[0]);
    wire lane3_sel = read_acc & ( command_latched[1]) & ( command_latched[0]);

    wire [15:0] status_word;
    assign status_word[0]    = sequencer_done;
    assign status_word[1]    = ~sequencer_done;
    assign status_word[15:2] = 14'd0;

    wire [15:0] response_value;
    assign response_value = (lane0_accumulator & {16{lane0_sel}}) | (lane1_accumulator & {16{lane1_sel}}) | (lane2_accumulator & {16{lane2_sel}}) | (lane3_accumulator & {16{lane3_sel}}) | (status_word       & {16{is_status}});

    // one register both directions. no counter: cs frames the
    // transaction and the host owns the clock count.
    reg [15:0] data_register;

    assign spi_miso = data_register[15];
    assign instruction_data = data_register;

    always @(posedge clk) begin
        if (cs_falling) begin
            data_register <= response_value;
        end
        else if (cs_active & sclk_rising) begin
            data_register <= {data_register[14:0], mosi_sync[1]};
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            instruction_valid <= 1'b0;
            go                <= 1'b0;
        end
        else begin
            instruction_valid <= cs_rising & is_exec;
            go                <= cs_rising & is_go;
        end
    end

endmodule
`default_nettype wire