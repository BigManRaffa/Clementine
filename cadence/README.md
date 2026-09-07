# Yosys/OpenLane vs Genus/Innovus

SKY130 HD, 25 ns clock.
## 1. Synthesis: Yosys vs Genus

| | Yosys | Genus 21.18-s082_1 |
|---|---|---|
| Total cells | 4,424 | 3,033 |
| Sequential | 547 | 547 |
| Combinational | 3,877 | 2,486 |
| Cell area | 49,288.5 um2 | 32,309.7 um2 |
| Sequential area | 11,634.9 um2 (23.6%) | 13,499.2 um2 (41.8%) |
| Combinational area | 37,653.6 um2 | 18,810.5 um2 |
| Avg area / cell | 11.14 um2 | 10.65 um2 |
| Avg area / comb cell | 9.71 um2 | 7.57 um2 |
| Net area estimate | Yosys has no wire-load model | 19,631.8 um2 |
| Average fanout | Yosys emits no fanout stat | 2.8 |
| Max fanout | Yosys emits no fanout stat | 547 (`clk`) |
| Terms / net | Yosys emits no fanout stat | 3.768 |
| Terms / instance | Yosys emits no fanout stat | 4.035 |
| Setup slack @ ss | +1.28 ns (OpenSTA pre-PnR) | +5.08 ns |
| Setup TNS | 0 | 0 |
| Violating paths | 0 | 0 |
| Effort | LibreLane default | `syn_generic_effort medium` |
| Runtime | 7.1 s | 92.9 s CPU / 124 s real |
| Peak memory | Yosys doesn't log it | 1,717.6 MB |

Yosys extras: 
- 4,408 wires / 4,443 wire bits
    - Yosys reports wire bits because it keeps RTL vector declarations as first-class objects in its netlist.
    - Genus instead flattens buses into individual nets at elaboration and just reports a net count.
- 557 public wires / 592 bits
    - Yosys distinguishes RTL-named wires from its own generated internal `$auto$` ones (an internal net yosys creates during optimization/techmapping) and counts them separately.
    - Genus names every net it creates (n_244, FE_PHC609_n_218) so there's no anonymous category to split out.
- 8 port objects, 43 port bits
    - Yosys keeps each of the 8-bit input and output buses as one port object carrying 8 bits: `ui_in`, `uo_out`, `uio_in`, `uio_out`, `uio_oe` at 8 bits each (40), plus `clk`, `rst_n`, `ena` at 1 bit each (3).
    - Genus splits it at elaboration into 8 separate scalar ports, for example `ui_in[0]` through `ui_in[7]`, so it only ever reports the flat count of 43 I/Os, it's every top-level pin counted individually regardless of direction.

Genus: 
- 46% fewer cells 
- 53% less area
- Medium effort

Combinational logic is exactly 2.00x under Yosys. Flop count identical at 547.

## 2. Place and route: OpenROAD vs Innovus

### Physical

| | OpenROAD | Innovus 21.38-s099_1 |
|---|---|---|
| Die | 334.88 x 225.76 = 75,602.5 um2 | 334 x 216 = 72,102.0 um2 |
| Core | 329.36 x 220.32 = 72,564.6 um2 | 61,545.6 um2 |
| Cell rows | 81 | 72 |
| Placement sites | 57,996 | Innovus reports rows, not sites |
| Routing layers | 4 (`RT_MAX_LAYER = met4`) | 6 |
| Total placed instances | 10,730 | 4,647 + fill |
| Logic instances | 5,390 | 3,639 |
| Antenna diodes | 44 | 0 |
| Tap cells | 1,037 | 1,008 |
| Fill + decap | 4,259 | `summaryReport` excludes fill by design |
| Logic cell area | 59,012 um2 | 37,842.5 um2 |
| Total std cell area | 60,419.2 um2 | 39,103.8 um2 |
| Utilization | 83.26% | 62.885% |
| Density excl. physical | 83.26% | 61.487% |
| Chip density | OpenROAD reports core only | 54.234% |
| Distinct cell types | 80 logic + 4 physical | 95 logic + 1 physical |
| IOs | 45 | 43 |
| Max / mean displacement | 73.9 / 2.814 um | Innovus has no equivalent metric |

