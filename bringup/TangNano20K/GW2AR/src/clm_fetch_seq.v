`default_nettype none

module clm_fetch_seq (
    input wire clk,
    input wire rst_n,

    input wire go,

    input wire instruction_valid,
    input wire [15:0] instruction_in,

    input wire [2:0] rs_address,
    input wire [2:0] rt_address,
    input wire bank_conflict,
    input wire is_halt,

    input wire any_lane_active,

    input wire stack_top_valid,
    input wire stack_top_type,
    input wire [3:0] stack_top_target,

    output wire [15:0] current_instruction,
    output wire replay_state,

    output wire instruction_commit,
    output wire operand_hold_load,
    output wire operand_hold_use,
    output wire [1:0] read_row_even,
    output wire [1:0] read_row_odd,
    output wire conflict_bank_select,

    output wire command_reconverge_pop,

    output wire done
);

    localparam TOKEN_RECONVERGE = 1'b1;
    localparam TOKEN_ELSE = 1'b0;

    localparam ST_NORMAL = 1'b0;
    localparam ST_REPLAY = 1'b1;

    reg [15:0] slot0, slot2, slot4, slot6;
    reg [15:0] slot1, slot3, slot5, slot7;

    reg [2:0] write_pointer;

    always @(posedge clk) begin
        if (!rst_n) write_pointer <= 3'd0;
        else if (go) write_pointer <= 3'd0;
        else if (instruction_valid) write_pointer <= write_pointer + 3'd1;
    end

    wire wbank_even = ~write_pointer[0];
    wire [1:0] wrow = write_pointer[2:1];

    wire e0 = instruction_valid &  wbank_even & (wrow == 2'd0);
    wire e2 = instruction_valid &  wbank_even & (wrow == 2'd1);
    wire e4 = instruction_valid &  wbank_even & (wrow == 2'd2);
    wire e6 = instruction_valid &  wbank_even & (wrow == 2'd3);
    wire o1 = instruction_valid & ~wbank_even & (wrow == 2'd0);
    wire o3 = instruction_valid & ~wbank_even & (wrow == 2'd1);
    wire o5 = instruction_valid & ~wbank_even & (wrow == 2'd2);
    wire o7 = instruction_valid & ~wbank_even & (wrow == 2'd3);

    always @(posedge clk) begin
        if (e0) slot0 <= instruction_in;
        if (e2) slot2 <= instruction_in;
        if (e4) slot4 <= instruction_in;
        if (e6) slot6 <= instruction_in;
        if (o1) slot1 <= instruction_in;
        if (o3) slot3 <= instruction_in;
        if (o5) slot5 <= instruction_in;
        if (o7) slot7 <= instruction_in;
    end

    wire [1:0] rrow = logical_pc[2:1];

    wire [15:0] even_data = rrow[1] ? (rrow[0] ? slot6 : slot4) : (rrow[0] ? slot2 : slot0);
    wire [15:0] odd_data  = rrow[1] ? (rrow[0] ? slot7 : slot5) : (rrow[0] ? slot3 : slot1);

    assign current_instruction = logical_pc[0] ? odd_data : even_data;

    // halted resets HIGH, machine powers up frozen. the current instruction
    // is the only stored word: 16 flops, not the old 256.
    reg [3:0] logical_pc;
    reg fsm_state;
    reg halted;

    wire in_replay;
    assign in_replay = (fsm_state == ST_REPLAY);

    assign replay_state = in_replay;

    wire scan_mode;
    assign scan_mode = (~any_lane_active) & (~halted) & (~in_replay);

    // self-clearing, the pop moves the stack top, so next cycle the match is gone
    wire reconverge_bubble;
    assign reconverge_bubble = stack_top_valid & (stack_top_type == TOKEN_RECONVERGE) & (logical_pc == stack_top_target) & (~in_replay) & (~halted);

    // the one dead region instruction allowed to commit, it wakes the false lanes
    wire else_boundary;
    assign else_boundary = scan_mode & stack_top_valid & (stack_top_type == TOKEN_ELSE) & (logical_pc == stack_top_target);

    wire capture_event;
    assign capture_event = bank_conflict & (~scan_mode) & (~reconverge_bubble) & (~in_replay) & (~halted);

    assign instruction_commit = (~halted) & (~instruction_valid) & ( in_replay | else_boundary | ((~scan_mode) & (~capture_event) & (~reconverge_bubble)) );

    wire scan_step;
    assign scan_step = scan_mode & (~reconverge_bubble) & (~else_boundary);

    // skipped halt has commit low so it's ignored for free
    wire halt_event;
    assign halt_event = is_halt & instruction_commit;

    assign operand_hold_load = capture_event;
    assign operand_hold_use = in_replay;
    assign command_reconverge_pop = reconverge_bubble;
    assign done = halted;

    // replay flips the conflicted bank to rt's rows, the other side is a dont care
    assign read_row_even = (rs_address[0] | in_replay) ? rt_address[2:1] : rs_address[2:1];
    assign read_row_odd = (rs_address[0] & (~in_replay)) ? rs_address[2:1] : rt_address[2:1];

    assign conflict_bank_select = rs_address[0];

    wire execution_advance;
    assign execution_advance = (~instruction_valid) & (instruction_commit | scan_step);

    always @(posedge clk) begin
        if (!rst_n) begin
            logical_pc <= 4'h0;
            fsm_state <= ST_NORMAL;
            halted <= 1'b1;
        end
        else if (go) begin
            logical_pc <= 4'h0;
            fsm_state <= ST_NORMAL;
            halted <= 1'b0;
        end
        else begin
            if (capture_event) begin
                fsm_state <= ST_REPLAY;
            end
            else if (in_replay) begin
                fsm_state <= ST_NORMAL;
            end

            if (halt_event) begin
                halted <= 1'b1;
            end

            if (execution_advance) begin
                logical_pc <= logical_pc + 4'h1;
            end
        end
    end

endmodule
`default_nettype wire