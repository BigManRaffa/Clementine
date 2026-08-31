floorPlan -site unithd -d 334 216 10 10 10 10

globalNetConnect VPWR -type pgpin -pin VPWR -inst * -override
globalNetConnect VGND -type pgpin -pin VGND -inst * -override

addStripe -nets {VPWR VGND} -layer met4 -direction vertical -width 1.6 -spacing 1.7 -set_to_set_distance 38.87
sroute -connect {corePin} -nets {VPWR VGND}

addWellTap -cell sky130_fd_sc_hd__tapvpwrvgnd_1 -cellInterval 25 -prefix TAP

setPinAssignMode -pinEditInBatch true
editPin -pin {clk rst_n ena} -side LEFT -layer 3 -spreadType SIDE
editPin -pin ui_in* -side BOTTOM -layer 2 -spreadType SIDE
editPin -pin uo_out* -side TOP -layer 2 -spreadType SIDE
editPin -pin uio_* -side RIGHT -layer 3 -spreadType SIDE
setPinAssignMode -pinEditInBatch false

place_opt_design

set_ccopt_property target_max_trans 0.25
create_ccopt_clock_tree_spec
ccopt_design

set diode_cell [lindex [dbGet head.libCells.name *diode*] 0]
setNanoRouteMode -routeWithTimingDriven true
setNanoRouteMode -drouteFixAntenna true
setNanoRouteMode -routeInsertAntennaDiode true
setNanoRouteMode -routeAntennaCellName $diode_cell
routeDesign

setAnalysisMode -analysisType onChipVariation
setOptMode -holdTargetSlack 0.02
optDesign -postRoute -setup
optDesign -postRoute -hold
optDesign -postRoute -drv

setFillerMode -add_fillers_with_drc false
addFiller -cell {sky130_fd_sc_hd__fill_1 sky130_fd_sc_hd__fill_2 sky130_fd_sc_hd__fill_4 sky130_fd_sc_hd__fill_8} -prefix FILL -fixDRC
ecoRoute -target

verifyConnectivity -type all -noAntenna -error 10000 -report conn_final.rpt
verify_drc -report drc_final.rpt
verifyProcessAntenna -report antenna_final.rpt

timeDesign -postRoute -outDir timing_setup
timeDesign -postRoute -hold -outDir timing_hold
report_timing > timing_postroute.rpt
report_area  > area_postroute.rpt
report_power > power_postroute.rpt
summaryReport -outFile summary.rpt

saveDesign clementine_sky130.enc
saveNetlist clementine_sky130_pnr.v
write_sdf clementine_sky130.sdf
streamOut clementine_sky130.gds -mode ALL -stripes 1 -units 1000 -libName clementine -structureName tt_um_bigmanraffa_clm -dieAreaAsBoundary
streamOut clementine_sky130_merged.gds -mode ALL -stripes 1 -units 1000 -libName clementine -structureName tt_um_bigmanraffa_clm -dieAreaAsBoundary -merge "$env(PDK_ROOT)/sky130A/libs.ref/sky130_fd_sc_hd/gds/sky130_fd_sc_hd.gds"