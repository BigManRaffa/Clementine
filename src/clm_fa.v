module clm_fa (
    input wire a,
    input wire b,
    input wire cin,
    output wire sum,
    output wire cout
);

wire axb;

    assign axb = a ^ b;
    assign sum = axb ^ cin;
    assign cout = (a & b) | (axb & cin);

endmodule
