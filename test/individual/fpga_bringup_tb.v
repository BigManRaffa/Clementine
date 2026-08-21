`timescale 1ns/1ps
`default_nettype none

module clementine_fpga_bringup_tb;
    localparam real REF_HALF_NS = 18.5185185185; // 27 MHz
    localparam integer SPI_HALF_1MHZ = 500;
    localparam integer SPI_HALF_2MHZ = 250;
    localparam integer SPI_HALF_3MHZ = 167;
    localparam integer SPI_HALF_4MHZ = 125;
    localparam integer SPI_HALF_5MHZ = 100;
    localparam [2:0] CMD_EXEC = 3'b000;
    localparam [2:0] CMD_GO = 3'b001;
    localparam [2:0] CMD_STATUS = 3'b010;
    localparam [2:0] CMD_BUFFER = 3'b011;
    localparam [2:0] CMD_ACC0 = 3'b100;
    localparam [2:0] CMD_ACC1 = 3'b101;
    localparam [2:0] CMD_ACC2 = 3'b110;
    localparam [2:0] CMD_ACC3 = 3'b111;
    localparam [3:0] OP_ADD = 4'h1;
    localparam [3:0] OP_SUB = 4'h2;
    localparam [3:0] OP_MAC = 4'h7;
    localparam [3:0] OP_CLRACC = 4'h8;
    localparam [3:0] OP_LDI = 4'h9;
    localparam [3:0] OP_MOV = 4'hA;
    localparam [3:0] OP_LDAC = 4'hD;
    localparam [3:0] OP_HALT = 4'hF;
    reg clk;
    reg rst_n;
    reg sclk;
    reg cs_n;
    reg mosi;
    reg [2:0] ui_in;
    wire miso;
    wire [3:0] led;
    fpga_top dut (
        .clk (clk),
        .rst_n (rst_n),
        .sclk (sclk),
        .cs_n (cs_n),
        .mosi (mosi),
        .miso (miso),
        .ui_in (ui_in),
        .led (led)
    );
    integer checks;
    integer failures;
    integer test_number;
    integer miso_high_phase_changes;
    integer miso_unknown_samples;
    reg monitor_miso_high_phase;
    reg [15:0] rx_word;
    reg [15:0] acc_word;
    reg [31:0] rx_long;
    time pll_t0;
    time pll_t1;
    real pll_period_ns;
    real pll_freq_mhz;
    initial begin
        clk = 1'b0;
        forever #(REF_HALF_NS) clk = ~clk;
    end
