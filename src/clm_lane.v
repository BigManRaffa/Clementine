`default_nettype none

module clm_lane (
    input wire clk,

    input wire [1:0] read_row_even,
    input wire [1:0] read_row_odd,
    input wire conflict_bank_select,
    input wire [2:0] rd_address,
    input wire [7:0] immediate_value,

    input wire operand_hold_load,
    input wire operand_hold_use,
    input wire instruction_commit,
    input wire register_write_enable,
    input wire predicate_write_enable,

    input wire select_immediate,
    input wire force_one_box1,
    input wire force_one_box2,
    input wire swap_operands,

    input wire subtract_prepare,
    input wire prepare_zero,
    input wire select_accumulator,

    input wire shift_direction,
    input wire [1:0] bitwise_select,
    input wire [1:0] condition_select,

    input wire accumulator_clear,
    input wire accumulator_load,
    input wire accumulator_mac_capture,
    input wire accumulator_half_select,
    input wire ldac_select_highway_right,

    input wire [1:0] writeback_select,

    input wire lane_active,

    output wire predicate_out,
    output wire predicate_write_qualified
    output wire [15:0] accumulator_value,
);

    wire lane_commit;
    assign lane_commit = instruction_commit & lane_active;

    wire qualified_register_write;
    wire qualified_accumulator_clear;
    wire qualified_accumulator_load;
    wire qualified_accumulator_mac_capture;

    assign qualified_register_write = lane_commit & register_write_enable;
    assign qualified_accumulator_clear = lane_commit & accumulator_clear;
    assign qualified_accumulator_load = lane_commit & accumulator_load;
    assign qualified_accumulator_mac_capture = lane_commit & accumulator_mac_capture;

    assign predicate_write_qualified = lane_commit & predicate_write_enable;

    wire [7:0] read_data_even;
    wire [7:0] read_data_odd;
    wire [7:0] writeback_bus;

    clm_regfile lane_regfile (
        .clk (clk),
        .write_enable (qualified_register_write),
        .write_address (rd_address),
        .write_data (writeback_bus),
        .read_row_even (read_row_even),
        .read_data_even (read_data_even),
        .read_row_odd (read_row_odd),
        .read_data_odd (read_data_odd)
    );

    // 0 even 1 odd, same selector feeds both the capture and the replay cycle
    wire [7:0] conflict_bank_data;
    assign conflict_bank_data = conflict_bank_select ? read_data_odd : read_data_even;

    // not commit-gated on purpose, must grab rs before the instruction can retire
    reg [7:0] operand_hold;

    always @(posedge clk) begin
        if (operand_hold_load) begin
            operand_hold <= conflict_bank_data;
        end
    end

    wire [7:0] highway_left;
    wire [7:0] highway_right;

    assign highway_left = operand_hold_use ? operand_hold : read_data_even;
    assign highway_right = operand_hold_use ? conflict_bank_data : read_data_odd;

    // please work
    clm_alu lane_alu (
        .clk (clk),
        .highway_left (highway_left),
        .highway_right (highway_right),
        .immediate_value (immediate_value),
        .select_immediate (select_immediate),
        .force_one_box1 (force_one_box1),
        .force_one_box2 (force_one_box2),
        .swap_operands (swap_operands),
        .subtract_prepare (subtract_prepare),
        .prepare_zero (prepare_zero),
        .select_accumulator (select_accumulator),
        .shift_direction (shift_direction),
        .bitwise_select (bitwise_select),
        .condition_select (condition_select),
        .accumulator_clear (qualified_accumulator_clear),
        .accumulator_load (qualified_accumulator_load),
        .accumulator_mac_capture (qualified_accumulator_mac_capture),
        .accumulator_half_select (accumulator_half_select),
        .ldac_select_highway_right (ldac_select_highway_right),
        .writeback_select (writeback_select),
        .writeback_bus (writeback_bus),
        .predicate_out (predicate_out),
        .accumulator_value (accumulator_value)
    );

endmodule
`default_nettype wire