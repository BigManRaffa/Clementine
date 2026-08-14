`default_nettype none

module clm_mult_bw (
    input wire signed [7:0] a,
    input wire signed [7:0] b,
    output wire signed [15:0] product
);

    localparam integer op_width = 8;
    localparam integer prod_width = op_width * 2;
    localparam integer sign_index = op_width - 1;

    wire pp [0:op_width-1][0:op_width-1];

    genvar row_index;
    genvar col_index;

    // baugh-wooley fig. 3, turning two's complement operations into addition problem
    // positive terms + inverted sign-row/column terms + correction bits + sign*sign term
    generate
        for (row_index = 0; row_index < op_width; row_index++) begin : gen_pp_row
            for (col_index = 0; col_index < op_width; col_index = col_index + 1) begin : gen_pp_col

                if ((row_index == sign_index) && (col_index == sign_index)) begin : gen_pp_both_sign
                    assign pp[row_index][col_index] = a[row_index] & b[col_index];

                end else if (row_index == sign_index) begin : gen_pp_a_sign
                    assign pp[row_index][col_index] = a[row_index] & (~b[col_index]);

                end else if (col_index == sign_index) begin : gen_pp_b_sign
                    assign pp[row_index][col_index] = (~a[row_index]) & b[col_index];

                end else begin : gen_pp_plain
                    assign pp[row_index][col_index] = a[row_index] & b[col_index];
                end

            end
        end
    endgenerate

    wire correction_top;
    wire correction_hi_a;
    wire correction_hi_b;
    wire correction_lo_a;
    wire correction_lo_b;

    assign correction_top = 1'b1;
    assign correction_hi_a = ~a[sign_index];
    assign correction_hi_b = ~b[sign_index];
    assign correction_lo_a = a[sign_index];
    assign correction_lo_b = b[sign_index];

    // you can rewrite the 5 correction bits using two control signals 
    // a[7]+b[7] = (a[7] ^ b[7]) + 2(a[7] & b[7]), which gives XOR*2^7 + AND*2^8
    // mod 2^16, the upper correction becomes -XOR*2^14 - AND*2^15
    wire correction_xor;
    wire correction_and;

    assign correction_xor = correction_lo_a ^ correction_lo_b;
    assign correction_and = correction_lo_a & correction_lo_b;


    // D = 6
    wire d6_col6_sum0;
    wire d6_col6_carry0;

    wire d6_col7_sum0;
    wire d6_col7_carry0;
    wire d6_col7_sum1;
    wire d6_col7_carry1;

    wire d6_col8_sum0;
    wire d6_col8_carry0;
    wire d6_col8_sum1;
    wire d6_col8_carry1;

    wire d6_col9_sum0;
    wire d6_col9_carry0;

    clm_ha d6_col6_ha0 (.a (pp[0][6]), .b (pp[1][5]), .sum (d6_col6_sum0), .carry (d6_col6_carry0));

    clm_fa d6_col7_fa0 (.a (pp[0][7]), .b (pp[1][6]), .cin (pp[2][5]), .sum (d6_col7_sum0), .cout (d6_col7_carry0));

    // ha to fa, including XOR*2^7
    clm_fa d6_col7_fa1 (.a (pp[3][4]), .b (pp[4][3]), .cin (correction_xor), .sum (d6_col7_sum1), .cout (d6_col7_carry1));

    clm_fa d6_col8_fa0 (.a (pp[1][7]), .b (pp[2][6]), .cin (pp[3][5]), .sum (d6_col8_sum0), .cout (d6_col8_carry0));

    // ha to fa, including AND*2^8
    clm_fa d6_col8_fa1 (.a (pp[4][4]), .b (pp[5][3]), .cin (correction_and), .sum (d6_col8_sum1), .cout (d6_col8_carry1));

    clm_fa d6_col9_fa0 (.a (pp[2][7]), .b (pp[3][6]), .cin (pp[4][5]), .sum (d6_col9_sum0), .cout (d6_col9_carry0));

    // D = 4
    wire d4_col4_sum0;
    wire d4_col4_carry0;

    wire d4_col5_sum0;
    wire d4_col5_carry0;
    wire d4_col5_sum1;
    wire d4_col5_carry1;

    wire d4_col6_sum0;
    wire d4_col6_carry0;
    wire d4_col6_sum1;
    wire d4_col6_carry1;

    wire d4_col7_sum0;
    wire d4_col7_carry0;
    wire d4_col7_sum1;
    wire d4_col7_carry1;

    wire d4_col8_sum0;
    wire d4_col8_carry0;
    wire d4_col8_sum1;
    wire d4_col8_carry1;

    wire d4_col9_sum0;
    wire d4_col9_carry0;
    wire d4_col9_sum1;
    wire d4_col9_carry1;

    wire d4_col10_sum0;
    wire d4_col10_carry0;
    wire d4_col10_sum1;
    wire d4_col10_carry1;

    wire d4_col11_sum0;
    wire d4_col11_carry0;

    clm_ha d4_col4_ha0 (.a (pp[0][4]), .b (pp[1][3]), .sum (d4_col4_sum0), .carry (d4_col4_carry0));

    clm_fa d4_col5_fa0 (.a (pp[0][5]), .b (pp[1][4]), .cin (pp[2][3]), .sum (d4_col5_sum0), .cout (d4_col5_carry0));

    clm_ha d4_col5_ha0 (.a (pp[3][2]), .b (pp[4][1]), .sum (d4_col5_sum1), .carry (d4_col5_carry1));

    clm_fa d4_col6_fa0 (.a (pp[2][4]), .b (pp[3][3]), .cin (pp[4][2]), .sum (d4_col6_sum0), .cout (d4_col6_carry0));

    clm_fa d4_col6_fa1 (.a (pp[5][1]), .b (pp[6][0]), .cin (d6_col6_sum0), .sum (d4_col6_sum1), .cout (d4_col6_carry1));

    clm_fa d4_col7_fa0 (.a (pp[5][2]), .b (pp[6][1]), .cin (pp[7][0]), .sum (d4_col7_sum0), .cout (d4_col7_carry0));

    clm_fa d4_col7_fa1 (.a (d6_col6_carry0), .b (d6_col7_sum0), .cin (d6_col7_sum1), .sum (d4_col7_sum1), .cout (d4_col7_carry1));

    clm_fa d4_col8_fa0 (.a (pp[6][2]), .b (pp[7][1]), .cin (d6_col7_carry0), .sum (d4_col8_sum0), .cout (d4_col8_carry0));

    clm_fa d4_col8_fa1 (.a (d6_col7_carry1), .b (d6_col8_sum0), .cin (d6_col8_sum1), .sum (d4_col8_sum1), .cout (d4_col8_carry1));

    clm_fa d4_col9_fa0 (.a (pp[5][4]), .b (pp[6][3]), .cin (pp[7][2]), .sum (d4_col9_sum0), .cout (d4_col9_carry0));

    clm_fa d4_col9_fa1 (.a (d6_col8_carry0), .b (d6_col8_carry1), .cin (d6_col9_sum0), .sum (d4_col9_sum1), .cout (d4_col9_carry1));

    clm_fa d4_col10_fa0 (.a (pp[3][7]), .b (pp[4][6]), .cin (pp[5][5]), .sum (d4_col10_sum0), .cout (d4_col10_carry0));

    clm_fa d4_col10_fa1 (.a (pp[6][4]), .b (pp[7][3]), .cin (d6_col9_carry0), .sum (d4_col10_sum1), .cout (d4_col10_carry1));

    clm_fa d4_col11_fa0 (.a (pp[4][7]), .b (pp[5][6]), .cin (pp[6][5]), .sum (d4_col11_sum0), .cout (d4_col11_carry0));

    // D = 3
    wire d3_col3_sum0;
    wire d3_col3_carry0;

    wire d3_col4_sum0;
    wire d3_col4_carry0;

    wire d3_col5_sum0;
    wire d3_col5_carry0;

    wire d3_col6_sum0;
    wire d3_col6_carry0;

    wire d3_col7_sum0;
    wire d3_col7_carry0;

    wire d3_col8_sum0;
    wire d3_col8_carry0;

    wire d3_col9_sum0;
    wire d3_col9_carry0;

    wire d3_col10_sum0;
    wire d3_col10_carry0;

    wire d3_col11_sum0;
    wire d3_col11_carry0;

    wire d3_col12_sum0;
    wire d3_col12_carry0;

    clm_ha d3_col3_ha0 (.a (pp[0][3]), .b (pp[1][2]), .sum (d3_col3_sum0), .carry (d3_col3_carry0));

    clm_fa d3_col4_fa0 (.a (pp[2][2]), .b (pp[3][1]), .cin (pp[4][0]), .sum (d3_col4_sum0), .cout (d3_col4_carry0));

    clm_fa d3_col5_fa0 (.a (pp[5][0]), .b (d4_col4_carry0), .cin (d4_col5_sum0), .sum (d3_col5_sum0), .cout (d3_col5_carry0));

    clm_fa d3_col6_fa0 (.a (d4_col5_carry0), .b (d4_col5_carry1), .cin (d4_col6_sum0), .sum (d3_col6_sum0), .cout (d3_col6_carry0));

    clm_fa d3_col7_fa0 (.a (d4_col6_carry0), .b (d4_col6_carry1), .cin (d4_col7_sum0), .sum (d3_col7_sum0), .cout (d3_col7_carry0));

    clm_fa d3_col8_fa0 (.a (d4_col7_carry0), .b (d4_col7_carry1), .cin (d4_col8_sum0), .sum (d3_col8_sum0), .cout (d3_col8_carry0));

    clm_fa d3_col9_fa0 (.a (d4_col8_carry0), .b (d4_col8_carry1), .cin (d4_col9_sum0), .sum (d3_col9_sum0), .cout (d3_col9_carry0));

    clm_fa d3_col10_fa0 (.a (d4_col9_carry0), .b (d4_col9_carry1), .cin (d4_col10_sum0), .sum (d3_col10_sum0), .cout (d3_col10_carry0));

    clm_fa d3_col11_fa0 (.a (pp[7][4]), .b (d4_col10_carry0), .cin (d4_col10_carry1), .sum (d3_col11_sum0), .cout (d3_col11_carry0));

    clm_fa d3_col12_fa0 (.a (pp[5][7]), .b (pp[6][6]), .cin (pp[7][5]), .sum (d3_col12_sum0), .cout (d3_col12_carry0));

    // D = 2
    wire d2_col2_sum0;
    wire d2_col2_carry0;

    wire d2_col3_sum0;
    wire d2_col3_carry0;

    wire d2_col4_sum0;
    wire d2_col4_carry0;

    wire d2_col5_sum0;
    wire d2_col5_carry0;

    wire d2_col6_sum0;
    wire d2_col6_carry0;

    wire d2_col7_sum0;
    wire d2_col7_carry0;

    wire d2_col8_sum0;
    wire d2_col8_carry0;

    wire d2_col9_sum0;
    wire d2_col9_carry0;

    wire d2_col10_sum0;
    wire d2_col10_carry0;

    wire d2_col11_sum0;
    wire d2_col11_carry0;

    wire d2_col12_sum0;
    wire d2_col12_carry0;

    wire d2_col13_sum0;
    wire d2_col13_carry0;

    clm_ha d2_col2_ha0 (.a (pp[0][2]), .b (pp[1][1]), .sum (d2_col2_sum0), .carry (d2_col2_carry0));

    clm_fa d2_col3_fa0 (.a (pp[2][1]), .b (pp[3][0]), .cin (d3_col3_sum0), .sum (d2_col3_sum0), .cout (d2_col3_carry0));

    clm_fa d2_col4_fa0 (.a (d4_col4_sum0), .b (d3_col3_carry0), .cin (d3_col4_sum0), .sum (d2_col4_sum0), .cout (d2_col4_carry0));

    clm_fa d2_col5_fa0 (.a (d4_col5_sum1), .b (d3_col4_carry0), .cin (d3_col5_sum0), .sum (d2_col5_sum0), .cout (d2_col5_carry0));

    clm_fa d2_col6_fa0 (.a (d4_col6_sum1), .b (d3_col5_carry0), .cin (d3_col6_sum0), .sum (d2_col6_sum0), .cout (d2_col6_carry0));

    clm_fa d2_col7_fa0 (.a (d4_col7_sum1), .b (d3_col6_carry0), .cin (d3_col7_sum0), .sum (d2_col7_sum0), .cout (d2_col7_carry0));

    clm_fa d2_col8_fa0 (.a (d4_col8_sum1), .b (d3_col7_carry0), .cin (d3_col8_sum0), .sum (d2_col8_sum0), .cout (d2_col8_carry0));

    clm_fa d2_col9_fa0 (.a (d4_col9_sum1), .b (d3_col8_carry0), .cin (d3_col9_sum0), .sum (d2_col9_sum0), .cout (d2_col9_carry0));

    clm_fa d2_col10_fa0 (.a (d4_col10_sum1), .b (d3_col9_carry0), .cin (d3_col10_sum0), .sum (d2_col10_sum0), .cout (d2_col10_carry0));

    clm_fa d2_col11_fa0 (.a (d4_col11_sum0), .b (d3_col10_carry0), .cin (d3_col11_sum0), .sum (d2_col11_sum0), .cout (d2_col11_carry0));

    clm_fa d2_col12_fa0 (.a (d4_col11_carry0), .b (d3_col11_carry0), .cin (d3_col12_sum0), .sum (d2_col12_sum0), .cout (d2_col12_carry0));

    clm_fa d2_col13_fa0 (.a (pp[6][7]), .b (pp[7][6]), .cin (d3_col12_carry0), .sum (d2_col13_sum0), .cout (d2_col13_carry0));


    // RCA stage for final 2 rows
    // cascaded full adders in a sequential chain
    wire rca_carry1;
    wire rca_carry2;
    wire rca_carry3;
    wire rca_carry4;
    wire rca_carry5;
    wire rca_carry6;
    wire rca_carry7;
    wire rca_carry8;
    wire rca_carry9;
    wire rca_carry10;
    wire rca_carry11;
    wire rca_carry12;
    wire rca_carry13;
    wire rca_carry14;

    wire rca_sum14;
    wire rca_borrow15;

    assign product[0] = pp[0][0];

    clm_ha rca_col1_ha0 (.a (pp[0][1]), .b (pp[1][0]), .sum (product[1]), .carry (rca_carry1));

    clm_fa rca_col2_fa0 (.a (pp[2][0]), .b (d2_col2_sum0), .cin (rca_carry1), .sum (product[2]), .cout (rca_carry2));

    clm_fa rca_col3_fa0 (.a (d2_col2_carry0), .b (d2_col3_sum0), .cin (rca_carry2), .sum (product[3]), .cout (rca_carry3));

    clm_fa rca_col4_fa0 (.a (d2_col3_carry0), .b (d2_col4_sum0), .cin (rca_carry3), .sum (product[4]), .cout (rca_carry4));

    clm_fa rca_col5_fa0 (.a (d2_col4_carry0), .b (d2_col5_sum0), .cin (rca_carry4), .sum (product[5]), .cout (rca_carry5));

    clm_fa rca_col6_fa0 (.a (d2_col5_carry0), .b (d2_col6_sum0), .cin (rca_carry5), .sum (product[6]), .cout (rca_carry6));

    clm_fa rca_col7_fa0 (.a (d2_col6_carry0), .b (d2_col7_sum0), .cin (rca_carry6), .sum (product[7]), .cout (rca_carry7));

    clm_fa rca_col8_fa0 (.a (d2_col7_carry0), .b (d2_col8_sum0), .cin (rca_carry7), .sum (product[8]), .cout (rca_carry8));

    clm_fa rca_col9_fa0 (.a (d2_col8_carry0), .b (d2_col9_sum0), .cin (rca_carry8), .sum (product[9]), .cout (rca_carry9));

    clm_fa rca_col10_fa0 (.a (d2_col9_carry0), .b (d2_col10_sum0), .cin (rca_carry9), .sum (product[10]), .cout (rca_carry10));

    clm_fa rca_col11_fa0 (.a (d2_col10_carry0), .b (d2_col11_sum0), .cin (rca_carry10), .sum (product[11]), .cout (rca_carry11));

    clm_fa rca_col12_fa0 (.a (d2_col11_carry0), .b (d2_col12_sum0), .cin (rca_carry11), .sum (product[12]), .cout (rca_carry12));

    clm_fa rca_col13_fa0 (.a (d2_col12_carry0), .b (d2_col13_sum0), .cin (rca_carry12), .sum (product[13]), .cout (rca_carry13));

    clm_fa rca_col14_fa0 (.a (pp[7][7]), .b (d2_col13_carry0), .cin (rca_carry13), .sum (rca_sum14), .cout (rca_carry14));


    // upper half correction
    assign product[14] = rca_sum14 ^ correction_xor;
    assign rca_borrow15 = (~rca_sum14) & correction_xor;
    assign product[15] = rca_carry14 ^ rca_borrow15 ^ correction_and;

    // correction_hi_a, correction_hi_b and correction_top are absorbed into
    // the identity above and have no separate gate of their own
    wire correction_unused;
    assign correction_unused = &{correction_hi_a, correction_hi_b, correction_top, 1'b0};

endmodule