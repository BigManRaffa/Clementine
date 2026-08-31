set SKY $env(PDK_ROOT)/sky130A/libs.ref/sky130_fd_sc_hd

set_db init_lib_search_path $SKY/lib
set_db library sky130_fd_sc_hd__ss_100C_1v60.lib
set_db lef_library [list $SKY/techlef/sky130_fd_sc_hd__nom.tlef $SKY/lef/sky130_fd_sc_hd.lef $SKY/lef/sky130_ef_sc_hd.lef]

set_db hdl_search_path ../src

read_hdl -language sv {
  clm_ha.v clm_fa.v clm_mult_bw.v clm_alu.v
  clm_regfile.v clm_mask_stack.v clm_decoder.v
  clm_fetch_seq.v clm_lane.v clm_spi_host.v
  tt_um_bigmanraffa_clm.v
}

elaborate tt_um_bigmanraffa_clm
read_sdc clementine_asic.sdc

set_db syn_generic_effort medium
syn_generic
syn_map
syn_opt

report_qor    > reports/qor.rpt
report_timing > reports/timing.rpt
report_area   > reports/area.rpt
report_gates  > reports/gates.rpt
report_power  > reports/power.rpt

write_hdl > clementine_netlist.v
write_sdc > clementine_syn.sdc