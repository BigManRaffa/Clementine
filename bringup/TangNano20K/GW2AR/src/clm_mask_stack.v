`default_nettype none

module clm_mask_stack (
    input wire clk,
    input wire rst_n,

    input wire [3:0] predicate_out,
    input wire [3:0] predicate_write_qualified,

    input wire command_ifp,
    input wire command_else,
    input wire command_reconverge_pop,

    input wire [3:0] target_address,

    output wire [3:0] lane_active,

    output wire stack_top_valid,
    output wire stack_top_type,
    output wire [3:0] stack_top_target
);

    localparam TOKEN_ELSE = 1'b0;
    localparam TOKEN_RECONVERGE = 1'b1;

    reg [3:0] stored_predicate;

    always @(posedge clk) begin
        if (predicate_write_qualified[0]) stored_predicate[0] <= predicate_out[0];
        if (predicate_write_qualified[1]) stored_predicate[1] <= predicate_out[1];
        if (predicate_write_qualified[2]) stored_predicate[2] <= predicate_out[2];
        if (predicate_write_qualified[3]) stored_predicate[3] <= predicate_out[3];
    end

    reg [3:0] current_mask;

    reg [3:0] stack0_mask;
    reg [3:0] stack0_target;
    reg stack0_type;
    reg stack0_valid;

    reg [3:0] stack1_mask;
    reg [3:0] stack1_target;
    reg stack1_type;
    reg stack1_valid;

    always @(posedge clk) begin
        if (!rst_n) begin
            current_mask <= 4'b1111;
            stack0_valid <= 1'b0;
            stack1_valid <= 1'b0;
        end
        else if (command_reconverge_pop) begin
            current_mask <= stack0_mask;

            stack0_mask <= stack1_mask;
            stack0_target <= stack1_target;
            stack0_type <= stack1_type;
            stack0_valid <= stack1_valid;

            stack1_valid <= 1'b0;
        end
        else if (command_else) begin
            // rebuild parent by or-ing the else token's false mask with current, they are disjoint slices of it
            current_mask <= stack0_mask;

            stack0_mask <= current_mask | stack0_mask;
            stack0_target <= target_address;
            stack0_type <= TOKEN_RECONVERGE;
        end
        else if (command_ifp) begin
            current_mask <= stored_predicate & current_mask;

            stack1_mask <= stack0_mask;
            stack1_target <= stack0_target;
            stack1_type <= stack0_type;
            stack1_valid <= stack0_valid;

            stack0_mask <= (~stored_predicate) & current_mask;
            stack0_target <= target_address;
            stack0_type <= TOKEN_ELSE;
            stack0_valid <= 1'b1;
        end
    end

    assign lane_active = current_mask;

    assign stack_top_valid = stack0_valid;
    assign stack_top_type = stack0_type;
    assign stack_top_target = stack0_target;

endmodule
`default_nettype wire