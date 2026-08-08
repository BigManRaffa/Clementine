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

    // halted resets HIGH, machine powers up frozen. the current instruction
    // is the only stored word: 16 flops, not the old 256.
    reg [15:0] instruction_register;
    reg [3:0] logical_pc;
    reg fsm_state;
    reg halted;

    assign current_instruction = instruction_register;

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

    wire fresh;
    assign fresh = instruction_valid;

    assign instruction_commit = (~halted) & ( in_replay | (fresh & (else_boundary | ((~scan_mode) & (~capture_event) & (~reconverge_bubble)))) );

    wire scan_step;
    assign scan_step = fresh & scan_mode & (~reconverge_bubble) & (~else_boundary);

    // skipped halt has commit low so it's ignored for free
    wire halt_event;
    assign halt_event = is_halt & instruction_commit;

    assign operand_hold_load = capture_event;
    assign operand_hold_use = in_replay;
    assign command_reconverge_pop = reconverge_bubble & fresh;
    assign done = halted;

    // replay flips the conflicted bank to rt's rows, the other side is a dont care
    assign read_row_even = (rs_address[0] | in_replay) ? rt_address[2:1] : rs_address[2:1];
    assign read_row_odd = (rs_address[0] & (~in_replay)) ? rs_address[2:1] : rt_address[2:1];

    assign conflict_bank_select = rs_address[0];

    wire execution_advance;
    assign execution_advance = instruction_commit | scan_step;

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

    always @(posedge clk) begin
        if (instruction_valid & (~in_replay)) begin
            instruction_register <= instruction_in;
        end
    end

endmodule
`default_nettype wire