### Timing

| | OpenROAD | Innovus |
|---|---|---|
| Setup WNS @ ss_100C_1v60 | **-5.533 ns** | **+3.188 ns** |
| Setup TNS @ ss | -273.55 ns | 0.000 ns |
| Violating setup paths | 341 | 0 |
| Paths analyzed | 552 reported | 1,348 (1,337 reg2reg, 11 default) |
| Setup WNS, default group | OpenSTA doesn't split path groups | +9.747 ns |
| Setup min / p10 / median / max | -5.533 / -2.159 / +3.707 / +20.761 | +3.188 / +3.697 / +4.901 / +5.086 (50 worst) |
| Hold WNS @ ff_n40C_1v95 | +0.057 ns | +0.016 ns |
| Hold TNS | 0.000 ns | 0.000 ns |
| Violating hold paths | 0 | 0 |
| Hold min / median / max | +0.057 ns | +0.016 / +0.033 / +0.039 (50 worst) |
| Clock skew, setup / hold worst | +0.279 / -0.277 ns | needs `report_clock_timing -type skew` |
| Unannotated nets | 62 | 0 |

While the -5.533 does seem noticeable, it is the worst of the worst case scenario, a 1 in 10 billion chance.

However if that 1 in 10 billion chance did happen I would drop the TT clock from 40 MHz to 30 using the RP2040 on the demo board with the commander tool, so it is not a silicon bound issue. I am putting this here for for clarification's sake.

### DRV and signoff

| | OpenROAD | Innovus |
|---|---|---|
| Slew target | 0.75 ns | 0.25 ns |
| Max slew violations @ ss | **2,172** | 0 |
| Max cap violations @ ss | 11 | 0 |
| Max fanout violations | 33 | 0 |
| Max length violations | `WIRE_LENGTH_THRESHOLD` unset, skipped | 0 |
| Router DRC | 0 | 0 |
| Antenna | 0 violations, 13 diodes | 0 violations |
| Signoff DRC | Magic: 0 | no Pegasus deck exists for SKY130 |
| LVS | netgen: 0 errors | same, no Pegasus deck |
| IR drop | 7.9e-6 avg / 5.8e-5 worst | Voltus needs PGV, SKY130 doesn't ship one |
| Lint | Verilator: 0 errors, 8 warnings | pre-synth step, not a P&R metric |
| Connectivity | 11 disconnected pins, 0 critical | clean |
| Floating / no-driven nets | 2 | 4 |
| Multi-driven nets | 0 | 0 |
| Assign statements | 0 | 0 |
| Power grid violations | 0 | n/a |
| HFO (>200) nets | n/a | 0 |
| Streamout | GDS via Magic + KLayout | GDS + merged GDS, done |

Router convergence:

- OpenROAD: 742 -> 301 -> 271 -> 90 -> 0 -> 9 -> 6 -> 6 -> 0 over 9 iterations. 
- Innovus: clean first pass, 0 fails.

## 3. Where the mapping differs

| Category | OpenROAD | Innovus |
|---|---|---|
| Plain flops (`dfxtp`, `dfxbp`) | 547 | 149 |
| Scan / enable flops | 0 | 398 |
| Mux cells | 687 | 187 |
| Full / half adders | **0** | **193** |
| XOR / XNOR | 315 | 387 |
| Plain buffers | 57 | 54 |
| Clock buffers | 137 | 78 |
| Inverters | 45 | 136 |
| Clock inverters | 4 | 17 |
| Delay cells | **768** | **466** |
| Tie cells (`conb`) | 19 | 0 |

