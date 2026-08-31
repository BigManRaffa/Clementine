set SKY $env(PDK_ROOT)/sky130A/libs.ref/sky130_fd_sc_hd

create_library_set -name slow_lib -timing [list $SKY/lib/sky130_fd_sc_hd__ss_100C_1v60.lib]
create_library_set -name fast_lib -timing [list $SKY/lib/sky130_fd_sc_hd__ff_n40C_1v95.lib]

create_rc_corner -name typ -T 25

create_delay_corner -name slow_corner -library_set slow_lib -rc_corner typ
create_delay_corner -name fast_corner -library_set fast_lib -rc_corner typ

create_constraint_mode -name func -sdc_files [list clementine_syn.sdc]

create_analysis_view -name setup_view -delay_corner slow_corner -constraint_mode func
create_analysis_view -name hold_view  -delay_corner fast_corner -constraint_mode func

set_analysis_view -setup {setup_view} -hold {hold_view}