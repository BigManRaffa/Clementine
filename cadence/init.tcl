set SKY $env(PDK_ROOT)/sky130A/libs.ref/sky130_fd_sc_hd

set init_verilog clementine_netlist.v
set init_top_cell tt_um_bigmanraffa_clm
set init_lef_file [list $SKY/techlef/sky130_fd_sc_hd__nom.tlef $SKY/lef/sky130_fd_sc_hd.lef $SKY/lef/sky130_ef_sc_hd.lef]
set init_mmmc_file mmmc.tcl
set init_pwr_net VPWR
set init_gnd_net VGND

init_design