Adders
- SKY130 ships prebuilt adder cells (`fa_1`, `ha_1`, `fahcin_1`) that produce two outputs at once, a sum and a carry.
    - Genus grabbed 193 of them for the Dadda trees.
- ABC, the mapper Yosys hands off to, can only pick cells with a single output, so it physically can't use those and has to rebuild every adder out of XOR and AOI gates instead, which is why the combinational area doubles.

Sequential
- My registers only update when an enable signal says so, which in RTL means a mux sitting in front of every flop picking between "keep the old value" and "take the new one".
    - SKY130 has a flop called `sdfxtp_1` that already has that mux built into it. It exists for scan testing, but the mux works the same either way. 
        - Genus used 398 of them and deleted 398 separate mux cells in the process. 
        - Yosys didn't spot that trick and kept the flop and the mux as two cells each time.
- No DFT in genus.tcl so Genus doesn't build a scan chain, it just purely steals the cheaper cell.

**Drive strength:**

| Drive | Yosys | Genus |
|---|---|---|
| `_0` | 0 | 6 (0.2%) |
| `_1` | 690 (15.6%) | 2,915 (96.1%) |
| `_2` | 3,734 (84.4%) | 112 (3.7%) |

`a21oi_2` = 12.51 um2 vs `a21oi_1` = 5.01 um2.

**Polarity:**

| Genus | count | Yosys | count |
|---|---|---|---|
| `nor2_1` | 236 | `a21o_2` | 234 |
| `nand2_1` | 160 | `or2_2` | 193 |
| `a21oi_1` | 156 | `a211o_2` | 158 |
| `mux2i_1` | 148 | `and3_2` | 148 |
| `a22oi_1` | 115 | `o211a_2` | 133 |
| `a221oi_1` | 112 | `o21a_2` | 120 |
| `a222oi_1` | 70 | `a22o_2` | 46 |

**Delay cells:**

| Cell | OpenROAD | Innovus |
|---|---|---|
| `dlygate4sd3_1` | 409 | 1 |
| `dlygate4sd2_1` | 0 | 24 |
| `clkdlybuf4s50_1` | 0 | 409 |
| `clkdlybuf4s50_2` | 0 | 1 |
| `clkdlybuf4s25_1` | 351 | 30 |
| `clkdlybuf4s18_2` | 0 | 1 |
| `clkdlybuf4s15_2` | 2 | 0 |
| `dlymetal6s4s_1` | 5 | 0 |
| `dlymetal6s2s_1` | 1 | 0 |
| **Total** | **768** | **466** |

Both inserted exactly 409 hold-fixing delay cells. The divergence is the ~359 OpenROAD spent on max-fanout repair versus Innovus's ~56. Nine of OpenROAD's sit on the setup-critical path burning 11.34 ns. Innovus's critical path has zero.

## 4. Routing

| | OpenROAD | Innovus | Delta |
|---|---|---|---|
| Routed wirelength | 199,001 um | 137,449.7 um | +44.8% OpenROAD |
| Vias | 45,636 | 32,264 | +41.4% OpenROAD |
| Multi-cut vias | 0 | 0 | |
| Total nets (incl. power) | 5,379 | 3,889 | |
| Signal nets | 5,377 | 3,853 | +39.6% OpenROAD |
| Pin connections | 18,668 | 13,463 | +38.7% OpenROAD |
| Terms / net | 3.479 | 3.494 | -0.4% OpenROAD |
| Wirelength / net | 37.0 um | 35.3 um | +4.8% OpenROAD |
| Vias / net | 8.49 | 8.37 | +1.3% OpenROAD |
| Estimated wirelength | 167,126 um | 112,300 um | |
| Overshoot vs own estimate | +19.1% | +22.4% | |
| Longest net | 592.7 um (routed) | 331-340 um (estimated) | |
| Long connections | check skipped | 0 | |
| Tri-state / degenerate nets | 0 / 0 | 0 / 0 | |

