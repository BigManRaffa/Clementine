`default_nettype none

// output = (box1 x box2) + box3
// highways are fixed physical buses 
// swap_operands = 1 means rs/rt landed on the reversed highways (re-route only, same logical op

module clm_alu (
    input  wire        clk,

    input  wire [7:0]  highway_left,
    input  wire [7:0]  highway_right,
    input  wire [7:0]  immediate_value,

    input  wire        select_immediate,
    input  wire        force_one_box1,
    input  wire        force_one_box2,
    input  wire        swap_operands,
    input  wire        subtract_prepare,
    input  wire        prepare_zero,
    input  wire        select_accumulator,

    input  wire        shift_direction,
    input  wire [1:0]  bitwise_select,
    input  wire [1:0]  condition_select,

    input  wire        accumulator_clear,
    input  wire        accumulator_load,
    input  wire        accumulator_mac_capture,
    input  wire        accumulator_half_select,
    input  wire        ldac_select_highway_right,

    input  wire [1:0]  writeback_select,

    output wire [7:0]  writeback_bus,
    output wire        predicate_out
);

    wire [7:0] box1_source;
    assign box1_source = select_immediate ? immediate_value : highway_left;

    wire force_one_box1_n;
    assign force_one_box1_n = ~force_one_box1;

    // force +1, not all ones, all ones reads as -1 to the signed multiplier
    wire [7:0] box1_value;
    assign box1_value[0]   = box1_source[0] | force_one_box1;
    assign box1_value[7:1] = box1_source[7:1] & {7{force_one_box1_n}};

    wire force_one_box2_n;
    assign force_one_box2_n = ~force_one_box2;

    wire [7:0] box2_value;
    assign box2_value[0]   = highway_right[0] | force_one_box2;
    assign box2_value[7:1] = highway_right[7:1] & {7{force_one_box2_n}};

    // multiplying by forced +1 doubles as the 16-bit sign extender
    wire [15:0] engine_product;

    clm_mult_bw engine_multiplier (.a (box1_value), .b (box2_value), .product (engine_product));

    wire [7:0] selected_subtrahend;
    assign selected_subtrahend = swap_operands ? highway_left : highway_right;

    wire [7:0] subtrahend_conditioned;
    assign subtrahend_conditioned = selected_subtrahend ^ {8{subtract_prepare}};

    // sign-extend then invert = 8 xors 
    // +1 sent to adder carry-in
    wire [15:0] prepared_arithmetic_operand;
    assign prepared_arithmetic_operand[7:0]  = prepare_zero ? 8'h00 : subtrahend_conditioned;
    assign prepared_arithmetic_operand[15:8] = prepare_zero ? 8'h00 : {8{subtrahend_conditioned[7]}};

    // only state in the module, no reset by design cuz clracc is the program's clear
    reg [15:0] accumulator;
    wire [15:0] box3_value;
    assign box3_value = select_accumulator ? accumulator : prepared_arithmetic_operand;

    wire [15:0] adder_result;
    wire [16:0] adder_carry;

    assign adder_carry[0] = subtract_prepare;   // completes ~rt + 1

    clm_fa adder_bit0  (.a (engine_product[0]),  .b (box3_value[0]),  .cin (adder_carry[0]),  .sum (adder_result[0]),  .cout (adder_carry[1]));
    clm_fa adder_bit1  (.a (engine_product[1]),  .b (box3_value[1]),  .cin (adder_carry[1]),  .sum (adder_result[1]),  .cout (adder_carry[2]));
    clm_fa adder_bit2  (.a (engine_product[2]),  .b (box3_value[2]),  .cin (adder_carry[2]),  .sum (adder_result[2]),  .cout (adder_carry[3]));
    clm_fa adder_bit3  (.a (engine_product[3]),  .b (box3_value[3]),  .cin (adder_carry[3]),  .sum (adder_result[3]),  .cout (adder_carry[4]));
    clm_fa adder_bit4  (.a (engine_product[4]),  .b (box3_value[4]),  .cin (adder_carry[4]),  .sum (adder_result[4]),  .cout (adder_carry[5]));
    clm_fa adder_bit5  (.a (engine_product[5]),  .b (box3_value[5]),  .cin (adder_carry[5]),  .sum (adder_result[5]),  .cout (adder_carry[6]));
    clm_fa adder_bit6  (.a (engine_product[6]),  .b (box3_value[6]),  .cin (adder_carry[6]),  .sum (adder_result[6]),  .cout (adder_carry[7]));
    clm_fa adder_bit7  (.a (engine_product[7]),  .b (box3_value[7]),  .cin (adder_carry[7]),  .sum (adder_result[7]),  .cout (adder_carry[8]));
    clm_fa adder_bit8  (.a (engine_product[8]),  .b (box3_value[8]),  .cin (adder_carry[8]),  .sum (adder_result[8]),  .cout (adder_carry[9]));
    clm_fa adder_bit9  (.a (engine_product[9]),  .b (box3_value[9]),  .cin (adder_carry[9]),  .sum (adder_result[9]),  .cout (adder_carry[10]));
    clm_fa adder_bit10 (.a (engine_product[10]), .b (box3_value[10]), .cin (adder_carry[10]), .sum (adder_result[10]), .cout (adder_carry[11]));
    clm_fa adder_bit11 (.a (engine_product[11]), .b (box3_value[11]), .cin (adder_carry[11]), .sum (adder_result[11]), .cout (adder_carry[12]));
    clm_fa adder_bit12 (.a (engine_product[12]), .b (box3_value[12]), .cin (adder_carry[12]), .sum (adder_result[12]), .cout (adder_carry[13]));
    clm_fa adder_bit13 (.a (engine_product[13]), .b (box3_value[13]), .cin (adder_carry[13]), .sum (adder_result[13]), .cout (adder_carry[14]));
    clm_fa adder_bit14 (.a (engine_product[14]), .b (box3_value[14]), .cin (adder_carry[14]), .sum (adder_result[14]), .cout (adder_carry[15]));
    clm_fa adder_bit15 (.a (engine_product[15]), .b (box3_value[15]), .cin (adder_carry[15]), .sum (adder_result[15]), .cout (adder_carry[16]));
    // 8-bit diff can't overflow 16 bits so no adder_carry[16]

    // shared by the bitwise XOR instruction and CMP equality detection
    wire [7:0] bitwise_xor_result;
    assign bitwise_xor_result = highway_left ^ highway_right;

    wire subtraction_sign;
    wire subtraction_zero;

    assign subtraction_sign = adder_result[8];

    // equality is reused from the bitwise xor
    // rs == rt only when every xor bit is zero
    assign subtraction_zero = ~(|bitwise_xor_result);

    wire condition_lt;
    wire condition_gt;
    wire condition_le;
    wire condition_ge;

    assign condition_lt = subtraction_sign;
    assign condition_gt = (~subtraction_sign) & (~subtraction_zero);
    assign condition_le = subtraction_sign | subtraction_zero;
    assign condition_ge = ~subtraction_sign;

    // condition_select: 00 lt, 01 gt, 10 le, 11 ge
    assign predicate_out = condition_select[1]
        ? (condition_select[0] ? condition_ge : condition_le)
        : (condition_select[0] ? condition_gt : condition_lt);

    wire [7:0] shifter_data;
    wire [2:0] shifter_amount;

    assign shifter_data   = swap_operands ? highway_right      : highway_left;
    assign shifter_amount = swap_operands ? highway_left[2:0]  : highway_right[2:0];

    // shl = reverse, shr, reverse back
    // so one right ladder serves both directions
    wire [7:0] shifter_entry;
    assign shifter_entry = shift_direction ? shifter_data : {shifter_data[0], shifter_data[1], shifter_data[2], shifter_data[3], shifter_data[4], 
    shifter_data[5], shifter_data[6], shifter_data[7]};

    wire [7:0] shifter_stage_one;
    wire [7:0] shifter_stage_two;
    wire [7:0] shifter_stage_four;

    assign shifter_stage_one  = shifter_amount[0] ? {1'b0,    shifter_entry[7:1]}     : shifter_entry;
    assign shifter_stage_two  = shifter_amount[1] ? {2'b00,   shifter_stage_one[7:2]} : shifter_stage_one;
    assign shifter_stage_four = shifter_amount[2] ? {4'b0000, shifter_stage_two[7:4]} : shifter_stage_two;

    wire [7:0] shifter_result;
    assign shifter_result = shift_direction ? shifter_stage_four : {shifter_stage_four[0], shifter_stage_four[1], shifter_stage_four[2], shifter_stage_four[3], 
    shifter_stage_four[4], shifter_stage_four[5], shifter_stage_four[6], shifter_stage_four[7]};

    wire [7:0] bitwise_and_result;
    wire [7:0] bitwise_or_result;

    assign bitwise_and_result = highway_left & highway_right;
    assign bitwise_or_result  = highway_left | highway_right;

    // bitwise_select: 00 and, 01 or, 1x xor
    wire [7:0] bitwise_selected;
    assign bitwise_selected = bitwise_select[1] ? bitwise_xor_result : (bitwise_select[0] ? bitwise_or_result : bitwise_and_result);


    wire [7:0] ldac_source;
    assign ldac_source = ldac_select_highway_right ? highway_right : highway_left;

    always @(posedge clk) begin
        if (accumulator_clear) begin
            accumulator[15:8] <= 8'h00;
            accumulator[7:0]  <= 8'h00;
        end
        else if (accumulator_load) begin
            if (accumulator_half_select) begin
                accumulator[15:8] <= ldac_source;
            end
            else begin
                accumulator[7:0]  <= ldac_source;
            end
        end
        else if (accumulator_mac_capture) begin
            accumulator[15:0] <= adder_result[15:0];
        end
    end

    wire [7:0] mvac_byte;
    assign mvac_byte = accumulator_half_select ? accumulator[15:8] : accumulator[7:0];

    // why does switching from a one-hot merge to a 2:1 writeback selector save me 50 cells
    wire [7:0] writeback_pair_low;
    wire [7:0] writeback_pair_high;

    assign writeback_pair_low = writeback_select[0] ? shifter_result : adder_result[7:0];

    assign writeback_pair_high = writeback_select[0] ? mvac_byte : bitwise_selected;

    assign writeback_bus = writeback_select[1] ? writeback_pair_high : writeback_pair_low;

endmodule
`default_nettype wire