`ifndef CLEMENTINE_TB_NO_DUMP
    initial begin
        $dumpfile("clementine_fpga_bringup_tb.vcd");
        $dumpvars(0, clementine_fpga_bringup_tb);
    end
`endif
    // ISA encoders
    function [15:0] enc_rrr;
        input [3:0] op;
        input [2:0] rd;
        input [2:0] rs;
        input [2:0] rt;
        begin
            enc_rrr = {op, rd, rs, rt, 3'b000};
        end
    endfunction
    function [15:0] enc_add;
        input [2:0] rd;
        input [2:0] rs;
        input [2:0] rt;
        begin
            enc_add = enc_rrr(OP_ADD, rd, rs, rt);
        end
    endfunction
    function [15:0] enc_sub;
        input [2:0] rd;
        input [2:0] rs;
        input [2:0] rt;
        begin
            enc_sub = enc_rrr(OP_SUB, rd, rs, rt);
        end
    endfunction
    function [15:0] enc_mac;
        input [2:0] rs;
        input [2:0] rt;
        begin
            enc_mac = {OP_MAC, 3'b000, rs, rt, 3'b000};
        end
    endfunction
    function [15:0] enc_clracc;
        begin
            enc_clracc = {OP_CLRACC, 12'h000};
        end
    endfunction
    function [15:0] enc_ldi;
        input [2:0] rd;
        input [7:0] imm;
        begin
            enc_ldi = {OP_LDI, rd, imm, 1'b0};
        end
    endfunction
    function [15:0] enc_mov_host;
        input [2:0] rd;
        begin
            enc_mov_host = {OP_LDI, rd, 8'h00, 1'b1};
        end
    endfunction
    function [15:0] enc_laneid;
        input [2:0] rd;
        begin
            enc_laneid = {OP_MOV, rd, 8'h00, 1'b1};
        end
    endfunction
    function [15:0] enc_ldac;
        input [2:0] rs;
        input high;
        begin
            enc_ldac = {OP_LDAC, 3'b000, rs, 2'b00, high, 3'b000};
        end
    endfunction
    function [15:0] enc_halt;
        begin
            enc_halt = {OP_HALT, 12'h000};
        end
    endfunction
    task banner;
        input [8*80-1:0] name;
        begin
            test_number = test_number + 1;
            $display("");
            $display("[%0d] %0s", test_number, name);
            $display("----------------------------------------------------------------");
        end
    endtask
    task check_true;
        input condition;
        input [8*120-1:0] message;
        begin
            checks = checks + 1;
            if (condition === 1'b1) begin
                $display("  PASS: %0s", message);
            end
            else begin
                failures = failures + 1;
                $display("  FAIL: %0s", message);
            end
        end
    endtask
    task check16;
        input [15:0] got;
        input [15:0] expected;
        input [8*100-1:0] message;
        begin
            checks = checks + 1;
            if (got === expected) begin
                $display("  PASS: %0s = 0x%04h", message, got);
            end
            else begin
                failures = failures + 1;
                $display("  FAIL: %0s expected 0x%04h got 0x%04h",
                         message, expected, got);
            end
        end
    endtask
    task spi_idle;
        begin
            cs_n = 1'b1;
            sclk = 1'b0;
            mosi = 1'b0;
            ui_in = 3'b000;
            repeat (8) @(posedge dut.core_clk);
        end
    endtask
    task spi_frame16;
        input [2:0] command;
        input [15:0] tx;
        input integer half_ns;
        input integer phase_ns;
        output [15:0] rx;
        integer i;
        reg [15:0] tmp;
        begin
            tmp = 16'h0000;
            ui_in = command;
            sclk = 1'b0;
            mosi = 1'b0;
            cs_n = 1'b1;
            if (phase_ns > 0)
                #(phase_ns);
            repeat (3) @(posedge dut.core_clk);
            cs_n = 1'b0;
            repeat (4) @(posedge dut.core_clk);
            for (i = 15; i >= 0; i = i - 1) begin
                mosi = tx[i];
                #(half_ns);
                sclk = 1'b1;
                #(half_ns/5);
                if ((miso !== 1'b0) && (miso !== 1'b1)) begin
                    miso_unknown_samples = miso_unknown_samples + 1;
                    tmp[i] = 1'b0;
                end
                else begin
                    tmp[i] = miso;
                end
                #(half_ns - (half_ns/5));
                sclk = 1'b0;
            end
            #(half_ns);
            cs_n = 1'b1;
            mosi = 1'b0;
            repeat (5) @(posedge dut.core_clk);
            rx = tmp;
        end
    endtask
    task spi_frame16_change_command;
        input [2:0] command;
        input [2:0] replacement_command;
        input [15:0] tx;
        input integer half_ns;
        output [15:0] rx;
        integer i;
        reg [15:0] tmp;
        begin
            tmp = 16'h0000;
            ui_in = command;
            sclk = 1'b0;
            mosi = 1'b0;
            cs_n = 1'b1;
            repeat (3) @(posedge dut.core_clk);
            cs_n = 1'b0;
            repeat (4) @(posedge dut.core_clk);
            // deliberately disturb sideband command after acquisition
            ui_in = replacement_command;
            repeat (1) @(posedge dut.core_clk);
            for (i = 15; i >= 0; i = i - 1) begin
                mosi = tx[i];
                #(half_ns);
                sclk = 1'b1;
                #(half_ns/5);
                tmp[i] = miso;
                #(half_ns - (half_ns/5));
                sclk = 1'b0;
            end
            #(half_ns);
            cs_n = 1'b1;
            mosi = 1'b0;
            repeat (5) @(posedge dut.core_clk);
            rx = tmp;
        end
    endtask
    task spi_buffer32;
        input [31:0] tx;
        input integer half_ns;
        integer i;
        begin
            ui_in = CMD_BUFFER;
            sclk = 1'b0;
            mosi = 1'b0;
            cs_n = 1'b1;
            repeat (3) @(posedge dut.core_clk);
            cs_n = 1'b0;
            repeat (4) @(posedge dut.core_clk);
            for (i = 31; i >= 0; i = i - 1) begin
                mosi = tx[i];
                #(half_ns);
                sclk = 1'b1;
                #(half_ns);
                sclk = 1'b0;
            end
            #(half_ns);
            cs_n = 1'b1;
            mosi = 1'b0;
            repeat (5) @(posedge dut.core_clk);
        end
    endtask
    task exec_word;
        input [15:0] word;
        begin
            spi_frame16(CMD_EXEC, word, SPI_HALF_5MHZ, 0, rx_word);
        end
    endtask
    task go_core;
        begin
            spi_frame16(CMD_GO, 16'h0000, SPI_HALF_5MHZ, 0, rx_word);
        end
    endtask
    task read_status;
        output [15:0] status;
        begin
            spi_frame16(CMD_STATUS, 16'h0000, SPI_HALF_5MHZ, 0, status);
        end
    endtask
    task read_acc;
        input [1:0] lane;
        output [15:0] value;
        reg [2:0] command;
        begin
            command = CMD_ACC0 + lane;
            spi_frame16(command, 16'h0000, SPI_HALF_5MHZ, 0, value);
        end
    endtask
    task wait_for_done;
        integer poll;
        reg [15:0] status;
        begin
            poll = 0;
            status = 16'h0000;
            while ((poll < 128) && (status[0] !== 1'b1)) begin
                read_status(status);
                poll = poll + 1;
            end
            check_true(status[0] === 1'b1, "kernel eventually returned to DONE=1");
        end
    endtask
    task hard_reset;
        begin
            cs_n = 1'b1;
            sclk = 1'b0;
            mosi = 1'b0;
            ui_in = 3'b000;
            // fpga_top uses core_rst_n = por_shift[3] & ~rst_n
            rst_n = 1'b1;
            repeat (12) @(posedge dut.core_clk);
            rst_n = 1'b0;
            repeat (12) @(posedge dut.core_clk);
            spi_idle;
        end
    endtask
    always @(miso) begin
        if (monitor_miso_high_phase &&
            (cs_n === 1'b0) &&
            (sclk === 1'b1)) begin
            miso_high_phase_changes = miso_high_phase_changes + 1;
            $display("  MISO TIMING WARNING at %0t: changed while SCLK high", $time);
        end
    end
    task test_pll_frequency;
        integer i;
        begin
            banner("PLL frequency and duty sanity");
            // ignore startup and then average many core periods
            repeat (20) @(posedge dut.core_clk);
            @(posedge dut.core_clk);
            pll_t0 = $time;
            for (i = 0; i < 64; i = i + 1)
                @(posedge dut.core_clk);
            pll_t1 = $time;
            pll_period_ns = (pll_t1 - pll_t0) / 64.0;
            pll_freq_mhz = 1000.0 / pll_period_ns;
            $display("  measured core period = %0.3f ns", pll_period_ns);
            $display("  measured core clock  = %0.3f MHz", pll_freq_mhz);
            check_true((pll_freq_mhz > 38.5) && (pll_freq_mhz < 42.0),
                       "PLL output is near intended 40.5 MHz");
        end
    endtask
    task test_status_and_reset;
        reg [15:0] status;
        begin
            banner("Reset release and STATUS pin path");
            hard_reset;
            read_status(status);
            check16(status, 16'h0001, "STATUS after reset");
            check_true(led !== 4'bxxxx, "LED wrapper outputs are resolved");
        end
    endtask
    task test_spi_rate_phase_sweep;
        integer rate_index;
        integer phase_index;
        integer half_ns;
        integer phase_ns;
        reg [15:0] status;
        begin
            banner("SPI 1-5 MHz phase sweep");
            hard_reset;
            for (rate_index = 1; rate_index <= 5; rate_index = rate_index + 1) begin
                case (rate_index)
                    1: half_ns = SPI_HALF_1MHZ;
                    2: half_ns = SPI_HALF_2MHZ;
                    3: half_ns = SPI_HALF_3MHZ;
                    4: half_ns = SPI_HALF_4MHZ;
                    default: half_ns = SPI_HALF_5MHZ;
                endcase
                for (phase_index = 0; phase_index < 4; phase_index = phase_index + 1) begin
                    case (phase_index)
                        0: phase_ns = 0;
                        1: phase_ns = 3;
                        2: phase_ns = 11;
                        default: phase_ns = 17;
                    endcase
                    spi_frame16(CMD_STATUS, 16'hA55A,
                                half_ns, phase_ns, status);
                    if (status !== 16'h0001) begin
                        failures = failures + 1;
                        $display("  FAIL: %0d MHz phase %0d ns status=0x%04h",
                                 rate_index, phase_ns, status);
                    end
                    checks = checks + 1;
                end
            end
        end
    endtask
    task test_command_freeze;
        reg [15:0] status;
        begin
            banner("Sideband command freezes for active transaction");
            hard_reset;
            spi_frame16_change_command(CMD_STATUS, CMD_ACC3,
                                       16'h0000, SPI_HALF_5MHZ, status);
            check16(status, 16'h0001,
                    "STATUS transaction survives command pin change");
        end
    endtask
    task test_miso_high_phase_stability;
        reg [15:0] status;
        integer before_count;
        begin
            banner("Mode-0 MISO stability through SCLK high phase");
            hard_reset;
            before_count = miso_high_phase_changes;
            monitor_miso_high_phase = 1'b1;
            spi_frame16(CMD_STATUS, 16'h0000,
                        SPI_HALF_5MHZ, 0, status);
            monitor_miso_high_phase = 1'b0;
            check16(status, 16'h0001, "STATUS read during MISO timing monitor");
            check_true(miso_high_phase_changes == before_count,
                       "MISO did not change while physical SCLK was high");
        end
    endtask
    task test_laneid_accumulators;
        integer lane;
        begin
            banner("LANEID -> LDAC -> four accumulator reads");
            hard_reset;
            exec_word(enc_laneid(3'd1));
            exec_word(enc_clracc());
            exec_word(enc_ldac(3'd1, 1'b0));
            exec_word(enc_halt());
            go_core;
            wait_for_done;
            for (lane = 0; lane < 4; lane = lane + 1) begin
                read_acc(lane[1:0], acc_word);
                check16(acc_word, lane[15:0], "LANEID accumulator result");
            end
        end
    endtask
    task test_ldi_add;
        integer lane;
        begin
            banner("Broadcast LDI + ADD architectural smoke test");
            hard_reset;
            exec_word(enc_ldi(3'd1, 8'h12));
            exec_word(enc_ldi(3'd2, 8'h34));
            exec_word(enc_add(3'd3, 3'd1, 3'd2));
            exec_word(enc_clracc());
            exec_word(enc_ldac(3'd3, 1'b0));
            exec_word(enc_halt());
            go_core;
            wait_for_done;
            for (lane = 0; lane < 4; lane = lane + 1) begin
                read_acc(lane[1:0], acc_word);
                check16(acc_word, 16'h0046, "0x12 + 0x34 result");
            end
        end
    endtask
    task test_buffer_mov_host;
        reg [7:0] expected [0:3];
        integer lane;
        begin
            banner("BUFFER lane ordering and MOV_HOST staging");
            hard_reset;
            expected[0] = 8'h11;
            expected[1] = 8'h80;
            expected[2] = 8'hFE;
            expected[3] = 8'h7F;
            spi_buffer32(32'h1180FE7F, SPI_HALF_5MHZ);
            exec_word(enc_mov_host(3'd1));
            exec_word(enc_clracc());
            exec_word(enc_ldac(3'd1, 1'b0));
            exec_word(enc_halt());
            go_core;
            wait_for_done;
            for (lane = 0; lane < 4; lane = lane + 1) begin
                read_acc(lane[1:0], acc_word);
                check16(acc_word, {8'h00, expected[lane]},
                        "lane-local MOV_HOST byte");
            end
        end
    endtask
    task test_buffer_full_overwrite;
        reg [7:0] expected [0:3];
        integer lane;
        begin
            banner("Second BUFFER fully overwrites old staging data");
            hard_reset;
            spi_buffer32(32'h12345678, SPI_HALF_5MHZ);
            spi_buffer32(32'h80FF007F, SPI_HALF_5MHZ);
            expected[0] = 8'h80;
            expected[1] = 8'hFF;
            expected[2] = 8'h00;
            expected[3] = 8'h7F;
            exec_word(enc_mov_host(3'd2));
            exec_word(enc_clracc());
            exec_word(enc_ldac(3'd2, 1'b0));
            exec_word(enc_halt());
            go_core;
            wait_for_done;
            for (lane = 0; lane < 4; lane = lane + 1) begin
                read_acc(lane[1:0], acc_word);
                check16(acc_word, {8'h00, expected[lane]},
                        "second BUFFER overwrite result");
            end
        end
    endtask
    task test_same_bank_sub;
        integer lane;
        begin
            banner("Same-bank SUB result through capture/replay path");
            hard_reset;
            // R1 and R3 are both odd-bank sources
            exec_word(enc_ldi(3'd1, 8'h44));
            exec_word(enc_ldi(3'd3, 8'h11));
            exec_word(enc_sub(3'd5, 3'd1, 3'd3));
            exec_word(enc_clracc());
            exec_word(enc_ldac(3'd5, 1'b0));
            exec_word(enc_halt());
            go_core;
            wait_for_done;
            for (lane = 0; lane < 4; lane = lane + 1) begin
                read_acc(lane[1:0], acc_word);
                check16(acc_word, 16'h0033,
                        "same-bank SUB architectural result");
            end
        end
    endtask
    task test_bare_go_rerun;
        reg [15:0] first;
        reg [15:0] second;
        begin
            banner("Static instruction image survives bare GO rerun");
            hard_reset;
            exec_word(enc_ldi(3'd1, 8'h5A));
            exec_word(enc_clracc());
            exec_word(enc_ldac(3'd1, 1'b0));
            exec_word(enc_halt());
            go_core;
            wait_for_done;
            read_acc(2'd0, first);
            // No instruction upload here.
            go_core;
            wait_for_done;
            read_acc(2'd0, second);
            check16(first, 16'h005A, "first execution result");
            check16(second, 16'h005A, "bare-GO rerun result");
        end
    endtask
    task test_hardware_mirror_mac_once;
        begin
            banner("Exact FPGA MAC bring-up sequence runs once");
            hard_reset;
            // literal sequence used during hardware bring-up
            exec_word(16'h8000);
            exec_word(16'h9202);
            exec_word(16'h9402);
            exec_word(16'h7050);
            exec_word(16'hF000);
            go_core;
            wait_for_done;
            read_acc(2'd0, acc_word);
            check16(acc_word, 16'h0001,
                    "single MAC sequence accumulator must equal one");
        end
    endtask
    task test_go_payload_ignored;
        reg [15:0] dummy_rx;
        begin
            banner("GO data payload is architecturally ignored");
            hard_reset;
            exec_word(enc_ldi(3'd1, 8'h2A));
            exec_word(enc_clracc());
            exec_word(enc_ldac(3'd1, 1'b0));
            exec_word(enc_halt());
            spi_frame16(CMD_GO, 16'hA55A,
                        SPI_HALF_5MHZ, 0, dummy_rx);
            wait_for_done;
            read_acc(2'd0, acc_word);
            check16(acc_word, 16'h002A,
                    "GO dummy bits do not alter execution");
        end
    endtask
    task test_reset_mid_spi_frame_recovery;
        integer bit_index;
        reg [15:0] status;
        reg [15:0] recovered_acc;
        reg [15:0] interrupted_word;
        begin
            banner("Reset in the middle of an SPI frame recovers cleanly");
            hard_reset;

            exec_word(enc_ldi(3'd1, 8'h66));
            exec_word(enc_clracc());
            exec_word(enc_ldac(3'd1, 1'b0));
            exec_word(enc_halt());

            go_core;
            wait_for_done;

            read_acc(2'd0, recovered_acc);
            check16(
                recovered_acc,
                16'h0066,
                "baseline kernel before interrupted transaction"
            );

            interrupted_word = 16'hFFFF;

            ui_in = CMD_EXEC;
            sclk  = 1'b0;
            mosi  = 1'b0;
            cs_n  = 1'b1;

            repeat (3) @(posedge dut.core_clk);

            cs_n = 1'b0;
            repeat (4) @(posedge dut.core_clk);

            for (bit_index = 15; bit_index >= 8; bit_index = bit_index - 1) begin
                mosi = interrupted_word[bit_index];

                #(SPI_HALF_5MHZ);
                sclk = 1'b1;

                #(SPI_HALF_5MHZ);
                sclk = 1'b0;
            end

            rst_n = 1'b1;

            repeat (4) @(posedge dut.core_clk);

            check_true(
                dut.core_rst_n === 1'b0,
                "mid-frame board reset asserts core_rst_n low"
            );

            cs_n  = 1'b1;
            sclk  = 1'b0;
            mosi  = 1'b0;
            ui_in = 3'b000;

            repeat (6) @(posedge dut.core_clk);

            // release reset
            rst_n = 1'b0;

            repeat (8) @(posedge dut.core_clk);

            check_true(
                dut.core_rst_n === 1'b1,
                "core reset releases after interrupted-frame recovery"
            );

            // SPI must become immediately usable again
            read_status(status);

            check16(
                status,
                16'h0001,
                "STATUS is sane after reset interrupted an SPI frame"
            );

            // the half-received EXEC must NOT have committed
            // The original instruction image should still run unchanged
            go_core;
            wait_for_done;

            read_acc(2'd0, recovered_acc);

            check16(
                recovered_acc,
                16'h0066,
                "abandoned EXEC did not corrupt slot 0 or create a phantom write"
            );
        end
    endtask
    task test_short_spi_soak;
        integer i;
        reg [15:0] status;
        begin
            banner("Deterministic SPI pin soak");
            hard_reset;
            for (i = 0; i < 24; i = i + 1) begin
                spi_frame16(CMD_STATUS,
                            (16'h1357 ^ (i * 16'h0101)),
                            SPI_HALF_5MHZ,
                            (i % 5),
                            status);
                if (status !== 16'h0001) begin
                    checks = checks + 1;
                    failures = failures + 1;
                    $display("  FAIL: soak iteration %0d status=0x%04h", i, status);
                end
                else begin
                    checks = checks + 1;
                end
            end
            check_true(miso_unknown_samples == 0,
                       "no unknown MISO samples during supported SPI traffic");
        end
    endtask
    initial begin
        checks = 0;
        failures = 0;
        test_number = 0;
        miso_high_phase_changes = 0;
        miso_unknown_samples = 0;
        monitor_miso_high_phase = 1'b0;
        rst_n = 1'b1;
        sclk = 1'b0;
        cs_n = 1'b1;
        mosi = 1'b0;
        ui_in = 3'b000;
        $display("");
        $display("============================================================");
        $display(" Clementine FPGA bring-up regression");
        $display("============================================================");
        // give the PLL model time to become meaningful
        #1000;
        test_pll_frequency;
        test_status_and_reset;
        test_spi_rate_phase_sweep;
        test_command_freeze;
        test_miso_high_phase_stability;
        test_laneid_accumulators;
        test_ldi_add;
        test_buffer_mov_host;
        test_buffer_full_overwrite;
        test_same_bank_sub;
        test_bare_go_rerun;
        test_hardware_mirror_mac_once;
        test_go_payload_ignored;
        test_reset_mid_spi_frame_recovery;
        test_short_spi_soak;
        $display("");
        $display("============================================================");
        $display(" Clementine FPGA regression summary");
        $display("============================================================");
        $display(" tests      : %0d", test_number);
        $display(" checks     : %0d", checks);
        $display(" failures   : %0d", failures);
        $display(" MISO high-phase changes : %0d", miso_high_phase_changes);
        $display(" unknown MISO samples    : %0d", miso_unknown_samples);
        $display("============================================================");
        if (failures == 0) begin
            $display(" ALL FPGA BRING-UP TESTS PASSED");
        end
        else begin
            $display(" FPGA BRING-UP TESTS FAILED");
            $fatal(1);
        end
        #100;
        $finish;
    end
endmodule

`ifdef CLEMENTINE_TB_STUB_RPLL
module rPLL (
    output reg CLKOUT,
    output wire LOCK,
    output wire CLKOUTP,
    output wire CLKOUTD,
    output wire CLKOUTD3,
    input wire RESET,
    input wire RESET_P,
    input wire CLKIN,
    input wire CLKFB,
    input wire [5:0] FBDSEL,
    input wire [5:0] IDSEL,
    input wire [5:0] ODSEL,
    input wire [3:0] PSDA,
    input wire [3:0] DUTYDA,
    input wire [3:0] FDLY
);
    parameter FCLKIN = "27";
    parameter DYN_IDIV_SEL = "false";
    parameter IDIV_SEL = 1;
    parameter DYN_FBDIV_SEL = "false";
    parameter FBDIV_SEL = 2;
    parameter DYN_ODIV_SEL = "false";
    parameter ODIV_SEL = 16;
    parameter PSDA_SEL = "0000";
    parameter DYN_DA_EN = "true";
    parameter DUTYDA_SEL = "1000";
    parameter CLKOUT_FT_DIR = 1'b1;
    parameter CLKOUTP_FT_DIR = 1'b1;
    parameter CLKOUT_DLY_STEP = 0;
    parameter CLKOUTP_DLY_STEP = 0;
    parameter CLKFB_SEL = "internal";
    parameter CLKOUT_BYPASS = "false";
    parameter CLKOUTP_BYPASS = "false";
    parameter CLKOUTD_BYPASS = "false";
    parameter DYN_SDIV_SEL = 2;
    parameter CLKOUTD_SRC = "CLKOUT";
    parameter CLKOUTD3_SRC = "CLKOUT";
    parameter DEVICE = "GW2AR-18C";
    assign LOCK = 1'b1;
    assign CLKOUTP = ~CLKOUT;
    assign CLKOUTD = CLKOUT;
    assign CLKOUTD3 = CLKOUT;
    initial begin
        CLKOUT = 1'b0;
        forever #12.345679 CLKOUT = ~CLKOUT; // 40.5 MHz
    end
endmodule
`endif
`default_nettype wire