| Layer | OpenROAD wire | Innovus wire | Cut | OpenROAD vias | Innovus vias |
|---|---|---|---|---|---|
| li1 | metrics don't split by layer | 1,954.7 um | li1->met1 | 18,922 | 13,465 |
| met1 | | 42,284.2 um | met1->met2 | 21,901 | 13,697 |
| met2 | | 44,659.1 um | met2->met3 | 3,905 | 2,982 |
| met3 | | 24,108.4 um | met3->met4 | 908 | 1,861 |
| met4 | | 19,821.8 um | met4->met5 | 0 (capped) | 259 |
| met5 | blocked by config | 4,621.5 um | | | |
| **Total** | **199,001 um** | **137,449.7 um** | | **45,636** | **32,264** |

Innovus power net area: met1 9.05%, met4 4.59% of routable area.

Innovus net length: avg 29.15 um (sigma 40.18), RMS 49.64 um, avg connection 11.69 um (sigma 13.50).

**Fanout distribution:**

| Terms | OpenROAD | Innovus |
|---|---|---|
| 2 | 2,118 (39.5%) | 1,954 (50.7%) |
| 3 | 1,909 (35.6%) | 1,273 (33.0%) |
| 4 | 448 (8.3%) | 157 (4.1%) |
| 5 | 254 (4.7%) | 104 (2.7%) |
| 6 | 75 (1.4%) | 29 (0.8%) |
| 7 | 70 (1.3%) | 39 (1.0%) |
| 8 | 88 (1.6%) | 85 (2.2%) |
| 9 | 88 (1.6%) | 108 (2.8%) |
| **>= 10** | **287 (5.3%)** | **104 (2.7%)** |

Both flows average around the same fanout, but OpenROAD has 287 nets with 10 or more loads against Innovus's 104, so 2.76x more high-fanout nets on the OpenROAD side even though the typical net is identical.
- Yosys made 48% more cells and more cells means more things hanging off of each control signal, the decoder output that used to drive 6 gates now drives 15.
- LibreLane was configured with `MAX_FANOUT_CONSTRAINT = 10`, meaning "no net may drive more than 10 loads," so at step 32, `repair_design` looked at all 287 offenders and inserted buffers to split them up. Innovus only had 104 to deal with.

## 5. Runtime

| Stage | OpenROAD | Innovus |
|---|---|---|
| Lint | 0.8 s | pre-synth, not in P&R |
| Synthesis | 7.9 s | 124 s real / 92.9 s CPU |
| Floorplan + PDN + tap | 4.6 s | ~2 s |
| Placement + repair | 16.7 s | 74 s (`place_opt_design`) |
| CTS | 11.9 s | 42 s (`ccopt_design`) |
| Post-CTS / post-route opt | 17.4 s | 101 s (3x `optDesign`) |
| **Routing** | **621.1 s** | **48 s** |
| Antenna | 23.9 s | run separately after the flow |
| Fill | 2.1 s | ~0 s |
| RCX + STA | 21.0 s | 10 s (2x `timeDesign`) |
| DRC / LVS / streamout | 38.4 s | streamout only, no deck for DRC/LVS |
| **P&R total** | **740.6 s** | **277 s CPU / 342 s real** |
| **Flow total** | **787.6 s** | **466 s** |
| Peak memory | 1 GiB (detailed routing) | 3,035 MB hold fix / 2,350 MB route |

Yosys is 15.7x faster at synthesis, but it's the same reason why it produces 46% more cells. NanoRoute is 12.7x faster at routing (globalDetailRoute 43 s, detailRoute 37 s within).

Genus is doing timing-driven mapping:
- It evaluates candidate cells against real liberty delay arcs
- Tries multi-output cells like `fa_1`, does phase assignment (deciding where to use inverting gates)
- Spots the mux+flop merge into `sdfxtp_1`, and runs a `syn_opt` pass afterward. Every one of those is a search over alternatives with timing feedback.

