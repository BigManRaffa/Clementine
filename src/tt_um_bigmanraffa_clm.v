/*
 * Copyright (c) 2026 Rafeed Khan
 * SPDX-License-Identifier: Apache-2.0
 */
`default_nettype none

module tt_um_bigmanraffa_clm (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

    assign uio_oe = 8'b00000100;
    assign uio_out[7:3] = 5'b00000;
    assign uio_out[1:0] = 2'b00;

    wire go;
    wire instruction_shift_enable;
    wire [15:0] instruction_shift_data;

    wire [15:0] current_instruction;
    wire replay_state;
    wire instruction_commit;
    wire operand_hold_load;
    wire operand_hold_use;
    wire [1:0] read_row_even;
    wire [1:0] read_row_odd;
    wire conflict_bank_select;
    wire command_reconverge_pop;
    wire done;

    wire [2:0] rd_address;
    wire [2:0] rs_address;
    wire [2:0] rt_address;
    wire [7:0] immediate_value;

    wire uses_both_sources;
    wire bank_conflict;
    wire is_halt;

    wire command_ifp;
    wire command_else;
    wire [3:0] mask_target;

    wire select_immediate;
    wire force_one_box1;
    wire force_one_box2;
    wire swap_operands;

    wire subtract_prepare;
    wire prepare_zero;
    wire select_accumulator;

    wire shift_direction;
    wire [1:0] bitwise_select;
    wire [1:0] condition_select;

    wire accumulator_clear;
    wire accumulator_load;
    wire accumulator_mac_capture;
    wire accumulator_half_select;
    wire ldac_select_highway_right;

    wire [1:0] writeback_select;
    wire register_write_enable;
    wire predicate_write_enable;

    wire [3:0] lane_active;
    wire stack_top_valid;
    wire stack_top_type;
    wire [3:0] stack_top_target;

    wire [3:0] predicate_out;
    wire [3:0] predicate_write_qualified;

    wire [15:0] lane0_accumulator;
    wire [15:0] lane1_accumulator;
    wire [15:0] lane2_accumulator;
    wire [15:0] lane3_accumulator;

    wire laneid_mode;
    
    wire any_lane_active;
    assign any_lane_active = |lane_active;

    wire mask_rst_n;
    assign mask_rst_n = rst_n & (~go);

    assign uo_out[0] = done;
    assign uo_out[1] = any_lane_active;
    assign uo_out[2] = instruction_commit;
    assign uo_out[3] = replay_state;
    assign uo_out[7:4] = 4'b0000;

    clm_spi_host spi_host (
        .clk (clk),
        .rst_n (rst_n),

        .spi_sclk (uio_in[3]),
        .spi_mosi (uio_in[1]),
        .spi_cs_n (uio_in[0]),
        .spi_miso (uio_out[2]),

        .sequencer_done (done),

        .lane0_accumulator (lane0_accumulator),
        .lane1_accumulator (lane1_accumulator),
        .lane2_accumulator (lane2_accumulator),
        .lane3_accumulator (lane3_accumulator),

        .instruction_shift_enable (instruction_shift_enable),
        .instruction_shift_data (instruction_shift_data),
        .go (go)
    );

    clm_fetch_seq fetch_seq (
        .clk (clk),
        .rst_n (rst_n),

        .go (go),

        .instruction_shift_enable (instruction_shift_enable),
        .instruction_shift_data (instruction_shift_data),

        .rs_address (rs_address),
        .rt_address (rt_address),
        .bank_conflict (bank_conflict),
        .is_halt (is_halt),

        .any_lane_active (any_lane_active),

        .stack_top_valid (stack_top_valid),
        .stack_top_type (stack_top_type),
        .stack_top_target (stack_top_target),

        .current_instruction (current_instruction),
        .replay_state (replay_state),

        .instruction_commit (instruction_commit),
        .operand_hold_load (operand_hold_load),
        .operand_hold_use (operand_hold_use),
        .read_row_even (read_row_even),
        .read_row_odd (read_row_odd),
        .conflict_bank_select (conflict_bank_select),

        .command_reconverge_pop (command_reconverge_pop),

        .done (done)
    );

    clm_decoder decoder (
        .current_instruction (current_instruction),
        .replay_state (replay_state),

        .rd_address (rd_address),
        .rs_address (rs_address),
        .rt_address (rt_address),
        .immediate_value (immediate_value),

        .uses_both_sources (uses_both_sources),
        .bank_conflict (bank_conflict),
        .is_halt (is_halt),

        .command_ifp (command_ifp),
        .command_else (command_else),
        .mask_target (mask_target),

        .select_immediate (select_immediate),
        .force_one_box1 (force_one_box1),
        .force_one_box2 (force_one_box2),
        .swap_operands (swap_operands),

        .laneid_mode (laneid_mode),

        .subtract_prepare (subtract_prepare),
        .prepare_zero (prepare_zero),
        .select_accumulator (select_accumulator),

        .shift_direction (shift_direction),
        .bitwise_select (bitwise_select),
        .condition_select (condition_select),

        .accumulator_clear (accumulator_clear),
        .accumulator_load (accumulator_load),
        .accumulator_mac_capture (accumulator_mac_capture),
        .accumulator_half_select (accumulator_half_select),
        .ldac_select_highway_right (ldac_select_highway_right),

        .writeback_select (writeback_select),
        .register_write_enable (register_write_enable),
        .predicate_write_enable (predicate_write_enable)
    );

    clm_mask_stack mask_stack (
        .clk (clk),
        .rst_n (mask_rst_n),

        .predicate_out (predicate_out),
        .predicate_write_qualified (predicate_write_qualified),

        .command_ifp (command_ifp & instruction_commit),
        .command_else (command_else & instruction_commit),
        .command_reconverge_pop (command_reconverge_pop),

        .target_address (mask_target),

        .lane_active (lane_active),

        .stack_top_valid (stack_top_valid),
        .stack_top_type (stack_top_type),
        .stack_top_target (stack_top_target)
    );

    clm_lane #(.LANE_ID(2'd0)) lane0 (
        .clk (clk),

        .read_row_even (read_row_even),
        .read_row_odd (read_row_odd),
        .conflict_bank_select (conflict_bank_select),
        .rd_address (rd_address),
        .immediate_value (immediate_value),

        .operand_hold_load (operand_hold_load),
        .operand_hold_use (operand_hold_use),
        .instruction_commit (instruction_commit),
        .register_write_enable (register_write_enable),
        .predicate_write_enable (predicate_write_enable),

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

        .accumulator_clear (accumulator_clear),
        .accumulator_load (accumulator_load),
        .accumulator_mac_capture (accumulator_mac_capture),
        .accumulator_half_select (accumulator_half_select),
        .ldac_select_highway_right (ldac_select_highway_right),

        .writeback_select (writeback_select),

        .lane_active (lane_active[0]),

        .laneid_mode (laneid_mode),

        .predicate_out (predicate_out[0]),
        .predicate_write_qualified (predicate_write_qualified[0]),
        .accumulator_value (lane0_accumulator)
    );

    clm_lane #(.LANE_ID(2'd1)) lane1 (
        .clk (clk),

        .read_row_even (read_row_even),
        .read_row_odd (read_row_odd),
        .conflict_bank_select (conflict_bank_select),
        .rd_address (rd_address),
        .immediate_value (immediate_value),

        .operand_hold_load (operand_hold_load),
        .operand_hold_use (operand_hold_use),
        .instruction_commit (instruction_commit),
        .register_write_enable (register_write_enable),
        .predicate_write_enable (predicate_write_enable),

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

        .accumulator_clear (accumulator_clear),
        .accumulator_load (accumulator_load),
        .accumulator_mac_capture (accumulator_mac_capture),
        .accumulator_half_select (accumulator_half_select),
        .ldac_select_highway_right (ldac_select_highway_right),

        .writeback_select (writeback_select),

        .lane_active (lane_active[1]),

        .laneid_mode (laneid_mode),

        .predicate_out (predicate_out[1]),
        .predicate_write_qualified (predicate_write_qualified[1]),
        .accumulator_value (lane1_accumulator)
    );

    clm_lane #(.LANE_ID(2'd2)) lane2 (
        .clk (clk),

        .read_row_even (read_row_even),
        .read_row_odd (read_row_odd),
        .conflict_bank_select (conflict_bank_select),
        .rd_address (rd_address),
        .immediate_value (immediate_value),

        .operand_hold_load (operand_hold_load),
        .operand_hold_use (operand_hold_use),
        .instruction_commit (instruction_commit),
        .register_write_enable (register_write_enable),
        .predicate_write_enable (predicate_write_enable),

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

        .accumulator_clear (accumulator_clear),
        .accumulator_load (accumulator_load),
        .accumulator_mac_capture (accumulator_mac_capture),
        .accumulator_half_select (accumulator_half_select),
        .ldac_select_highway_right (ldac_select_highway_right),

        .writeback_select (writeback_select),

        .lane_active (lane_active[2]),

        .laneid_mode (laneid_mode),

        .predicate_out (predicate_out[2]),
        .predicate_write_qualified (predicate_write_qualified[2]),
        .accumulator_value (lane2_accumulator)
    );

    clm_lane #(.LANE_ID(2'd3)) lane3 (
        .clk (clk),

        .read_row_even (read_row_even),
        .read_row_odd (read_row_odd),
        .conflict_bank_select (conflict_bank_select),
        .rd_address (rd_address),
        .immediate_value (immediate_value),

        .operand_hold_load (operand_hold_load),
        .operand_hold_use (operand_hold_use),
        .instruction_commit (instruction_commit),
        .register_write_enable (register_write_enable),
        .predicate_write_enable (predicate_write_enable),

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

        .accumulator_clear (accumulator_clear),
        .accumulator_load (accumulator_load),
        .accumulator_mac_capture (accumulator_mac_capture),
        .accumulator_half_select (accumulator_half_select),
        .ldac_select_highway_right (ldac_select_highway_right),

        .writeback_select (writeback_select),

        .lane_active (lane_active[3]),

        .laneid_mode (laneid_mode),

        .predicate_out (predicate_out[3]),
        .predicate_write_qualified (predicate_write_qualified[3]),
        .accumulator_value (lane3_accumulator)
    );

    // List all unused inputs to prevent warnings
    wire _unused = &{ui_in, uio_in[7:4], uio_in[2], ena, uses_both_sources, 1'b0};

endmodule
`default_nettype wire