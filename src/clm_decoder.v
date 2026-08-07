`default_nettype none

// layer 1: opcode grid, one wire high per cycle
// layer 2: flat or per output
// outputs are raw
module clm_decoder (
    input wire [15:0] current_instruction,
    input wire replay_state,

    output wire [2:0] rd_address,
    output wire [2:0] rs_address,
    output wire [2:0] rt_address,
    output wire [7:0] immediate_value,

    output wire uses_both_sources,
    output wire bank_conflict,
    output wire is_halt,

    output wire command_ifp,
    output wire command_else,
    output wire [3:0] mask_target,

    output wire select_immediate,
    output wire force_one_box1,
    output wire force_one_box2,
    output wire swap_operands,

    output wire laneid_mode,

    output wire subtract_prepare,
    output wire prepare_zero,
    output wire select_accumulator,

    output wire shift_direction,
    output wire [1:0] bitwise_select,
    output wire [1:0] condition_select,

    output wire accumulator_clear,
    output wire accumulator_load,
    output wire accumulator_mac_capture,
    output wire accumulator_half_select,
    output wire ldac_select_highway_right,

    output wire [1:0] writeback_select,
    output wire register_write_enable,
    output wire predicate_write_enable
);

    // sliced by format, a slice only matters under its own opcode, so overlaps dont clash
    assign rd_address = current_instruction[11:9];
    assign rs_address = current_instruction[8:6];
    assign rt_address = current_instruction[5:3];
    assign immediate_value = current_instruction[8:1];
    assign mask_target = current_instruction[3:0];

    wire field_sel;
    wire field_dir;
    wire field_subop;

    assign field_sel = current_instruction[3];
    assign field_dir = current_instruction[0];
    assign field_subop = current_instruction[4];

    wire opcode_b3;
    wire opcode_b2;
    wire opcode_b1;
    wire opcode_b0;

    assign opcode_b3 = current_instruction[15];
    assign opcode_b2 = current_instruction[14];
    assign opcode_b1 = current_instruction[13];
    assign opcode_b0 = current_instruction[12];

    wire select_nop;
    wire select_add;
    wire select_sub;
    wire select_and;
    wire select_or;
    wire select_xor;
    wire select_shift;
    wire select_mac;
    wire select_clracc;
    wire select_ldi;
    wire select_mov;
    wire select_cmp;
    wire select_mvac;
    wire select_ldac;
    wire select_ifp_else;
    wire select_halt;

    assign select_nop = (~opcode_b3) & (~opcode_b2) & (~opcode_b1) & (~opcode_b0); // 0000
    assign select_add = (~opcode_b3) & (~opcode_b2) & (~opcode_b1) & ( opcode_b0); // 0001
    assign select_sub = (~opcode_b3) & (~opcode_b2) & ( opcode_b1) & (~opcode_b0); // 0010
    assign select_and = (~opcode_b3) & (~opcode_b2) & ( opcode_b1) & ( opcode_b0); // 0011
    assign select_or = (~opcode_b3) & ( opcode_b2) & (~opcode_b1) & (~opcode_b0); // 0100
    assign select_xor = (~opcode_b3) & ( opcode_b2) & (~opcode_b1) & ( opcode_b0); // 0101
    assign select_shift = (~opcode_b3) & ( opcode_b2) & ( opcode_b1) & (~opcode_b0); // 0110
    assign select_mac = (~opcode_b3) & ( opcode_b2) & ( opcode_b1) & ( opcode_b0); // 0111
    assign select_clracc = ( opcode_b3) & (~opcode_b2) & (~opcode_b1) & (~opcode_b0); // 1000
    assign select_ldi = ( opcode_b3) & (~opcode_b2) & (~opcode_b1) & ( opcode_b0); // 1001
    assign select_mov = ( opcode_b3) & (~opcode_b2) & ( opcode_b1) & (~opcode_b0); // 1010
    assign select_cmp = ( opcode_b3) & (~opcode_b2) & ( opcode_b1) & ( opcode_b0); // 1011
    assign select_mvac = ( opcode_b3) & ( opcode_b2) & (~opcode_b1) & (~opcode_b0); // 1100
    assign select_ldac = ( opcode_b3) & ( opcode_b2) & (~opcode_b1) & ( opcode_b0); // 1101
    assign select_ifp_else = ( opcode_b3) & ( opcode_b2) & ( opcode_b1) & (~opcode_b0); // 1110
    assign select_halt = ( opcode_b3) & ( opcode_b2) & ( opcode_b1) & ( opcode_b0); // 1111

    // bit[4] splits 1110 into ifp vs else
    wire select_ifp;
    wire select_else;

    assign select_ifp = select_ifp_else & (~field_subop);
    assign select_else = select_ifp_else & ( field_subop);

    // rs odd, rt even, so they land on swapped highways, only sub/cmp/shift care
    wire reversed_physical_order;
    assign reversed_physical_order = rs_address[0] & (~rt_address[0]);

    wire sub_normal_routing;
    wire sub_reversed_routing;
    wire cmp_normal_routing;
    wire cmp_reversed_routing;
    wire mov_from_even_bank;
    wire mov_from_odd_bank;

    assign sub_normal_routing = select_sub & (~reversed_physical_order);
    assign sub_reversed_routing = select_sub & ( reversed_physical_order);
    assign cmp_normal_routing = select_cmp & (~reversed_physical_order);
    assign cmp_reversed_routing = select_cmp & ( reversed_physical_order);
    assign mov_from_even_bank = select_mov & (~rs_address[0]);
    assign mov_from_odd_bank = select_mov & ( rs_address[0]);

    assign laneid_mode = select_mov & current_instruction[0];

    assign select_immediate = select_ldi | laneid_mode;

    // force +1 into the box thats not holding the source. the other box passes it, x1 also sign extends
    assign force_one_box1 = (sub_reversed_routing | cmp_reversed_routing | mov_from_odd_bank) & (~laneid_mode);

    assign force_one_box2 = select_add | sub_normal_routing | cmp_normal_routing | select_ldi | mov_from_even_bank | laneid_mode;

    // low on replay, front end already fixed rs left rt right, nothing to swap
    wire reversal_applicable;
    assign reversal_applicable = select_sub | select_cmp | select_shift;

    assign swap_operands = reversal_applicable & reversed_physical_order & (~replay_state);

    assign subtract_prepare = select_sub | select_cmp;
    assign prepare_zero = select_mov | select_ldi;
    assign select_accumulator = select_mac;

    assign shift_direction = field_dir;

    assign bitwise_select[1] = select_xor;
    assign bitwise_select[0] = select_or;

    assign condition_select = current_instruction[11:10];

    assign accumulator_clear = select_clracc;
    assign accumulator_load = select_ldac;
    assign accumulator_mac_capture = select_mac;
    assign accumulator_half_select = field_sel;
    assign ldac_select_highway_right = rs_address[0];

    // 00 arith, 01 shift, 10 bitwise, 11 mvac. non-writers sit at 00, fine cuz write_enable is low
    assign writeback_select[1] = select_and | select_or | select_xor | select_mvac;
    assign writeback_select[0] = select_shift | select_mvac;

    assign register_write_enable = select_add | select_sub | select_and | select_or | select_xor | select_shift | select_ldi | select_mov | select_mvac;

    assign predicate_write_enable = select_cmp;

    assign command_ifp = select_ifp;
    assign command_else = select_else;

    assign is_halt = select_halt;

    assign uses_both_sources = select_add | select_sub | select_and | select_or | select_xor | select_shift | select_mac | select_cmp;

    wire same_bank_parity;
    
    assign same_bank_parity = ~(rs_address[0] ^ rt_address[0]);

    // not replay-gated on purpose. rs/rt still in the ir so it keeps firing, sequencer just ignores it
    assign bank_conflict = uses_both_sources & same_bank_parity;

endmodule
`default_nettype wire