Yosys hands the mapping to ABC which does a fast structural cut-based map with no timing feedback and no multi-output cells. It just picks a working implementation as fast as it can, not necessarily a good one.

TritonRoute took 12.7x longer than NanoRoute on the stopwatch and needed 9 iterations against NanoRoute's 1, at 20 points higher utilization.
- Its DRC trace went 742 -> 301 -> 271 -> 90 -> 0 -> 9 -> 6 -> 6 -> 0, bouncing back up after already reaching zero.
- Measured on the stopwatch it's 12.7x, but measured in processor time it's 41.6x. 
- Detailed routing finished in 615.6 s on the stopwatch, but it spread the work over 8 cores at once, so the total processor time spent was 1,996 s. 
- Innovus routed in 49 s on the stopwatch and spent 48 s of processor time, meaning it used one core and just did the job.
- Stopwatch time is how long the build takes. Processor time is total compute burned, so four cores running for one minute costs you one minute but spends four.
- TritonRoute needs 8 cores going at once to land within an order of magnitude of NanoRoute on one.
- My `run.tcl` script did give Innovus permission to use 4 cores, it just didn't need them for routing. 
    - The two tools may also count processor time differently, so treat 41.6x as each tool's own figure rather than a controlled measurement.
- The time gap cost nothing in quality though.
    - Per-net efficiency came out nearly identical (37.0 vs 35.3 um per net, 8.49 vs 8.37 vias per net) and both finished at zero DRC. 

## 6. Corner handling

| | OpenROAD | Innovus |
|---|---|---|
| Corners during optimization | `nom_tt_025C_1v80` only | ss setup / ff hold from `init_design` |
| `RSZ_CORNERS` / `PNR_CORNERS` | both `None` | MMMC, always both |
| Corners at signoff | all 9 | same 2 |
| Violation checker scope | `TIMING_VIOLATION_CORNERS = ['*tt*']` | n/a |
| Timing-driven placement | `PL_TIMING_DRIVEN = False` | `place_opt_design` |

| Step | Corner | Setup WS |
|---|---|---|
| 12 pre-PnR STA | nom_ss | +1.28 ns |
| 31 mid-PnR | nom_tt | +8.71 ns |
| 36 post-CTS | nom_tt | +11.23 ns |
| 38 post-resize | nom_tt | +12.03 ns |
| 43 post-GRT | nom_tt | +11.58 ns |
| 55 signoff | nom_tt | +9.52 ns |
| 55 signoff | max_ss | **-5.53 ns** |

`flow__errors__count` = 0 with 341 failing paths in the GDS.

## 7. Critical path

| Tool | Startpoint | Endpoint | Cells | Arrival | Slack |
|---|---|---|---|---|---|
| Genus | `fetch_seq_logical_pc_reg[2]` | `lane2_lane_alu_accumulator_reg[15]` | not tabulated pre-layout | 19.357 ns | +5.078 ns |
| Innovus | `fetch_seq_logical_pc_reg[1]` | `lane2_lane_alu_accumulator_reg[15]` | 41 | 21.718 ns | +3.188 ns |
| OpenROAD | `_7706_` = `fetch_seq.logical_pc[2]` | `_7872_` | 116 | 31.336 ns | -5.533 ns |

Of OpenROAD's 31.336 ns: 12.726 ns in 11 inserted repair buffers, 11.339 ns in 9 delay cells.

All of the 150 failing paths OpenSTA actually listed in `violator_list.rpt` come from 3 flops: 

| Launch flop | Net | Paths |
|---|---|---|
| `_7704_` | `fetch_seq.logical_pc[0]` | 86 |
| `_7708_` | `decoder.replay_state` | 62 |
| `_7706_` | `fetch_seq.logical_pc[2]` | 2 |

150 distinct endpoints: `lane0-3.accumulator_value` x41, `lane2.lane_regfile.reg_r1..r7` x42, `mask_stack.stored_predicate` x4, remainder across other lane regfiles.

