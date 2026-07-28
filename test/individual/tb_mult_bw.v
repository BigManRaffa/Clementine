`timescale 1ns/1ps

module tb_mult_bw;

    reg  signed [7:0]  a;
    reg  signed [7:0]  b;
    wire signed [15:0] product;

    reg signed [15:0] expected;

    integer a_loop;
    integer b_loop;
    integer error_count;

    clm_mult_bw dut (
        .a(a),
        .b(b),
        .product(product)
    );

    initial begin
        error_count = 0;

        for (a_loop = -128; a_loop <= 127; a_loop = a_loop + 1) begin
            for (b_loop = -128; b_loop <= 127; b_loop = b_loop + 1) begin
                a = a_loop;
                b = b_loop;
                expected = a_loop * b_loop;

                #1;

                if (product !== expected) begin
                    if (error_count < 10) begin
                        $display(
                            "FAIL a=%0d b=%0d got=%0d want=%0d",
                            a, b, product, expected
                        );
                    end

                    error_count = error_count + 1;
                end
            end
        end

        if (error_count == 0)
            $display("We are good");
        else
            $display("Fail, %0d of 65536", error_count);

        $finish;
    end

endmodule