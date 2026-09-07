create_clock -name clk -period 25.0 [get_ports clk]
set_clock_uncertainty 0.25 [get_clocks clk]
set_clock_transition 0.1 [get_clocks clk]

set_false_path -from [get_ports rst_n]
set_false_path -from [get_ports ena]

set INPUTS [remove_from_collection [all_inputs] [get_ports {clk rst_n ena}]]

set_input_delay  5.0 -clock clk $INPUTS
set_output_delay 5.0 -clock clk [all_outputs]

set_input_transition 0.1 $INPUTS
set_load 0.05 [all_outputs]