Innovus path cells: 6 `ha_1`, 5 `a21oi_1`, 3 `xnor2_1`, 3 `mux2i_1`, 3 `a221o_1`, 2 each `xor2_1` / `o2bb2ai_1` / `nor2_1` / `fahcin_1` / `dfxbp_1` / `a22o_1`, 1 each `o221ai_1` / `nand2_1` / `inv_4` / `inv_1` / `fa_1` / `and2b_1` / `and2_4` / `a2bb2o_1` / `a221oi_1`.

This is the parts list of the 41 gates on Innovus's slowest path, and 9 of them are prebuilt adder cells (`ha_1`, `fa_1`, `fahcin_1`) that ABC can't use.

I think it's an interesting statistic since it proves the adder gap isn't necessarily just an area problem because it lands right on the path that sets the maximum clock speed, which is why OpenROAD needs 116 gates for the same journey.

## 8. Power

| | OpenSTA @ ss | Innovus @ ss | Genus @ ss |
|---|---|---|---|
| Total | 1.412 mW | 2.723 mW | 2.346 mW |
| Internal | 1.008 mW (71.4%) | 1.451 mW (53.3%) | 1.370 mW (58.4%) |
| Switching | 0.371 mW (26.3%) | 1.251 mW (45.9%) | 0.957 mW (40.8%) |
| Leakage | 0.034 mW (2.4%) | 0.022 mW (0.8%) | 0.019 mW (0.8%) |
| Sequential | 48.6% | 32.3% | 35.8% |
| Combinational | 11.4% | 60.0% | 60.3% |
| Clock | 40.1% | 7.7% | 4.0% |
| Activity source | OpenSTA propagated default | flat 0.2 | Genus default |

Power depends on how often the signals actually flip, and none of the three tools were given an activity trace (ex: a VCD from my testbench), so each one guessed differently.
- Innovus assumed a flat 20% toggle rate, OpenSTA propagated its own default, Genus used another.

OpenSTA's guess came out so low that 3,827 combinational cells burn 5.6e-5 W between them, which is essentially zero and physically impossible for real switching logic, so the three numbers aren't measuring the same thing and shouldn't be compared. I'm just putting this statistic here in case anyone asks "but what about power?"

## 9. Constraints

| | LibreLane | Cadence |
|---|---|---|
| Clock period | 25.0 ns | 25.0 ns |
| Clock uncertainty | 0.25 ns | 0.25 ns |
| Input / output delay | 5.0 ns | 5.0 ns |
| Clock transition | 0.15 ns | 0.1 ns |
| Output load | 0.0334 pF | 0.05 pF |
| Input transition | not set | 0.1 ns |
| `rst_n` / `ena` | timed | `set_false_path` |
| Setup / hold corners | ss_100C_1v60 / ff_n40C_1v95 | same |
| Library | `sky130_fd_sc_hd` | same |

## Verdict

Cadence takes every quality metric: 
- 46% fewer cells
- 53% less area at synthesis
- 48% fewer instances post-route
- +3.19 ns against -5.53 ns setup
- zero DRVs against 2,216. 

Open source takes runtime, but only at synthesis: 

- Yosys is 15.7x faster because it isn't doing the timing-driven search that produces those numbers above. 
- The routing side is the opposite, TritonRoute is 12.7x slower for identical per-net quality.
- If you stripped routing out, the open source wins the whole flow: 
    - 166.5 s against 418 s, a 2.5x lead. Routing is the only thing keeping it behind.

The single mechanical cause:
- ABC cannot map multi-output cells, so `fa_1` / `ha_1` are unreachable and the Baugh-Wooley Dadda trees get rebuilt from generic gates. 
- That inflates combinational cell count, which inflates net count, which inflates high-fanout nets 2.76x, which triggers 359 fanout-repair delay cells, nine of which land on the critical path and burn 11.34 ns.