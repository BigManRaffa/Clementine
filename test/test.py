import os
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge, Timer
from cocotb.handle import Deposit, Force, Release


# ---------------------------------------------------------------------------
# Integer helpers
# ---------------------------------------------------------------------------


def u8(v: int) -> int:
    return v & 0xFF


def u16(v: int) -> int:
    return v & 0xFFFF


def s8(v: int) -> int:
    v &= 0xFF
    return v - 0x100 if v & 0x80 else v


def s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def signed_product8(a: int, b: int) -> int:
    return u16(s8(a) * s8(b))


def signed_cmp8(a: int, b: int, cond: int) -> bool:
    aa, bb = s8(a), s8(b)
    if cond == 0:
        return aa < bb
    if cond == 1:
        return aa > bb
    if cond == 2:
        return aa <= bb
    if cond == 3:
        return aa >= bb
    raise ValueError(cond)


# ---------------------------------------------------------------------------
# ISA encoders.  These implement the documented fixed 16-bit ISA, not decoder
# equations from RTL.
# ---------------------------------------------------------------------------

OP_NOP = 0x0
OP_ADD = 0x1
OP_SUB = 0x2
OP_AND = 0x3
OP_OR = 0x4
OP_XOR = 0x5
OP_SHIFT = 0x6
OP_MAC = 0x7
OP_CLRACC = 0x8
OP_LDI = 0x9
OP_MOV = 0xA
OP_CMP = 0xB
OP_MVAC = 0xC
OP_LDAC = 0xD
OP_IFP_ELSE = 0xE
OP_HALT = 0xF

COND_LT = 0
COND_GT = 1
COND_LE = 2
COND_GE = 3


def _rrr(op: int, rd: int, rs: int, rt: int, low: int = 0) -> int:
    return ((op & 0xF) << 12) | ((rd & 7) << 9) | ((rs & 7) << 6) | ((rt & 7) << 3) | (low & 7)


def NOP() -> int:
    return 0x0000


def ADD(rd: int, rs: int, rt: int) -> int:
    return _rrr(OP_ADD, rd, rs, rt)


def SUB(rd: int, rs: int, rt: int) -> int:
    return _rrr(OP_SUB, rd, rs, rt)


def AND(rd: int, rs: int, rt: int) -> int:
    return _rrr(OP_AND, rd, rs, rt)


def OR(rd: int, rs: int, rt: int) -> int:
    return _rrr(OP_OR, rd, rs, rt)


def XOR(rd: int, rs: int, rt: int) -> int:
    return _rrr(OP_XOR, rd, rs, rt)


def SHL(rd: int, rs: int, rt: int) -> int:
    return _rrr(OP_SHIFT, rd, rs, rt, low=0)


def SHR(rd: int, rs: int, rt: int) -> int:
    return _rrr(OP_SHIFT, rd, rs, rt, low=1)


def MAC(rs: int, rt: int) -> int:
    return (OP_MAC << 12) | ((rs & 7) << 6) | ((rt & 7) << 3)


def CLRACC() -> int:
    return OP_CLRACC << 12


def LDI(rd: int, imm8: int) -> int:
    # bit 0 is deliberately kept zero.
    return (OP_LDI << 12) | ((rd & 7) << 9) | ((imm8 & 0xFF) << 1)


def MOV(rd: int, rs: int) -> int:
    # MOV/LANEID subop is bit 0.  Normal MOV must encode zero here.
    return (OP_MOV << 12) | ((rd & 7) << 9) | ((rs & 7) << 6)


def LANEID(rd: int) -> int:
    # rs is architecturally ignored.  Encode it as zero for deterministic code.
    return (OP_MOV << 12) | ((rd & 7) << 9) | 1


def CMP(cond: int, rs: int, rt: int) -> int:
    return (OP_CMP << 12) | ((cond & 3) << 10) | ((rs & 7) << 6) | ((rt & 7) << 3)


def MVAC(rd: int, high: int = 0) -> int:
    return (OP_MVAC << 12) | ((rd & 7) << 9) | ((high & 1) << 3)


def LDAC(rs: int, high: int = 0) -> int:
    return (OP_LDAC << 12) | ((rs & 7) << 6) | ((high & 1) << 3)


def IFP(else_pc: int) -> int:
    return (OP_IFP_ELSE << 12) | (0 << 4) | (else_pc & 0xF)


def ELSE(endif_pc: int) -> int:
    return (OP_IFP_ELSE << 12) | (1 << 4) | (endif_pc & 0xF)


def HALT() -> int:
    return OP_HALT << 12


def pad_kernel(words: Sequence[int]) -> List[int]:
    """Return exactly 16 words with HALT in slot 15.

    Input may either already contain a HALT as its final semantic instruction or
    omit it.  The source HALT is treated as an end marker and physically placed
    in slot 15, matching the assembler contract.
    """
    body = list(words)
    if body and ((body[-1] >> 12) & 0xF) == OP_HALT:
        body = body[:-1]
    if len(body) > 15:
        raise ValueError("kernel body exceeds 15 words before HALT")
    return [u16(x) for x in body] + [NOP()] * (15 - len(body)) + [HALT()]


def exact_kernel(words: Sequence[int]) -> List[int]:
    if len(words) != 16:
        raise ValueError(f"expected exactly 16 words, got {len(words)}")
    if ((words[15] >> 12) & 0xF) != OP_HALT:
        raise ValueError("slot 15 must be HALT")
    return [u16(x) for x in words]


# ---------------------------------------------------------------------------
# Cocotb utilities
# ---------------------------------------------------------------------------


_TEST_CLOCK_TASK = None
_TEST_CLOCK_PERIOD_NS = 20


async def ensure_clock(dut, period_ns: int = 20):
    """Start one clock for THIS Cocotb test.

    Cocotb kills child tasks when a test ends.  Therefore a clock task cannot be
    shared across separate @cocotb.test() functions.  The previous regression
    kept a stale global Task handle after Cocotb had killed the actual clock,
    causing test #2 to lose clk and the simulator to terminate; all later tests
    then appeared as 0 ns failures.

    This helper restarts the clock whenever the stored task is absent or done,
    and repeated calls inside the same test reuse that one live task.
    """
    global _TEST_CLOCK_TASK, _TEST_CLOCK_PERIOD_NS

    if _TEST_CLOCK_TASK is not None:
        try:
            task_done = _TEST_CLOCK_TASK.done()
        except Exception:
            task_done = True
        if task_done:
            _TEST_CLOCK_TASK = None

    if _TEST_CLOCK_TASK is None:
        _TEST_CLOCK_PERIOD_NS = period_ns
        _TEST_CLOCK_TASK = cocotb.start_soon(
            Clock(dut.clk, period_ns, unit="ns").start()
        )
        await Timer(1, unit="ns")
    elif period_ns != _TEST_CLOCK_PERIOD_NS:
        raise AssertionError(
            f"clock already running at {_TEST_CLOCK_PERIOD_NS} ns, requested {period_ns} ns"
        )


def stop_test_clock():
    """Stop the current test's clock and forget its Task handle."""
    global _TEST_CLOCK_TASK
    if _TEST_CLOCK_TASK is not None:
        try:
            _TEST_CLOCK_TASK.kill()
        except Exception:
            pass
        _TEST_CLOCK_TASK = None


async def start_clock(dut, period_ns: int = 20):
    await ensure_clock(dut, period_ns)


async def active_low_reset(dut, cycles: int = 4):
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, cycles)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def settle(ns: int = 1):
    await Timer(ns, unit="ns")


def value_is_resolvable(sig) -> bool:
    try:
        return bool(sig.value.is_resolvable)
    except AttributeError:
        try:
            int(sig.value)
            return True
        except Exception:
            return False


def iv(sig) -> int:
    return int(sig.value)


def bit(v: int, n: int) -> int:
    return (v >> n) & 1


def exhaustive_enabled() -> bool:
    """Full coverage is the default; FAST=1 is a developer convenience only."""
    return os.getenv("FAST", "0") not in {"1", "true", "TRUE", "yes", "YES"}


def exhaustive_values() -> Iterable[int]:
    if exhaustive_enabled():
        return range(256)
    # Deliberately biased quick subset: zeros, sign edges, patterns, random-ish.
    return [0x00, 0x01, 0x02, 0x07, 0x0F, 0x55, 0x7E, 0x7F,
            0x80, 0x81, 0xAA, 0xF0, 0xFE, 0xFF]


# ---------------------------------------------------------------------------
# SPI master.  It models the external master, not internal slave timing.
# SPI mode 0, 3 command bits + 16 data bits, MSB first, one CS assertion.
# ---------------------------------------------------------------------------

SPI_CMD_LOAD = 0b000
SPI_CMD_GO = 0b001
SPI_CMD_STATUS = 0b010
SPI_CMD_NOP = 0b011
SPI_CMD_ACC0 = 0b100
SPI_CMD_ACC1 = 0b101
SPI_CMD_ACC2 = 0b110
SPI_CMD_ACC3 = 0b111


class SpiMaster:
    def __init__(self, dut, top_level: bool = False):
        self.dut = dut
        self.top_level = top_level
        self._uio = 0

    def _drive_top_bit(self, idx: int, val: int):
        if val:
            self._uio |= 1 << idx
        else:
            self._uio &= ~(1 << idx)
        self.dut.uio_in.value = self._uio

    def set_cs(self, val: int):
        if self.top_level:
            self._drive_top_bit(0, val)
        else:
            self.dut.spi_cs_n.value = val

    def set_mosi(self, val: int):
        if self.top_level:
            self._drive_top_bit(1, val)
        else:
            self.dut.spi_mosi.value = val

    def set_sclk(self, val: int):
        if self.top_level:
            self._drive_top_bit(3, val)
        else:
            self.dut.spi_sclk.value = val

    def get_miso(self) -> int:
        if self.top_level:
            return (int(self.dut.uio_out.value) >> 2) & 1
        return int(self.dut.spi_miso.value)

    async def idle(self, core_cycles: int = 5):
        self.set_sclk(0)
        self.set_mosi(0)
        self.set_cs(1)
        await ClockCycles(self.dut.clk, core_cycles)

    async def transaction(
        self,
        command: int,
        tx_word: int = 0,
        spi_hz: int = 5_000_000,
        phase_offset_ns: int = 0,
    ) -> int:
        if not (0 <= command < 8):
            raise ValueError(command)
        if spi_hz <= 0:
            raise ValueError(spi_hz)
        half_ns = 1e9 / (2.0 * spi_hz)
        if half_ns < 1:
            raise ValueError("SPI helper requires >=1 ns half-period")

        self.set_sclk(0)
        self.set_mosi(0)
        self.set_cs(1)
        if phase_offset_ns:
            await Timer(phase_offset_ns, unit="ns")

        self.set_cs(0)
        # Let the three-stage CS synchronizer observe a clean falling edge.
        await ClockCycles(self.dut.clk, 4)

        bits = [((command >> n) & 1) for n in (2, 1, 0)]
        bits += [((tx_word >> n) & 1) for n in range(15, -1, -1)]
        rx_bits: List[int] = []

        sample_delay = min(10.0, half_ns / 4.0)
        high_remainder = half_ns - sample_delay

        for i, out_bit in enumerate(bits):
            # Mode 0: data becomes stable while SCLK is low.
            self.set_mosi(out_bit)
            await Timer(half_ns, unit="ns")
            self.set_sclk(1)
            await Timer(sample_delay, unit="ns")

            # The first three returned bits are command-phase don't-cares.
            if i >= 3:
                rx_bits.append(self.get_miso())

            if high_remainder > 0:
                await Timer(high_remainder, unit="ns")
            self.set_sclk(0)

        # A proper frame keeps CS low through the final falling edge and gives
        # the core-domain edge detector time to consume the final rising edge.
        await Timer(half_ns, unit="ns")
        self.set_cs(1)
        self.set_mosi(0)
        await ClockCycles(self.dut.clk, 5)

        rx = 0
        for b in rx_bits:
            rx = (rx << 1) | b
        return rx

    async def command_only(
        self,
        command: int,
        spi_hz: int = 5_000_000,
        phase_offset_ns: int = 0,
    ):
        """Send exactly the 3 command bits, including the falling boundary.

        This is intentionally protocol-short and is used only to verify the
        specified GO exception: GO is allowed to fire at the command/data
        boundary before the nominal 16 dummy data clocks.
        """
        half_ns = 1e9 / (2.0 * spi_hz)
        self.set_sclk(0)
        self.set_mosi(0)
        self.set_cs(1)
        if phase_offset_ns:
            await Timer(phase_offset_ns, unit="ns")
        self.set_cs(0)
        await ClockCycles(self.dut.clk, 4)
        for n in (2, 1, 0):
            self.set_mosi((command >> n) & 1)
            await Timer(half_ns, unit="ns")
            self.set_sclk(1)
            await Timer(half_ns, unit="ns")
            self.set_sclk(0)
        # Give the synchronized falling edge after command bit 0 time to become
        # at_phase_boundary, then abort the nominal data phase.
        await ClockCycles(self.dut.clk, 4)
        self.set_cs(1)
        self.set_mosi(0)
        await ClockCycles(self.dut.clk, 5)

    async def partial_load(
        self,
        data_bits: Sequence[int],
        spi_hz: int = 5_000_000,
    ):
        """Send LOAD plus fewer than 16 data bits and then raise CS."""
        if len(data_bits) >= 16:
            raise ValueError("partial_load expects fewer than 16 data bits")
        half_ns = 1e9 / (2.0 * spi_hz)
        self.set_sclk(0)
        self.set_mosi(0)
        self.set_cs(0)
        await ClockCycles(self.dut.clk, 4)
        stream = [0, 0, 0] + [int(x) & 1 for x in data_bits]
        for b in stream:
            self.set_mosi(b)
            await Timer(half_ns, unit="ns")
            self.set_sclk(1)
            await Timer(half_ns, unit="ns")
            self.set_sclk(0)
        await Timer(half_ns, unit="ns")
        self.set_cs(1)
        await ClockCycles(self.dut.clk, 6)


async def spi_load_kernel(spi: SpiMaster, words: Sequence[int], spi_hz: int = 5_000_000):
    if len(words) != 16:
        raise ValueError("Clementine kernel image is exactly 16 words")
    for word in words:
        await spi.transaction(SPI_CMD_LOAD, word, spi_hz=spi_hz)


async def spi_read_status(spi: SpiMaster, spi_hz: int = 5_000_000) -> int:
    return await spi.transaction(SPI_CMD_STATUS, 0, spi_hz=spi_hz)


async def spi_read_acc(spi: SpiMaster, lane: int, spi_hz: int = 5_000_000) -> int:
    if lane not in range(4):
        raise ValueError(lane)
    return await spi.transaction(SPI_CMD_ACC0 + lane, 0, spi_hz=spi_hz)


async def wait_for_done_signal(done_sig, clk, timeout_cycles: int = 256):
    for _ in range(timeout_cycles):
        await RisingEdge(clk)
        await ReadOnly()
        if int(done_sig.value):
            return
    raise AssertionError(f"DONE did not assert within {timeout_cycles} core cycles")


@dataclass
class RunStats:
    cycles: int
    commits: int
    pc_advances: Optional[int]
    final_pc: Optional[int]
    final_instruction: Optional[int]


async def monitor_top_run(dut, timeout_cycles: int = 256) -> RunStats:
    """Measure a run from DONE falling to DONE rising.

    When the simulator exposes fetch_seq.logical_pc, the monitor also proves the
    physical-ring/PC invariant by counting every modulo-16 PC advance.  This is
    diagnostic visibility only; pass/fail expectations are architectural.
    """
    done_sig = dut.uo_out

    # Wait until the fetch sequencer actually leaves halted state.
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if not value_is_resolvable(done_sig):
            raise AssertionError("DONE/debug bus contains X/Z while waiting for run to start")
        if (int(done_sig.value) & 1) == 0:
            break
    else:
        raise AssertionError("run never left HALT after GO")

    pc_handle = None
    try:
        pc_handle = dut.fetch_seq.logical_pc
        prev_pc = int(pc_handle.value)
        advances = 0
    except Exception:
        prev_pc = None
        advances = None

    cycles = 0
    commits = 0
    while cycles < timeout_cycles:
        # Sample commit permission *before* the edge it governs.  Sampling only
        # after the edge would miss the HALT commit because halted becomes high
        # on that same edge and immediately drives instruction_commit low.
        try:
            commit_before_edge = (int(dut.uo_out.value) >> 2) & 1
        except Exception:
            try:
                commit_before_edge = int(dut.instruction_commit.value)
            except Exception:
                commit_before_edge = 0

        await RisingEdge(dut.clk)
        await ReadOnly()
        cycles += 1
        commits += commit_before_edge

        if pc_handle is not None:
            new_pc = int(pc_handle.value)
            if new_pc != prev_pc:
                if new_pc != ((prev_pc + 1) & 0xF):
                    raise AssertionError(
                        f"logical PC moved illegally {prev_pc:x}->{new_pc:x}; "
                        "valid movement is hold or +1 mod 16"
                    )
                advances += 1
            prev_pc = new_pc

        if not value_is_resolvable(done_sig):
            raise AssertionError("DONE/debug bus contains X/Z during active run")
        if int(done_sig.value) & 1:
            final_instruction = None
            try:
                final_instruction = int(dut.current_instruction.value)
            except Exception:
                try:
                    final_instruction = int(dut.fetch_seq.current_instruction.value)
                except Exception:
                    pass
            return RunStats(
                cycles=cycles,
                commits=commits,
                pc_advances=advances,
                final_pc=prev_pc,
                final_instruction=final_instruction,
            )

    raise AssertionError(f"run did not return to HALT within {timeout_cycles} core cycles")

# ---------------------------------------------------------------------------
# Single-TOPLEVEL hierarchy harness
# ---------------------------------------------------------------------------
#
# TinyTapeout always compiles tb.v as TOPLEVEL=tb.  tb.v stays UNCHANGED.
# Exhaustive tests therefore operate on the REAL production submodules already
# instantiated beneath tb.tt_um_bigmanraffa_clm.  Python temporarily forces only
# the INPUT ports of the target child; its outputs/state/logic are untouched.
# Every force is released after the test.  Full-chip tests do not force anything.
# ---------------------------------------------------------------------------


class _DepositedSignal:
    """Writable view of a real storage element; no VPI force is left behind."""
    def __init__(self, handle):
        self._handle = handle

    @property
    def value(self):
        return self._handle.value

    @value.setter
    def value(self, new_value):
        self._handle.value = Deposit(new_value)

    def __getattr__(self, name):
        return getattr(self._handle, name)


class _ForcedSignal:
    def __init__(self, handle, forced_registry):
        self._handle = handle
        self._forced_registry = forced_registry

    @property
    def value(self):
        return self._handle.value

    @value.setter
    def value(self, new_value):
        self._handle.value = Force(new_value)
        self._forced_registry[id(self._handle)] = self._handle

    def __getattr__(self, name):
        return getattr(self._handle, name)


class _HierarchyDut:
    def __init__(self, root, target, force_inputs, forced_registry):
        self._root = root
        self._target = target
        self._force_inputs = set(force_inputs)
        self._forced_registry = forced_registry

    def __getattr__(self, name):
        if name == "clk":
            return self._root.clk
        if name == "rst_n":
            return self._root.rst_n

        handle = getattr(self._target, name)
        if name in self._force_inputs:
            return _ForcedSignal(handle, self._forced_registry)
        return handle


class _DecoderHierarchyDut(_HierarchyDut):
    """Drive decoder through the real fetch-sequencer source storage.

    Icarus does not reliably honor a Force applied directly to a child input
    net that is continuously driven by its parent.  The decoder's instruction
    input really comes from instruction_ring[0], and replay_state really comes
    from fetch_seq.fsm_state.  During the first three combinational decoder
    tests the core clock has not started, so depositing those two real state
    elements gives a stable, wrapper-free exhaustive stimulus source.
    """
    def __init__(self, root, target, force_inputs, forced_registry):
        super().__init__(root, target, force_inputs, forced_registry)
        fetch = _core(root).fetch_seq
        try:
            self._instruction_source = fetch.instruction_ring[0]
        except Exception as exc:
            raise AssertionError(
                "Icarus did not expose fetch_seq.instruction_ring[0]; decoder exhaustive test cannot be driven"
            ) from exc
        self._replay_source = fetch.fsm_state

    def __getattr__(self, name):
        if name == "current_instruction":
            return _DepositedSignal(self._instruction_source)
        if name == "replay_state":
            return _DepositedSignal(self._replay_source)
        return super().__getattr__(name)


def _core(root):
    return root.tt_um_bigmanraffa_clm


def _resolve_path(handle, dotted_path):
    for piece in dotted_path.split("."):
        handle = getattr(handle, piece)
    return handle


_HIER_TARGETS = {
    "clm_decoder": (
        "decoder",
        {"current_instruction", "replay_state"},
    ),
    "clm_regfile": (
        "lane0.lane_regfile",
        {
            "write_enable", "write_address", "write_data",
            "read_row_even", "read_row_odd",
        },
    ),
    "clm_mask_stack": (
        "mask_stack",
        {
            "predicate_out", "predicate_write_qualified",
            "command_ifp", "command_else", "command_reconverge_pop",
            "target_address",
        },
    ),
    "clm_fetch_seq": (
        "fetch_seq",
        {
            "go",
            "instruction_shift_enable", "instruction_shift_data",
            "rs_address", "rt_address", "bank_conflict", "is_halt",
            "any_lane_active",
            "stack_top_valid", "stack_top_type", "stack_top_target",
        },
    ),
    "clm_mult_bw": (
        "lane0.lane_alu.engine_multiplier",
        {"a", "b"},
    ),
    "clm_alu": (
        "lane0.lane_alu",
        {
            "highway_left", "highway_right", "immediate_value",
            "select_immediate", "force_one_box1", "force_one_box2",
            "swap_operands", "subtract_prepare", "prepare_zero",
            "select_accumulator",
            "shift_direction", "bitwise_select", "condition_select",
            "accumulator_clear", "accumulator_load",
            "accumulator_mac_capture", "accumulator_half_select",
            "ldac_select_highway_right", "writeback_select",
            "writeback_enable_arithmetic", "writeback_enable_shift",
            "writeback_enable_bitwise", "writeback_enable_mvac",
        },
    ),
    "clm_lane": (
        "lane0",
        {
            "read_row_even", "read_row_odd", "conflict_bank_select",
            "rd_address", "immediate_value",
            "operand_hold_load", "operand_hold_use",
            "instruction_commit", "register_write_enable",
            "predicate_write_enable",
            "select_immediate", "force_one_box1", "force_one_box2",
            "swap_operands", "laneid_mode",
            "subtract_prepare", "prepare_zero", "select_accumulator",
            "shift_direction", "bitwise_select", "condition_select",
            "accumulator_clear", "accumulator_load",
            "accumulator_mac_capture", "accumulator_half_select",
            "ldac_select_highway_right", "writeback_select",
            "lane_active",
        },
    ),
    "clm_spi_host": (
        "spi_host",
        {
            "spi_sclk", "spi_mosi", "spi_cs_n",
            "sequencer_done",
            "lane0_accumulator", "lane1_accumulator",
            "lane2_accumulator", "lane3_accumulator",
        },
    ),
}


async def _release_forces(forced_registry):
    """Release every VPI force from a writable phase before another test starts."""
    # A failed assertion can unwind while Cocotb is in ReadOnly.  Advancing time
    # first guarantees that Release() itself is legal and prevents poisoned
    # forces from leaking into the next test.
    await Timer(1, unit="ns")
    errors = []
    for handle in list(forced_registry.values()):
        try:
            handle.value = Release()
        except Exception as exc:
            errors.append((getattr(handle, "_path", repr(handle)), repr(exc)))
    forced_registry.clear()
    await Timer(1, unit="ns")
    if errors:
        raise AssertionError("failed to release VPI forces: " + repr(errors))


def hierarchy_test(target_name):
    """Run a real child-module test under the one fixed TinyTapeout tb.

    This never silently returns.  Missing hierarchy is a hard failure, so a
    reported PASS always means the test body actually executed.
    """
    if target_name not in _HIER_TARGETS:
        raise ValueError("unknown hierarchy target: " + target_name)

    def decorate(fn):
        async def guarded(root, *args, **kwargs):
            # Never inherit the scheduler's ReadOnly phase from a previous test.
            await Timer(1, unit="ns")
            root.ena.value = 1
            root.ui_in.value = 0
            root.uio_in.value = 0b00000001  # CS high, SCLK/MOSI low

            # Decoder is deliberately first and purely combinational, so it can
            # run before a clock exists.  Every sequential unit test gets ONE clock for that test only and begins
            # with the real core control logic reset/halted.
            if target_name == "clm_decoder":
                root.rst_n.value = 1
            else:
                await ensure_clock(root, 20)
                root.rst_n.value = 0
                await ClockCycles(root.clk, 2)
                root.rst_n.value = 1
                await ClockCycles(root.clk, 1)
                await Timer(1, unit="ns")

            path, force_inputs = _HIER_TARGETS[target_name]
            try:
                target = _resolve_path(_core(root), path)
            except Exception as exc:
                raise AssertionError(
                    "Required production hierarchy is missing: "
                    + "tb.tt_um_bigmanraffa_clm." + path
                ) from exc

            forced_registry = {}
            proxy_cls = _DecoderHierarchyDut if target_name == "clm_decoder" else _HierarchyDut
            unit = proxy_cls(
                root=root,
                target=target,
                force_inputs=force_inputs,
                forced_registry=forced_registry,
            )
            try:
                return await fn(unit, *args, **kwargs)
            finally:
                await _release_forces(forced_registry)
                # Cleanup above deliberately advances out of ReadOnly.
                root.uio_in.value = 0b00000001
                await Timer(1, unit="ns")
                # A Cocotb clock is a child Task of this test.  Never try to
                # carry its Task handle into the next @cocotb.test().
                stop_test_clock()

        guarded.__name__ = fn.__name__
        guarded.__qualname__ = fn.__name__
        guarded.__doc__ = fn.__doc__
        return cocotb.test()(guarded)

    return decorate


# ===========================================================================
# TEST_DECODER  (REAL HIERARCHY: core.decoder)
# ===========================================================================

decoder_DONTCARE = object()


def decoder_expected_decoder(instr: int, replay: int):
    """Architectural golden model built from the ISA table.

    The model does not reproduce RTL equations.  It starts from instruction
    meaning: which resource is supposed to be used, which write is architecturally
    permitted, and which routing form is required by physical register parity.
    """
    op = (instr >> 12) & 0xF
    rd = (instr >> 9) & 0x7
    rs = (instr >> 6) & 0x7
    rt = (instr >> 3) & 0x7
    imm = (instr >> 1) & 0xFF
    target = instr & 0xF
    mov_laneid = instr & 1
    reversed_order = bool((rs & 1) and not (rt & 1))

    exp = {
        # Always-valid field exports.
        "rd_address": rd,
        "rs_address": rs,
        "rt_address": rt,
        "immediate_value": imm,
        "mask_target": target,

        # State/control defaults.
        "uses_both_sources": 0,
        "bank_conflict": 0,
        "is_halt": 0,
        "command_ifp": 0,
        "command_else": 0,
        "select_immediate": 0,
        "force_one_box1": 0,
        "force_one_box2": 0,
        "swap_operands": 0,
        "laneid_mode": 0,
        "subtract_prepare": 0,
        "prepare_zero": 0,
        "select_accumulator": 0,
        "shift_direction": decoder_DONTCARE,
        "bitwise_select": 0,
        "condition_select": decoder_DONTCARE,
        "accumulator_clear": 0,
        "accumulator_load": 0,
        "accumulator_mac_capture": 0,
        "accumulator_half_select": decoder_DONTCARE,
        "ldac_select_highway_right": decoder_DONTCARE,
        "writeback_select": 0,
        "register_write_enable": 0,
        "predicate_write_enable": 0,
    }

    uses_two = op in {OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR, OP_SHIFT, OP_MAC, OP_CMP}
    exp["uses_both_sources"] = int(uses_two)
    exp["bank_conflict"] = int(uses_two and ((rs & 1) == (rt & 1)))

    if op == OP_NOP:
        pass
    elif op == OP_ADD:
        exp["force_one_box2"] = 1
        exp["register_write_enable"] = 1
        exp["writeback_select"] = 0b00
    elif op == OP_SUB:
        exp["subtract_prepare"] = 1
        exp["register_write_enable"] = 1
        exp["writeback_select"] = 0b00
        if reversed_order:
            exp["force_one_box1"] = 1
        else:
            exp["force_one_box2"] = 1
        exp["swap_operands"] = int(reversed_order and not replay)
    elif op == OP_AND:
        exp["bitwise_select"] = 0b00
        exp["writeback_select"] = 0b10
        exp["register_write_enable"] = 1
    elif op == OP_OR:
        exp["bitwise_select"] = 0b01
        exp["writeback_select"] = 0b10
        exp["register_write_enable"] = 1
    elif op == OP_XOR:
        exp["bitwise_select"] = 0b10
        exp["writeback_select"] = 0b10
        exp["register_write_enable"] = 1
    elif op == OP_SHIFT:
        exp["shift_direction"] = instr & 1
        exp["writeback_select"] = 0b01
        exp["register_write_enable"] = 1
        exp["swap_operands"] = int(reversed_order and not replay)
    elif op == OP_MAC:
        exp["select_accumulator"] = 1
        exp["accumulator_mac_capture"] = 1
    elif op == OP_CLRACC:
        exp["accumulator_clear"] = 1
    elif op == OP_LDI:
        exp["select_immediate"] = 1
        exp["force_one_box2"] = 1
        exp["prepare_zero"] = 1
        exp["register_write_enable"] = 1
    elif op == OP_MOV:
        exp["laneid_mode"] = mov_laneid
        exp["prepare_zero"] = 1
        exp["register_write_enable"] = 1
        # During LANEID the ALU result is architecturally ignored.  Do not turn
        # its unused force-one controls into part of the ISA contract.
        if mov_laneid:
            exp["force_one_box1"] = decoder_DONTCARE
            exp["force_one_box2"] = decoder_DONTCARE
        elif rs & 1:
            exp["force_one_box1"] = 1
        else:
            exp["force_one_box2"] = 1
    elif op == OP_CMP:
        exp["subtract_prepare"] = 1
        exp["predicate_write_enable"] = 1
        exp["condition_select"] = (instr >> 10) & 0x3
        if reversed_order:
            exp["force_one_box1"] = 1
        else:
            exp["force_one_box2"] = 1
        exp["swap_operands"] = int(reversed_order and not replay)
    elif op == OP_MVAC:
        exp["accumulator_half_select"] = (instr >> 3) & 1
        exp["writeback_select"] = 0b11
        exp["register_write_enable"] = 1
    elif op == OP_LDAC:
        exp["accumulator_load"] = 1
        exp["accumulator_half_select"] = (instr >> 3) & 1
        exp["ldac_select_highway_right"] = rs & 1
    elif op == OP_IFP_ELSE:
        if (instr >> 4) & 1:
            exp["command_else"] = 1
        else:
            exp["command_ifp"] = 1
    elif op == OP_HALT:
        exp["is_halt"] = 1
    else:
        raise AssertionError(op)

    return exp


def decoder_check_signal(dut, name: str, expected, instr: int, replay: int):
    if expected is decoder_DONTCARE:
        return
    got = int(getattr(dut, name).value)
    if got != expected:
        op = (instr >> 12) & 0xF
        raise AssertionError(
            f"decoder mismatch: instr=0x{instr:04X} op=0x{op:X} replay={replay} "
            f"signal={name} expected={expected} got={got}"
        )








# ===========================================================================
# TEST_REGFILE  (REAL HIERARCHY: core.lane0.lane_regfile)
# ===========================================================================

async def regfile_start(dut):
    await ensure_clock(dut, 20)
    dut.write_enable.value = 0
    dut.write_address.value = 0
    dut.write_data.value = 0
    dut.read_row_even.value = 0
    dut.read_row_odd.value = 0
    await Timer(1, unit="ns")


async def regfile_write_reg(dut, addr: int, value: int):
    dut.write_address.value = addr
    dut.write_data.value = value & 0xFF
    dut.write_enable.value = 1
    await RisingEdge(dut.clk)
    await ReadOnly()
    await Timer(1, unit="ns")
    dut.write_enable.value = 0


async def regfile_read_reg(dut, addr: int) -> int:
    row = (addr >> 1) & 0x3
    if addr & 1:
        dut.read_row_odd.value = row
        await Timer(1, unit="ns")
        return int(dut.read_data_odd.value)
    dut.read_row_even.value = row
    await Timer(1, unit="ns")
    return int(dut.read_data_even.value)


async def regfile_snapshot(dut):
    return [await regfile_read_reg(dut, i) for i in range(8)]




# ===========================================================================
# TEST_MASK_STACK  (REAL HIERARCHY: core.mask_stack)
# ===========================================================================

async def mask_start(dut):
    await ensure_clock(dut, 20)
    dut.rst_n.value = 0
    dut.predicate_out.value = 0
    dut.predicate_write_qualified.value = 0
    dut.command_ifp.value = 0
    dut.command_else.value = 0
    dut.command_reconverge_pop.value = 0
    dut.target_address.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    await ReadOnly()
    await Timer(1, unit="ns")


async def mask_edge(dut):
    await RisingEdge(dut.clk)
    await ReadOnly()
    await Timer(1, unit="ns")


async def mask_capture_predicate(dut, value: int, qual: int = 0xF):
    dut.predicate_out.value = value & 0xF
    dut.predicate_write_qualified.value = qual & 0xF
    await mask_edge(dut)
    dut.predicate_write_qualified.value = 0


async def mask_do_ifp(dut, target: int):
    dut.target_address.value = target & 0xF
    dut.command_ifp.value = 1
    await mask_edge(dut)
    dut.command_ifp.value = 0


async def mask_do_else(dut, target: int):
    dut.target_address.value = target & 0xF
    dut.command_else.value = 1
    await mask_edge(dut)
    dut.command_else.value = 0


async def mask_do_pop(dut):
    dut.command_reconverge_pop.value = 1
    await mask_edge(dut)
    dut.command_reconverge_pop.value = 0


def mask_maybe_internal(dut, name: str):
    try:
        return int(getattr(dut, name).value)
    except Exception:
        return None


@hierarchy_test("clm_mask_stack")
async def reset_predicate_capture_and_atomic_commands(dut):
    await mask_start(dut)

    assert int(dut.lane_active.value) == 0xF
    assert int(dut.stack_top_valid.value) == 0

    # Per-lane predicate capture: update one bit at a time, then consume the
    # stored vector with IFP.  This observes architectural behavior instead of
    # merely reading the predicate storage register.
    await mask_capture_predicate(dut, 0b0001, 0b0001)
    await mask_capture_predicate(dut, 0b0010, 0b0010)
    await mask_capture_predicate(dut, 0b0100, 0b0100)
    await mask_capture_predicate(dut, 0b1000, 0b1000)
    # Each qualified write carried a 1 in its own bit, so all stored bits are 1.
    await mask_do_ifp(dut, 5)
    assert int(dut.lane_active.value) == 0xF
    assert int(dut.stack_top_valid.value) == 1
    assert int(dut.stack_top_type.value) == 0  # ELSE token
    assert int(dut.stack_top_target.value) == 5

    # ELSE for all-true predicate switches to the empty false side and rewrites
    # the token in place as RECONVERGE, preserving the parent for the later pop.
    await mask_do_else(dut, 9)
    assert int(dut.lane_active.value) == 0x0
    assert int(dut.stack_top_valid.value) == 1
    assert int(dut.stack_top_type.value) == 1
    assert int(dut.stack_top_target.value) == 9
    await mask_do_pop(dut)
    assert int(dut.lane_active.value) == 0xF
    assert int(dut.stack_top_valid.value) == 0

    # Unqualified predicate bits hold.  Start from 1010, then attempt to replace
    # all bits with 0101 but qualify only lanes 0 and 2.  Expected stored value
    # becomes 1111? Let's derive it explicitly:
    # old 1010, new 0101 on bits 0&2 -> old b3=1,b1=1; new b2=1,b0=1 => 1111.
    await mask_capture_predicate(dut, 0b1010, 0xF)
    await mask_capture_predicate(dut, 0b0101, 0b0101)
    await mask_do_ifp(dut, 3)
    assert int(dut.lane_active.value) == 0xF
    await mask_do_else(dut, 4)
    assert int(dut.lane_active.value) == 0x0
    await mask_do_pop(dut)
    assert int(dut.lane_active.value) == 0xF


@hierarchy_test("clm_mask_stack")
async def exhaustive_ifp_partition_and_roundtrip(dut):
    """All parent masks x all predicates, using legal IFP->ELSE->POP flow."""
    # The module has no direct SETMASK operation, so construct each parent mask
    # by first splitting all lanes and selecting the desired true subset, then
    # perform the test split underneath it.  Reset between cases keeps the
    # construction unambiguous and also pounds reset semantics.
    await ensure_clock(dut, 20)

    for parent in range(16):
        for pred in range(16):
            dut.rst_n.value = 0
            dut.predicate_write_qualified.value = 0
            dut.command_ifp.value = 0
            dut.command_else.value = 0
            dut.command_reconverge_pop.value = 0
            await RisingEdge(dut.clk)
            dut.rst_n.value = 1
            await RisingEdge(dut.clk)
            await ReadOnly()
            await Timer(1, unit="ns")

            # Build parent mask as the true side of an outer IFP.
            dut.predicate_out.value = parent
            dut.predicate_write_qualified.value = 0xF
            await mask_edge(dut)
            dut.predicate_write_qualified.value = 0
            dut.target_address.value = 0xE
            dut.command_ifp.value = 1
            await mask_edge(dut)
            dut.command_ifp.value = 0
            assert int(dut.lane_active.value) == parent

            # Inner split under that parent.
            dut.predicate_out.value = pred
            dut.predicate_write_qualified.value = 0xF
            await mask_edge(dut)
            dut.predicate_write_qualified.value = 0
            dut.target_address.value = 0xA
            dut.command_ifp.value = 1
            await mask_edge(dut)
            dut.command_ifp.value = 0

            true_side = parent & pred
            false_side = parent & (~pred & 0xF)
            assert int(dut.lane_active.value) == true_side, (
                f"IFP true mask wrong parent={parent:04b} pred={pred:04b}"
            )
            assert int(dut.stack_top_valid.value) == 1
            assert int(dut.stack_top_type.value) == 0
            assert int(dut.stack_top_target.value) == 0xA
            internal_false = mask_maybe_internal(dut, "stack0_mask")
            if internal_false is not None:
                assert internal_false == false_side

            # ELSE must expose the complementary active slice and store the
            # exact parent in the rewritten reconvergence token.
            dut.target_address.value = 0xB
            dut.command_else.value = 1
            await mask_edge(dut)
            dut.command_else.value = 0
            assert int(dut.lane_active.value) == false_side
            assert int(dut.stack_top_type.value) == 1
            assert int(dut.stack_top_target.value) == 0xB
            internal_parent = mask_maybe_internal(dut, "stack0_mask")
            if internal_parent is not None:
                assert internal_parent == parent

            # POP restores exactly the parent and reveals the outer token.
            dut.command_reconverge_pop.value = 1
            await mask_edge(dut)
            dut.command_reconverge_pop.value = 0
            assert int(dut.lane_active.value) == parent
            assert int(dut.stack_top_valid.value) == 1  # outer token survives
            assert int(dut.stack_top_type.value) == 0
            assert int(dut.stack_top_target.value) == 0xE


@hierarchy_test("clm_mask_stack")
async def nesting_depth_priority_exports_and_illegal_overflow_behavior(dut):
    await mask_start(dut)

    # Outer split: parent F -> true 0011 / false 1100.
    await mask_capture_predicate(dut, 0b0011)
    await mask_do_ifp(dut, 12)
    assert int(dut.lane_active.value) == 0b0011
    assert int(dut.stack_top_target.value) == 12

    # Inner split under lanes 0,1: lane0 true, lane1 false.
    await mask_capture_predicate(dut, 0b0001)
    await mask_do_ifp(dut, 8)
    assert int(dut.lane_active.value) == 0b0001
    assert int(dut.stack_top_target.value) == 8
    if mask_maybe_internal(dut, "stack1_valid") is not None:
        assert mask_maybe_internal(dut, "stack1_valid") == 1
        assert mask_maybe_internal(dut, "stack1_target") == 12

    # Inner ELSE only changes top entry; outer token below must remain intact.
    await mask_do_else(dut, 10)
    assert int(dut.lane_active.value) == 0b0010
    assert int(dut.stack_top_type.value) == 1
    assert int(dut.stack_top_target.value) == 10
    if mask_maybe_internal(dut, "stack1_target") is not None:
        assert mask_maybe_internal(dut, "stack1_target") == 12

    await mask_do_pop(dut)
    assert int(dut.lane_active.value) == 0b0011
    assert int(dut.stack_top_valid.value) == 1
    assert int(dut.stack_top_type.value) == 0
    assert int(dut.stack_top_target.value) == 12

    await mask_do_else(dut, 14)
    assert int(dut.lane_active.value) == 0b1100
    assert int(dut.stack_top_type.value) == 1
    assert int(dut.stack_top_target.value) == 14
    await mask_do_pop(dut)
    assert int(dut.lane_active.value) == 0b1111
    assert int(dut.stack_top_valid.value) == 0

    # Command priority: pop > else > ifp.  Build a valid token first.
    await mask_capture_predicate(dut, 0b0101)
    await mask_do_ifp(dut, 6)
    before_mask = int(dut.lane_active.value)
    assert before_mask == 0b0101
    dut.command_reconverge_pop.value = 1
    dut.command_else.value = 1
    dut.command_ifp.value = 1
    dut.target_address.value = 3
    await mask_edge(dut)
    dut.command_reconverge_pop.value = 0
    dut.command_else.value = 0
    dut.command_ifp.value = 0
    # Direct pop of an ELSE token is architecturally illegal, but isolated unit
    # priority is still deterministic: pop wins and loads its saved false mask.
    assert int(dut.lane_active.value) == 0b1010

    # No command means all valid control state holds.  The just-performed pop
    # exposed an invalid entry whose data fields are intentionally unreset, so
    # first create a fresh valid token; the test must never inspect invalid
    # mask/target/type storage as though it were architectural state.
    await mask_capture_predicate(dut, 0xF)
    await mask_do_ifp(dut, 7)
    state = (
        int(dut.lane_active.value), int(dut.stack_top_valid.value),
        int(dut.stack_top_type.value), int(dut.stack_top_target.value),
    )
    await mask_edge(dut)
    state2 = (
        int(dut.lane_active.value), int(dut.stack_top_valid.value),
        int(dut.stack_top_type.value), int(dut.stack_top_target.value),
    )
    assert state2 == state

    # Third nested IFP is outside the architectural depth-2 contract.  The
    # current implementation intentionally behaves as a two-entry shift stack:
    # a third push drops the oldest bottom entry but must leave the new top sane.
    dut.rst_n.value = 0
    await mask_edge(dut)
    dut.rst_n.value = 1
    await mask_edge(dut)
    for pred, target in ((0xE, 1), (0xC, 2), (0x8, 3)):
        await mask_capture_predicate(dut, pred)
        await mask_do_ifp(dut, target)
    assert int(dut.stack_top_valid.value) == 1
    assert int(dut.stack_top_type.value) == 0
    assert int(dut.stack_top_target.value) == 3
    # Pop twice must remain structurally coherent even though the oldest token
    # has been discarded; this is implementation behavior, not valid software.
    await mask_do_pop(dut)
    assert int(dut.stack_top_valid.value) == 1
    assert int(dut.stack_top_target.value) == 2
    await mask_do_pop(dut)
    assert int(dut.stack_top_valid.value) == 0


@hierarchy_test("clm_mask_stack")
async def commands_work_even_with_zero_active_mask(dut):
    await mask_start(dut)

    # All-false IFP drives current mask to zero and leaves an ELSE token whose
    # saved mask is the complete parent.
    await mask_capture_predicate(dut, 0)
    await mask_do_ifp(dut, 5)
    assert int(dut.lane_active.value) == 0
    assert int(dut.stack_top_type.value) == 0

    # Global ELSE must execute despite current_mask==0 and wake the false side.
    await mask_do_else(dut, 9)
    assert int(dut.lane_active.value) == 0xF
    assert int(dut.stack_top_type.value) == 1
    await mask_do_pop(dut)
    assert int(dut.lane_active.value) == 0xF
    assert int(dut.stack_top_valid.value) == 0


# ===========================================================================
# TEST_FETCH_SEQ  (REAL HIERARCHY: core.fetch_seq)
# ===========================================================================

async def fetch_start(dut):
    await ensure_clock(dut, 20)
    dut.rst_n.value = 0
    dut.go.value = 0
    dut.instruction_shift_enable.value = 0
    dut.instruction_shift_data.value = 0
    dut.rs_address.value = 0
    dut.rt_address.value = 1
    dut.bank_conflict.value = 0
    dut.is_halt.value = 0
    dut.any_lane_active.value = 1
    dut.stack_top_valid.value = 0
    dut.stack_top_type.value = 0
    dut.stack_top_target.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    await ReadOnly()
    await Timer(1, unit="ns")


async def fetch_edge(dut):
    await RisingEdge(dut.clk)
    await ReadOnly()
    await Timer(1, unit="ns")


async def fetch_upload_word(dut, word: int):
    dut.instruction_shift_data.value = word & 0xFFFF
    dut.instruction_shift_enable.value = 1
    await fetch_edge(dut)
    dut.instruction_shift_enable.value = 0


async def fetch_upload_image(dut, words):
    assert len(words) == 16
    for w in words:
        await fetch_upload_word(dut, w)
    await Timer(1, unit="ns")


async def fetch_go(dut):
    dut.go.value = 1
    await fetch_edge(dut)
    dut.go.value = 0
    await Timer(1, unit="ns")


def fetch_pc(dut):
    try:
        return int(dut.logical_pc.value)
    except Exception:
        return None


@hierarchy_test("clm_fetch_seq")
async def reset_upload_order_and_warm_restart_contract(dut):
    await fetch_start(dut)
    assert int(dut.done.value) == 1
    assert int(dut.replay_state.value) == 0
    if fetch_pc(dut) is not None:
        assert fetch_pc(dut) == 0
    assert int(dut.instruction_commit.value) == 0

    words = [0x1000 + i for i in range(15)] + [0xF000]
    await fetch_upload_image(dut, words)
    assert int(dut.current_instruction.value) == words[0]
    assert int(dut.done.value) == 1

    # A partial replacement is deliberately just a physical shift; there is no
    # length guard or auto-correction in the sequencer.
    await fetch_upload_word(dut, 0xBEEF)
    assert int(dut.current_instruction.value) == words[1]

    # Reload a clean complete image for execution.
    await fetch_upload_image(dut, words)
    assert int(dut.current_instruction.value) == words[0]

    await fetch_go(dut)
    assert int(dut.done.value) == 0
    assert int(dut.replay_state.value) == 0
    if fetch_pc(dut) is not None:
        assert fetch_pc(dut) == 0

    # Straight-line 16-word run.  HALT is commit-qualified only on the final
    # word.  The HALT edge itself performs the 16th physical rotation.
    for i in range(16):
        dut.is_halt.value = int(i == 15)
        assert int(dut.current_instruction.value) == words[i]
        assert int(dut.instruction_commit.value) == 1
        await fetch_edge(dut)

    assert int(dut.done.value) == 1
    assert int(dut.current_instruction.value) == words[0]
    if fetch_pc(dut) is not None:
        assert fetch_pc(dut) == 0

    # Bare GO reruns without upload because the previous run restored ring
    # orientation.  Prove the mouth still starts at instruction zero.
    dut.is_halt.value = 0
    await fetch_go(dut)
    assert int(dut.current_instruction.value) == words[0]
    assert int(dut.done.value) == 0










@hierarchy_test("clm_fetch_seq")
async def cross_cutting_cycle_invariants(dut):
    """Stress a mixed schedule and assert sequencer invariants every cycle."""
    await fetch_start(dut)
    words = [0x5000 + i for i in range(15)] + [0xF000]
    await fetch_upload_image(dut, words)
    await fetch_go(dut)

    # Deterministic stimulus pattern deliberately creates normal commits,
    # conflicts/replays, empty scans, and reconvergence bubbles.
    previous_pc = fetch_pc(dut)
    previous_instr = int(dut.current_instruction.value)

    for cycle in range(80):
        phase = cycle % 10
        dut.is_halt.value = 0
        dut.instruction_shift_enable.value = 0
        dut.stack_top_valid.value = 0
        dut.any_lane_active.value = 1
        dut.bank_conflict.value = 0

        if phase in (2, 3):
            dut.rs_address.value = 3
            dut.rt_address.value = 5
            dut.bank_conflict.value = 1
        elif phase in (5, 6):
            dut.any_lane_active.value = 0
        elif phase == 8:
            dut.stack_top_valid.value = 1
            dut.stack_top_type.value = 1
            current_pc = fetch_pc(dut)
            dut.stack_top_target.value = 0 if current_pc is None else current_pc
            dut.bank_conflict.value = 1  # reconverge must beat it

        await Timer(1, unit="ns")
        assert not (int(dut.operand_hold_load.value) and int(dut.operand_hold_use.value))
        if int(dut.done.value):
            assert int(dut.instruction_commit.value) == 0
        if int(dut.instruction_shift_enable.value):
            assert int(dut.instruction_commit.value) == 0

        commit = int(dut.instruction_commit.value)
        pop = int(dut.command_reconverge_pop.value)
        hold_load = int(dut.operand_hold_load.value)
        replay = int(dut.replay_state.value)
        active = int(dut.any_lane_active.value)
        token_valid = int(dut.stack_top_valid.value)
        token_type = int(dut.stack_top_type.value)
        pc_before = fetch_pc(dut)
        instr_before = int(dut.current_instruction.value)

        await fetch_edge(dut)

        pc_after = fetch_pc(dut)
        instr_after = int(dut.current_instruction.value)

        # Bubble/capture must park both ring and PC.  A scan or commit may move
        # both.  This checks behavior, not the internal execution_advance wire.
        if hold_load or pop:
            assert instr_after == instr_before
            if pc_before is not None:
                assert pc_after == pc_before

        previous_pc = pc_after
        previous_instr = instr_after

        # Stop before accidental wrap/forever scan makes this diagnostic test
        # unrelated to the intended mixed schedule.
        if int(dut.done.value):
            break




# ===========================================================================
# TEST_MULTIPLIER  (REAL HIERARCHY: core.lane0.lane_alu.engine_multiplier)
# ===========================================================================

@hierarchy_test("clm_mult_bw")
async def exhaustive_signed_8x8_product_space(dut):
    """Every signed int8 pair against mathematical two's-complement product."""
    vals = exhaustive_values()
    for a in vals:
        for b in vals:
            dut.a.value = a
            dut.b.value = b
            await Timer(1, unit="ns")
            expected = u16(s8(a) * s8(b))
            got = int(dut.product.value)
            assert got == expected, (
                f"signed multiply {s8(a)} * {s8(b)} expected 0x{expected:04X}, got 0x{got:04X}"
            )


# ===========================================================================
# TEST_ALU  (REAL HIERARCHY: core.lane0.lane_alu)
# ===========================================================================

# This bench deliberately drives semantic operations into the ALU.  It does not
# duplicate the implementation's box equations.  The golden results are the ISA
# meanings: signed 8-bit add/sub/multiply, logical shifts, bitwise operations,
# signed comparisons, and 16-bit wrapping accumulation.


async def alu_start(dut):
    await ensure_clock(dut, 20)
    alu_drive_inert(dut)
    await Timer(2, unit="ns")
    # Accumulator intentionally has no reset in RTL; every independent ALU test
    # establishes its own architectural starting state instead of inheriting a
    # previous test's value.
    await alu_clear_acc(dut)
    alu_drive_inert(dut)
    await Timer(1, unit="ns")


async def alu_stable_edge(dut):
    await RisingEdge(dut.clk)
    await ReadOnly()
    await Timer(1, unit="ns")


def alu_drive_inert(dut):
    dut.highway_left.value = 0
    dut.highway_right.value = 0
    dut.immediate_value.value = 0
    dut.select_immediate.value = 0
    dut.force_one_box1.value = 0
    dut.force_one_box2.value = 0
    dut.swap_operands.value = 0
    dut.subtract_prepare.value = 0
    dut.prepare_zero.value = 0
    dut.select_accumulator.value = 0
    dut.shift_direction.value = 0
    dut.bitwise_select.value = 0
    dut.condition_select.value = 0
    dut.accumulator_clear.value = 0
    dut.accumulator_load.value = 0
    dut.accumulator_mac_capture.value = 0
    dut.accumulator_half_select.value = 0
    dut.ldac_select_highway_right.value = 0
    alu_set_writeback(dut, 0)


def alu_set_writeback(dut, which: int):
    """Select architectural writeback source.

    Current Clementine uses encoded writeback_select.  The small compatibility
    branch lets the same semantic bench run against an older ALU checkout that
    exposed the four one-hot writeback enables, without changing the oracle.
    """
    if hasattr(dut, "writeback_select"):
        dut.writeback_select.value = which & 3
    else:
        dut.writeback_enable_arithmetic.value = int(which == 0)
        dut.writeback_enable_shift.value = int(which == 1)
        dut.writeback_enable_bitwise.value = int(which == 2)
        dut.writeback_enable_mvac.value = int(which == 3)


def alu_accumulator(dut) -> int:
    if hasattr(dut, "accumulator_value"):
        return int(dut.accumulator_value.value)
    return int(dut.alu_accumulator.value)


async def alu_clear_acc(dut):
    alu_drive_inert(dut)
    dut.accumulator_clear.value = 1
    await alu_stable_edge(dut)
    dut.accumulator_clear.value = 0
    assert alu_accumulator(dut) == 0


async def alu_load_acc_half(dut, value: int, high: int, source_on_right: int = 0):
    alu_drive_inert(dut)
    dut.highway_left.value = value & 0xFF if not source_on_right else 0xA5
    dut.highway_right.value = value & 0xFF if source_on_right else 0x5A
    dut.ldac_select_highway_right.value = source_on_right
    dut.accumulator_half_select.value = high
    dut.accumulator_load.value = 1
    await alu_stable_edge(dut)
    dut.accumulator_load.value = 0


def alu_drive_add(dut, rs: int, rt: int, reversed_route: bool = False):
    alu_drive_inert(dut)
    # Physical placement differs, logical operation does not.
    if reversed_route:
        dut.highway_left.value = rt
        dut.highway_right.value = rs
    else:
        dut.highway_left.value = rs
        dut.highway_right.value = rt
    dut.force_one_box2.value = 1
    alu_set_writeback(dut, 0)


def alu_drive_sub_or_cmp(dut, rs: int, rt: int, reversed_route: bool = False):
    alu_drive_inert(dut)
    if reversed_route:
        dut.highway_left.value = rt
        dut.highway_right.value = rs
        dut.force_one_box1.value = 1
        dut.swap_operands.value = 1
    else:
        dut.highway_left.value = rs
        dut.highway_right.value = rt
        dut.force_one_box2.value = 1
        dut.swap_operands.value = 0
    dut.subtract_prepare.value = 1
    alu_set_writeback(dut, 0)


@hierarchy_test("clm_alu")
async def exhaustive_add_and_routing_equivalence(dut):
    await alu_start(dut)
    vals = exhaustive_values()
    for a in vals:
        for b in vals:
            expected = u8(a + b)
            alu_drive_add(dut, a, b, False)
            await Timer(1, unit="ns")
            got_normal = int(dut.writeback_bus.value)
            assert got_normal == expected, f"ADD {s8(a)}+{s8(b)} expected 0x{expected:02X}, got 0x{got_normal:02X}"

            alu_drive_add(dut, a, b, True)
            await Timer(1, unit="ns")
            got_reversed = int(dut.writeback_bus.value)
            assert got_reversed == expected, f"ADD reversed route {s8(a)}+{s8(b)} wrong"


@hierarchy_test("clm_alu")
async def exhaustive_subtraction_both_physical_routings(dut):
    await alu_start(dut)
    vals = exhaustive_values()
    for a in vals:
        for b in vals:
            expected = u8(a - b)
            for reversed_route in (False, True):
                alu_drive_sub_or_cmp(dut, a, b, reversed_route)
                await Timer(1, unit="ns")
                got = int(dut.writeback_bus.value)
                assert got == expected, (
                    f"SUB route={'REV' if reversed_route else 'NORMAL'} "
                    f"{s8(a)}-{s8(b)} expected 0x{expected:02X}, got 0x{got:02X}"
                )

    # Explicit off-by-one and signed-edge diagnostics: these make failures easy
    # to understand even though the exhaustive sweep above already covers them.
    for a, b in ((0, 1), (1, 1), (0x80, 1), (0x7F, 0xFF), (0, 0x80)):
        alu_drive_sub_or_cmp(dut, a, b, False)
        await Timer(1, unit="ns")
        assert int(dut.writeback_bus.value) == u8(a - b)


@hierarchy_test("clm_alu")
async def exhaustive_signed_compare_truth_table_and_routing(dut):
    await alu_start(dut)
    vals = exhaustive_values()
    for a in vals:
        for b in vals:
            for cond in range(4):
                expected = int(signed_cmp8(a, b, cond))
                for reversed_route in (False, True):
                    alu_drive_sub_or_cmp(dut, a, b, reversed_route)
                    dut.condition_select.value = cond
                    await Timer(1, unit="ns")
                    got = int(dut.predicate_out.value)
                    assert got == expected, (
                        f"CMP cond={cond} route={'REV' if reversed_route else 'NORMAL'} "
                        f"a={s8(a)} b={s8(b)} expected {expected}, got {got}"
                    )

    # Named signed/unsigned disagreement cases.
    for a, b in ((0xFF, 1), (0x80, 0x7F), (0x7F, 0x80), (0, 0)):
        for cond in range(4):
            alu_drive_sub_or_cmp(dut, a, b, False)
            dut.condition_select.value = cond
            await Timer(1, unit="ns")
            assert int(dut.predicate_out.value) == int(signed_cmp8(a, b, cond))


@hierarchy_test("clm_alu")
async def exhaustive_bitwise_logic_and_writeback_isolation(dut):
    await alu_start(dut)
    vals = exhaustive_values()
    operations = {
        0: lambda a, b: a & b,
        1: lambda a, b: a | b,
        2: lambda a, b: a ^ b,
        3: lambda a, b: a ^ b,  # 1x encodes XOR
    }
    for select, fn in operations.items():
        for a in vals:
            for b in vals:
                alu_drive_inert(dut)
                dut.highway_left.value = a
                dut.highway_right.value = b
                dut.bitwise_select.value = select
                alu_set_writeback(dut, 2)
                await Timer(1, unit="ns")
                expected = u8(fn(a, b))
                assert int(dut.writeback_bus.value) == expected

                # Commutativity makes physical highway reversal immaterial.
                dut.highway_left.value = b
                dut.highway_right.value = a
                await Timer(1, unit="ns")
                assert int(dut.writeback_bus.value) == expected


@hierarchy_test("clm_alu")
async def exhaustive_logical_shifter_all_bytes_amounts_directions_and_routes(dut):
    await alu_start(dut)
    values = exhaustive_values()
    amount_bytes = exhaustive_values()
    for data in values:
        for amount_byte in amount_bytes:
            amount = amount_byte & 7
            for direction in (0, 1):
                expected = u8((data >> amount) if direction else (data << amount))
                for reversed_route in (False, True):
                    alu_drive_inert(dut)
                    if reversed_route:
                        dut.highway_left.value = amount_byte
                        dut.highway_right.value = data
                        dut.swap_operands.value = 1
                    else:
                        dut.highway_left.value = data
                        dut.highway_right.value = amount_byte
                        dut.swap_operands.value = 0
                    dut.shift_direction.value = direction
                    alu_set_writeback(dut, 1)
                    await Timer(1, unit="ns")
                    got = int(dut.writeback_bus.value)
                    assert got == expected, (
                        f"SHIFT dir={direction} data=0x{data:02X} amtbyte=0x{amount_byte:02X} "
                        f"route={'REV' if reversed_route else 'NORMAL'} expected=0x{expected:02X} got=0x{got:02X}"
                    )


@hierarchy_test("clm_alu")
async def exhaustive_mov_ldi_and_forced_positive_one_semantics(dut):
    await alu_start(dut)
    vals = exhaustive_values()

    for value in vals:
        # MOV source on the even/left highway.
        alu_drive_inert(dut)
        dut.highway_left.value = value
        dut.highway_right.value = 0xD3
        dut.force_one_box2.value = 1
        dut.prepare_zero.value = 1
        alu_set_writeback(dut, 0)
        await Timer(1, unit="ns")
        assert int(dut.writeback_bus.value) == value

        # MOV source on the odd/right highway.
        alu_drive_inert(dut)
        dut.highway_left.value = 0x2C
        dut.highway_right.value = value
        dut.force_one_box1.value = 1
        dut.prepare_zero.value = 1
        alu_set_writeback(dut, 0)
        await Timer(1, unit="ns")
        assert int(dut.writeback_bus.value) == value

        # LDI must be immune to junk on both register highways.
        for junk_l, junk_r in ((0x00, 0xFF), (0xA5, 0x5A), (0x80, 0x7F)):
            alu_drive_inert(dut)
            dut.highway_left.value = junk_l
            dut.highway_right.value = junk_r
            dut.immediate_value.value = value
            dut.select_immediate.value = 1
            dut.force_one_box2.value = 1
            dut.prepare_zero.value = 1
            alu_set_writeback(dut, 0)
            await Timer(1, unit="ns")
            assert int(dut.writeback_bus.value) == value

    # Negative pass-through is the strongest proof that force-one means +1,
    # not 0xFF/-1.
    for value in (0x80, 0x81, 0xFE, 0xFF):
        alu_drive_inert(dut)
        dut.highway_left.value = value
        dut.force_one_box2.value = 1
        dut.prepare_zero.value = 1
        alu_set_writeback(dut, 0)
        await Timer(1, unit="ns")
        assert int(dut.writeback_bus.value) == value


@hierarchy_test("clm_alu")
async def exhaustive_mac_products_then_chains_wrap_and_accumulator_management(dut):
    await alu_start(dut)
    vals = exhaustive_values()

    # Every signed 8x8 product, captured from a cleared accumulator.  This is an
    # end-to-end multiplier+adder+accumulator test, not a test of internal pp bits.
    for a in vals:
        for b in vals:
            await alu_clear_acc(dut)
            alu_drive_inert(dut)
            dut.highway_left.value = a
            dut.highway_right.value = b
            dut.select_accumulator.value = 1
            dut.accumulator_mac_capture.value = 1
            await alu_stable_edge(dut)
            dut.accumulator_mac_capture.value = 0
            expected = signed_product8(a, b)
            got = alu_accumulator(dut)
            assert got == expected, (
                f"MAC from zero {s8(a)}*{s8(b)} expected 0x{expected:04X}, got 0x{got:04X}"
            )

    # Running dot product deliberately exceeds one byte so both MVAC halves are meaningful.
    await alu_clear_acc(dut)
    total = 0
    chain = [(12, 13), (0xF8, 7), (127, 2), (0x80, 0xFF)]
    for a, b in chain:
        alu_drive_inert(dut)
        dut.highway_left.value = a
        dut.highway_right.value = b
        dut.select_accumulator.value = 1
        dut.accumulator_mac_capture.value = 1
        await alu_stable_edge(dut)
        dut.accumulator_mac_capture.value = 0
        total = u16(total + s8(a) * s8(b))
        assert alu_accumulator(dut) == total

    # Explicit modulo-2^16 accumulation wrap.
    await alu_clear_acc(dut)
    # 5 * 16384 = 81920 -> 0x4000 modulo 65536.
    for _ in range(5):
        alu_drive_inert(dut)
        dut.highway_left.value = 0x80
        dut.highway_right.value = 0x80
        dut.select_accumulator.value = 1
        dut.accumulator_mac_capture.value = 1
        await alu_stable_edge(dut)
        dut.accumulator_mac_capture.value = 0
    assert alu_accumulator(dut) == u16(5 * 16384)

    # LDAC writes exactly one half and selects the proper physical highway.
    await alu_clear_acc(dut)
    await alu_load_acc_half(dut, 0x34, high=0, source_on_right=0)
    assert alu_accumulator(dut) == 0x0034
    await alu_load_acc_half(dut, 0xAB, high=1, source_on_right=1)
    assert alu_accumulator(dut) == 0xAB34

    # MVAC round-trip of each half.
    alu_drive_inert(dut)
    dut.accumulator_half_select.value = 0
    alu_set_writeback(dut, 3)
    await Timer(1, unit="ns")
    assert int(dut.writeback_bus.value) == 0x34
    dut.accumulator_half_select.value = 1
    await Timer(1, unit="ns")
    assert int(dut.writeback_bus.value) == 0xAB

    # LDAC -> MAC starts from loaded 16-bit state.
    await alu_clear_acc(dut)
    await alu_load_acc_half(dut, 0xF0, high=0, source_on_right=0)
    await alu_load_acc_half(dut, 0x01, high=1, source_on_right=1)
    assert alu_accumulator(dut) == 0x01F0
    alu_drive_inert(dut)
    dut.highway_left.value = 3
    dut.highway_right.value = 4
    dut.select_accumulator.value = 1
    dut.accumulator_mac_capture.value = 1
    await alu_stable_edge(dut)
    dut.accumulator_mac_capture.value = 0
    assert alu_accumulator(dut) == 0x01FC

    # No action means hold across multiple edges.
    before = alu_accumulator(dut)
    alu_drive_inert(dut)
    for _ in range(4):
        await alu_stable_edge(dut)
    assert alu_accumulator(dut) == before


@hierarchy_test("clm_alu")
async def accumulator_update_priority_and_writeback_source_exclusivity(dut):
    await alu_start(dut)
    await alu_clear_acc(dut)
    await alu_load_acc_half(dut, 0xAA, 0, 0)
    await alu_load_acc_half(dut, 0x55, 1, 1)
    assert alu_accumulator(dut) == 0x55AA

    # Simultaneously request clear/load/mac.  Architectural priority is clear > load > mac.
    alu_drive_inert(dut)
    dut.highway_left.value = 7
    dut.highway_right.value = 9
    dut.accumulator_clear.value = 1
    dut.accumulator_load.value = 1
    dut.accumulator_mac_capture.value = 1
    dut.select_accumulator.value = 1
    await alu_stable_edge(dut)
    assert alu_accumulator(dut) == 0

    # Load beats MAC when clear is absent, preserving the unselected half.
    await alu_load_acc_half(dut, 0x12, 1, 0)
    assert alu_accumulator(dut) == 0x1200
    alu_drive_inert(dut)
    dut.highway_left.value = 0x34
    dut.highway_right.value = 3
    dut.accumulator_load.value = 1
    dut.accumulator_half_select.value = 0
    dut.ldac_select_highway_right.value = 0
    dut.accumulator_mac_capture.value = 1
    dut.select_accumulator.value = 1
    await alu_stable_edge(dut)
    assert alu_accumulator(dut) == 0x1234

    # The writeback selector must expose exactly the semantic source selected.
    # Arithmetic
    alu_drive_add(dut, 11, 7, False)
    alu_set_writeback(dut, 0)
    await Timer(1, unit="ns")
    assert int(dut.writeback_bus.value) == 18
    # Shift
    alu_drive_inert(dut)
    dut.highway_left.value = 0x81
    dut.highway_right.value = 1
    dut.shift_direction.value = 1
    alu_set_writeback(dut, 1)
    await Timer(1, unit="ns")
    assert int(dut.writeback_bus.value) == 0x40
    # Bitwise
    alu_drive_inert(dut)
    dut.highway_left.value = 0xA5
    dut.highway_right.value = 0x0F
    dut.bitwise_select.value = 0
    alu_set_writeback(dut, 2)
    await Timer(1, unit="ns")
    assert int(dut.writeback_bus.value) == 0x05
    # MVAC
    alu_drive_inert(dut)
    dut.accumulator_half_select.value = 0
    alu_set_writeback(dut, 3)
    await Timer(1, unit="ns")
    assert int(dut.writeback_bus.value) == 0x34


# ===========================================================================
# TEST_LANE  (REAL HIERARCHY: core.lane0)
# ===========================================================================

async def lane_stable_edge(dut):
    await RisingEdge(dut.clk)
    await ReadOnly()
    await Timer(1, unit="ns")


def lane_inert(dut):
    dut.read_row_even.value = 0
    dut.read_row_odd.value = 0
    dut.conflict_bank_select.value = 0
    dut.rd_address.value = 0
    dut.immediate_value.value = 0
    dut.operand_hold_load.value = 0
    dut.operand_hold_use.value = 0
    dut.instruction_commit.value = 0
    dut.register_write_enable.value = 0
    dut.predicate_write_enable.value = 0
    dut.select_immediate.value = 0
    dut.force_one_box1.value = 0
    dut.force_one_box2.value = 0
    dut.swap_operands.value = 0
    dut.laneid_mode.value = 0
    dut.subtract_prepare.value = 0
    dut.prepare_zero.value = 0
    dut.select_accumulator.value = 0
    dut.shift_direction.value = 0
    dut.bitwise_select.value = 0
    dut.condition_select.value = 0
    dut.accumulator_clear.value = 0
    dut.accumulator_load.value = 0
    dut.accumulator_mac_capture.value = 0
    dut.accumulator_half_select.value = 0
    dut.ldac_select_highway_right.value = 0
    dut.writeback_select.value = 0
    dut.lane_active.value = 1


async def lane_start(dut):
    await ensure_clock(dut, 20)
    lane_inert(dut)
    await Timer(2, unit="ns")

    # The lane datapath is intentionally unreset.  Each independent lane test
    # therefore initializes all architectural storage through REAL lane control
    # operations before checking behavior.
    for reg in range(1, 8):
        await lane_ldi(dut, reg, 0)
    await lane_clear_acc(dut)

    # operand_hold is also intentionally unreset. Capture grounded R0 once so a
    # later replay test cannot inherit a value from an earlier test.
    lane_inert(dut)
    dut.read_row_even.value = 0
    dut.conflict_bank_select.value = 0
    dut.operand_hold_load.value = 1
    await lane_stable_edge(dut)
    lane_inert(dut)
    await Timer(1, unit="ns")


async def lane_read_reg(dut, reg: int) -> int:
    row = (reg >> 1) & 3
    if reg & 1:
        dut.read_row_odd.value = row
        await Timer(1, unit="ns")
        return int(dut.lane_regfile.read_data_odd.value)
    dut.read_row_even.value = row
    await Timer(1, unit="ns")
    return int(dut.lane_regfile.read_data_even.value)


async def lane_ldi(dut, reg: int, value: int, active: int = 1, commit: int = 1):
    lane_inert(dut)
    dut.lane_active.value = active
    dut.instruction_commit.value = commit
    dut.register_write_enable.value = 1
    dut.rd_address.value = reg
    dut.immediate_value.value = value & 0xFF
    dut.select_immediate.value = 1
    dut.force_one_box2.value = 1
    dut.prepare_zero.value = 1
    dut.writeback_select.value = 0
    await lane_stable_edge(dut)
    lane_inert(dut)


async def lane_clear_acc(dut, active: int = 1, commit: int = 1):
    lane_inert(dut)
    dut.lane_active.value = active
    dut.instruction_commit.value = commit
    dut.accumulator_clear.value = 1
    await lane_stable_edge(dut)
    lane_inert(dut)


async def lane_ldac_from_reg(dut, reg: int, high: int = 0, active: int = 1, commit: int = 1):
    lane_inert(dut)
    dut.lane_active.value = active
    dut.instruction_commit.value = commit
    dut.accumulator_load.value = 1
    dut.accumulator_half_select.value = high
    dut.ldac_select_highway_right.value = reg & 1
    row = (reg >> 1) & 3
    if reg & 1:
        dut.read_row_odd.value = row
    else:
        dut.read_row_even.value = row
    await Timer(1, unit="ns")
    await lane_stable_edge(dut)
    lane_inert(dut)


async def lane_laneid_write(dut, reg: int, active: int = 1, commit: int = 1):
    lane_inert(dut)
    dut.lane_active.value = active
    dut.instruction_commit.value = commit
    dut.register_write_enable.value = 1
    dut.rd_address.value = reg
    dut.laneid_mode.value = 1
    # Make the ordinary ALU result deliberately nonzero.  The lane-ID source
    # must override it at the wrapper writeback injection point.
    dut.immediate_value.value = 0xA5
    dut.select_immediate.value = 1
    dut.force_one_box2.value = 1
    dut.prepare_zero.value = 1
    await lane_stable_edge(dut)
    lane_inert(dut)


async def lane_capture_replay_binary(dut, rs: int, rt: int, rd: int, op: str):
    """Perform the architecturally specified two-cycle same-bank replay.

    Capture reads logical rs from the conflicted bank without committing.
    Replay reads logical rt live, places held rs on the left resolved highway,
    and commits exactly once.
    """
    assert (rs & 1) == (rt & 1)
    rs_val = await lane_read_reg(dut, rs)
    rt_val = await lane_read_reg(dut, rt)

    before_rd = await lane_read_reg(dut, rd)
    before_acc = int(dut.accumulator_value.value)
    lane_inert(dut)
    dut.conflict_bank_select.value = rs & 1
    if rs & 1:
        dut.read_row_odd.value = (rs >> 1) & 3
    else:
        dut.read_row_even.value = (rs >> 1) & 3
    dut.operand_hold_load.value = 1
    dut.instruction_commit.value = 0
    # Deliberately request forbidden architectural updates during capture;
    # lane_commit must suppress every one except the temporary operand hold.
    dut.register_write_enable.value = 1
    dut.rd_address.value = rd
    dut.accumulator_clear.value = 1
    await lane_stable_edge(dut)
    assert int(dut.operand_hold.value) == rs_val
    assert await lane_read_reg(dut, rd) == before_rd
    assert int(dut.accumulator_value.value) == before_acc

    lane_inert(dut)
    dut.conflict_bank_select.value = rs & 1
    if rt & 1:
        dut.read_row_odd.value = (rt >> 1) & 3
    else:
        dut.read_row_even.value = (rt >> 1) & 3
    dut.operand_hold_use.value = 1
    dut.instruction_commit.value = 1
    dut.register_write_enable.value = 1
    dut.rd_address.value = rd
    dut.prepare_zero.value = 0
    dut.writeback_select.value = 0
    dut.swap_operands.value = 0  # replay contract: logical rs already left, rt right
    if op == "add":
        dut.force_one_box2.value = 1
        expected = u8(rs_val + rt_val)
    elif op == "sub":
        dut.force_one_box2.value = 1
        dut.subtract_prepare.value = 1
        expected = u8(rs_val - rt_val)
    else:
        raise ValueError(op)
    await Timer(1, unit="ns")
    # Resolved highways themselves are useful diagnostics, not the oracle.
    assert int(dut.highway_left.value) == rs_val
    assert int(dut.highway_right.value) == rt_val
    await lane_stable_edge(dut)
    lane_inert(dut)
    assert await lane_read_reg(dut, rd) == expected
    return expected








@hierarchy_test("clm_lane")
async def no_architectural_state_changes_when_commit_is_low(dut):
    await lane_start(dut)
    for reg in range(1, 8):
        await lane_ldi(dut, reg, 0x10 + reg)
    await lane_clear_acc(dut)
    await lane_ldac_from_reg(dut, 1, 0)
    before_regs = [await lane_read_reg(dut, r) for r in range(8)]
    before_acc = int(dut.accumulator_value.value)

    # Pound every state-changing request for several cycles with commit low.
    lane_inert(dut)
    dut.instruction_commit.value = 0
    dut.lane_active.value = 1
    dut.register_write_enable.value = 1
    dut.predicate_write_enable.value = 1
    dut.rd_address.value = 7
    dut.immediate_value.value = 0xEE
    dut.select_immediate.value = 1
    dut.force_one_box2.value = 1
    dut.prepare_zero.value = 1
    dut.accumulator_clear.value = 1
    dut.accumulator_load.value = 1
    dut.accumulator_mac_capture.value = 1
    for _ in range(5):
        assert int(dut.predicate_write_qualified.value) == 0
        await lane_stable_edge(dut)
    lane_inert(dut)

    after_regs = [await lane_read_reg(dut, r) for r in range(8)]
    assert after_regs == before_regs
    assert int(dut.accumulator_value.value) == before_acc


# ===========================================================================
# TEST_SPI_HOST  (REAL HIERARCHY: core.spi_host)
# ===========================================================================

async def spi_stable_edge(dut):
    await RisingEdge(dut.clk)
    await ReadOnly()
    await Timer(1, unit="ns")


async def spi_reset_spi(dut):
    await ensure_clock(dut, 20)
    dut.spi_sclk.value = 0
    dut.spi_mosi.value = 0
    dut.spi_cs_n.value = 1
    dut.sequencer_done.value = 1
    dut.lane0_accumulator.value = 0x0123
    dut.lane1_accumulator.value = 0x4567
    dut.lane2_accumulator.value = 0x89AB
    dut.lane3_accumulator.value = 0xCDEF
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 4)
    await Timer(1, unit="ns")


def spi_expected_status(done: int, count: int) -> int:
    return ((count & 0x1F) << 2) | ((0 if done else 1) << 1) | (1 if done else 0)


async def spi_record_pulse(dut, signal_name: str, stop, samples=None):
    sig = getattr(dut, signal_name)
    widths = []
    current_width = 0
    high_samples = []
    while not stop[0]:
        await RisingEdge(dut.clk)
        await ReadOnly()
        value = int(sig.value)
        if value:
            current_width += 1
            if samples is not None:
                high_samples.append(int(getattr(dut, samples).value))
        elif current_width:
            widths.append(current_width)
            current_width = 0
    if current_width:
        widths.append(current_width)
    return widths, high_samples


async def spi_transact_with_pulse_record(spi, signal_name: str, command: int, word: int = 0, **kwargs):
    stop = [False]
    sample_name = "instruction_shift_data" if signal_name == "instruction_shift_enable" else None
    monitor = cocotb.start_soon(spi_record_pulse(spi.dut, signal_name, stop, sample_name))
    rx = await spi.transaction(command, word, **kwargs)
    stop[0] = True
    await RisingEdge(spi.dut.clk)
    widths, samples = await monitor
    return rx, widths, samples


async def spi_command_only_with_pulse_record(spi, signal_name: str, command: int, **kwargs):
    stop = [False]
    monitor = cocotb.start_soon(spi_record_pulse(spi.dut, signal_name, stop))
    await spi.command_only(command, **kwargs)
    stop[0] = True
    await RisingEdge(spi.dut.clk)
    widths, samples = await monitor
    return widths, samples


@hierarchy_test("clm_spi_host")
async def reset_synchronizers_and_no_phantom_edges(dut):
    await spi_reset_spi(dut)
    assert int(dut.sclk_sync.value) == 0
    assert int(dut.cs_n_sync.value) == 0b111
    assert int(dut.mosi_sync.value) == 0
    assert int(dut.transaction_counter.value) == 0
    assert int(dut.command_register.value) == SPI_CMD_NOP
    assert int(dut.load_counter.value) == 0
    assert int(dut.go.value) == 0
    assert int(dut.instruction_shift_enable.value) == 0

    # Set CS genuinely active through the synchronizer, then place a tiny raw
    # SCLK pulse entirely between core rising edges.  An async pin wiggle that
    # is never sampled cannot synthesize a phantom synchronized rising edge.
    dut.spi_cs_n.value = 0
    await ClockCycles(dut.clk, 5)
    before = int(dut.transaction_counter.value)
    await FallingEdge(dut.clk)
    await Timer(2, unit="ns")
    dut.spi_sclk.value = 1
    await Timer(2, unit="ns")
    dut.spi_sclk.value = 0
    await ClockCycles(dut.clk, 5)
    assert int(dut.transaction_counter.value) == before

    # MOSI activity alone is data, never a clock event.
    for _ in range(5):
        dut.spi_mosi.value = 1
        await Timer(3, unit="ns")
        dut.spi_mosi.value = 0
        await Timer(3, unit="ns")
    await ClockCycles(dut.clk, 3)
    assert int(dut.transaction_counter.value) == before

    dut.spi_cs_n.value = 1
    await ClockCycles(dut.clk, 4)


@hierarchy_test("clm_spi_host")
async def supported_spi_rates_phase_offsets_same_transaction_status_and_accumulator_reads(dut):
    await spi_reset_spi(dut)
    spi = SpiMaster(dut)
    await spi.idle()

    # Exercise the entire stated 1-5 MHz range with awkward relative phases to
    # the 50 MHz core clock.  Correctness outside this range is not a contract.
    for hz in (1_000_000, 2_000_000, 5_000_000):
        for offset in (0, 3, 7, 11, 17):
            status = await spi.transaction(SPI_CMD_STATUS, 0xA55A, spi_hz=hz, phase_offset_ns=offset)
            assert status == spi_expected_status(done=1, count=0), (
                f"status wrong at {hz} Hz phase {offset} ns: 0x{status:04X}"
            )

    # All four 1xx commands are accumulator reads, and command[1:0] is exactly
    # the lane index.  Response is returned in the same transaction.
    lane_values = [0x0123, 0x4567, 0x89AB, 0xCDEF]
    for lane, expected in enumerate(lane_values):
        rx = await spi.transaction(SPI_CMD_ACC0 + lane, 0xFFFF)
        assert rx == expected, f"lane {lane} read expected 0x{expected:04X}, got 0x{rx:04X}"

    # Mid-run reads are explicitly live/not latched.  Change both DONE and one
    # accumulator input before the transaction and observe the new values.
    dut.sequencer_done.value = 0
    dut.lane2_accumulator.value = 0x1357
    status = await spi.transaction(SPI_CMD_STATUS, 0)
    assert status == spi_expected_status(done=0, count=0)
    assert await spi.transaction(SPI_CMD_ACC0 + 2, 0) == 0x1357


@hierarchy_test("clm_spi_host")
async def load_framing_every_data_pattern_pulse_width_counter_and_nop_semantics(dut):
    await spi_reset_spi(dut)
    spi = SpiMaster(dut)
    await spi.idle()

    # Commands numerically embedded in a 16-bit instruction word are still raw
    # LOAD data.  The only semantic discriminator is the preceding 3-bit command.
    patterns = [
        0x0000, 0x1000, 0x2000, 0x3000, 0x4000, 0x7000, 0x8000, 0x9000,
        0xA001, 0xB000, 0xD000, 0xE000, 0xF000, 0xFFFF, 0x55AA, 0xA55A,
    ]
    for index, word in enumerate(patterns, start=1):
        rx, widths, samples = await spi_transact_with_pulse_record(
            spi, "instruction_shift_enable", SPI_CMD_LOAD, word
        )
        # LOAD is 0xx, so current RTL returns the status snapshot during its
        # full-duplex data phase.  That response is diagnostic only; the write is
        # what matters architecturally.
        assert rx == spi_expected_status(done=1, count=index - 1)
        assert widths == [1], f"LOAD pulse widths for word {index}: {widths}"
        assert samples == [word], (
            f"instruction data was not stable beside one-cycle strobe: expected 0x{word:04X}, samples={samples}"
        )
        assert int(dut.load_counter.value) == min(index, 16)

    # Counter saturates at 16 rather than wrapping.
    for extra in range(4):
        word = 0x6000 | extra
        _, widths, samples = await spi_transact_with_pulse_record(
            spi, "instruction_shift_enable", SPI_CMD_LOAD, word
        )
        assert widths == [1]
        assert samples == [word]
        assert int(dut.load_counter.value) == 16

    status = await spi.transaction(SPI_CMD_STATUS, 0)
    assert status == spi_expected_status(done=1, count=16)

    # NOP changes no state but, by current 0xx response selection, its data
    # phase legitimately clocks out status rather than guaranteed zeros.
    count_before = int(dut.load_counter.value)
    rx = await spi.transaction(SPI_CMD_NOP, 0xDEAD)
    assert rx == spi_expected_status(done=1, count=count_before)
    assert int(dut.load_counter.value) == count_before
    assert int(dut.instruction_shift_enable.value) == 0
    assert int(dut.go.value) == 0


@hierarchy_test("clm_spi_host")
async def every_incomplete_load_length_is_noncommitting_but_go_can_fire_after_command_phase(dut):
    await spi_reset_spi(dut)
    spi = SpiMaster(dut)
    await spi.idle()

    # Every possible short data length 0..15 must fail to produce a complete
    # instruction write.  No partial word may increment the visibility counter.
    for data_len in range(16):
        stop = [False]
        mon = cocotb.start_soon(spi_record_pulse(dut, "instruction_shift_enable", stop, "instruction_shift_data"))
        bits = [((0xA55A >> n) & 1) for n in range(15, 15 - data_len, -1)]
        await spi.partial_load(bits)
        stop[0] = True
        await RisingEdge(dut.clk)
        widths, _ = await mon
        assert widths == [], f"partial LOAD with {data_len} data bits incorrectly strobed: {widths}"
        assert int(dut.load_counter.value) == 0

    # GO is intentionally different: it becomes effective on the falling edge
    # after command bit 0, before the nominal 16 dummy data clocks.
    widths, _ = await spi_command_only_with_pulse_record(spi, "go", SPI_CMD_GO)
    assert widths == [1], f"short GO did not create exactly one core-cycle pulse: {widths}"
    assert int(dut.load_counter.value) == 0


@hierarchy_test("clm_spi_host")
async def go_data_phase_ignored_exactly_one_pulse_and_clears_load_count(dut):
    await spi_reset_spi(dut)
    spi = SpiMaster(dut)
    await spi.idle()

    # Build a nonzero load count first.
    for i in range(5):
        await spi.transaction(SPI_CMD_LOAD, 0x1200 + i)
    assert int(dut.load_counter.value) == 5

    # Different dummy data words must not affect GO semantics.
    for dummy in (0x0000, 0xFFFF, 0xA55A):
        # Reload one word between GO commands so each clear can be observed.
        if int(dut.load_counter.value) == 0:
            await spi.transaction(SPI_CMD_LOAD, 0xBEEF)
            assert int(dut.load_counter.value) == 1
        _, widths, _ = await spi_transact_with_pulse_record(spi, "go", SPI_CMD_GO, dummy)
        assert widths == [1], f"GO pulse width/duplication error for dummy 0x{dummy:04X}: {widths}"
        assert int(dut.load_counter.value) == 0
        assert int(dut.instruction_shift_enable.value) == 0


@hierarchy_test("clm_spi_host")
async def command_register_all_eight_encodings_and_back_to_back_restart(dut):
    await spi_reset_spi(dut)
    spi = SpiMaster(dut)
    await spi.idle()

    dut.lane0_accumulator.value = 0x1111
    dut.lane1_accumulator.value = 0x2222
    dut.lane2_accumulator.value = 0x3333
    dut.lane3_accumulator.value = 0x4444

    # 000 LOAD
    _, widths, samples = await spi_transact_with_pulse_record(
        spi, "instruction_shift_enable", SPI_CMD_LOAD, 0xCAFE
    )
    assert widths == [1] and samples == [0xCAFE]

    # 001 GO
    _, widths, _ = await spi_transact_with_pulse_record(spi, "go", SPI_CMD_GO, 0x1234)
    assert widths == [1]

    # 010 STATUS, 011 NOP
    expected = spi_expected_status(done=1, count=0)
    assert await spi.transaction(SPI_CMD_STATUS, 0) == expected
    assert await spi.transaction(SPI_CMD_NOP, 0) == expected

    # 100..111 direct lane reads.
    for lane, expected_lane in enumerate((0x1111, 0x2222, 0x3333, 0x4444)):
        assert await spi.transaction(4 + lane, 0) == expected_lane

    # Back-to-back frames restart their 19-clock count under separate CS pulses.
    words = [0x0102, 0x0304, 0x0506, 0x0708]
    for word in words:
        _, widths, samples = await spi_transact_with_pulse_record(
            spi, "instruction_shift_enable", SPI_CMD_LOAD, word
        )
        assert widths == [1] and samples == [word]
    assert int(dut.load_counter.value) == len(words)


# ===========================================================================
# TEST_TOP  (FIXED TINY TAPEOUT TOPLEVEL=tb)
# ===========================================================================

def top_core_handle(dut):
    """Return production Clementine top beneath TinyTapeout tb, or dut itself."""
    try:
        return dut.tt_um_bigmanraffa_clm
    except AttributeError:
        return dut


async def top_reset_top(dut):
    await ensure_clock(dut, 20)
    # Always enter from a writable phase, even if the previous unit test ended
    # after a ReadOnly sample.
    await Timer(1, unit="ns")
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    spi = SpiMaster(dut, top_level=True)
    spi.set_sclk(0)
    spi.set_mosi(0)
    spi.set_cs(1)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    await Timer(1, unit="ns")
    assert value_is_resolvable(dut.uo_out), "uo_out contains X/Z immediately after clean top reset"
    assert (int(dut.uo_out.value) & 1) == 1, "DONE must be high out of reset"

    # R1-R7 and the 16-bit accumulator intentionally have no reset.  Earlier
    # hierarchical unit tests are allowed to mutate them, so scrub every lane
    # using a real Clementine program before each independent end-to-end test.
    scrub = pad_kernel([
        CLRACC(),
        LDI(1, 0), LDI(2, 0), LDI(3, 0), LDI(4, 0),
        LDI(5, 0), LDI(6, 0), LDI(7, 0),
    ])
    await spi_load_kernel(spi, scrub)
    scrub_stats = await top_run_loaded_kernel(dut, spi)
    assert scrub_stats.pc_advances in (None, 16)
    assert value_is_resolvable(dut.uo_out), "uo_out became X/Z during architectural scrub"
    return spi


def top_debug_value(dut, bit: int) -> int:
    return (int(dut.uo_out.value) >> bit) & 1


def top_assert_debug_mapping(dut):
    # Wrapper pins are the contract; hierarchy is only a diagnostic cross-check.
    core = top_core_handle(dut)
    try:
        assert top_debug_value(dut, 0) == int(core.done.value)
        assert top_debug_value(dut, 1) == int(core.any_lane_active.value)
        assert top_debug_value(dut, 2) == int(core.instruction_commit.value)
        assert top_debug_value(dut, 3) == int(core.replay_state.value)
    except AttributeError:
        pass
    assert (int(dut.uo_out.value) >> 4) == 0


async def top_run_loaded_kernel(dut, spi, timeout_cycles=512):
    """Issue a real SPI GO while independently watching the core run.

    GO takes effect after its 3-bit command phase, so a short kernel can finish
    while the SPI master's nominal 16 dummy data clocks are still being sent.
    Starting the run monitor *before* the transaction is therefore essential;
    waiting until spi.transaction() returned would miss the run entirely.
    """
    monitor = cocotb.start_soon(monitor_top_run(top_core_handle(dut), timeout_cycles=timeout_cycles))
    await Timer(1, unit="ns")
    await spi.transaction(SPI_CMD_GO, 0x0000)
    stats = await monitor
    assert stats.pc_advances in (None, 16), f"architectural run made {stats.pc_advances} PC/ring advances, expected 16"
    assert stats.final_pc in (None, 0), f"logical PC after HALT is {stats.final_pc}, expected 0"
    top_assert_debug_mapping(dut)
    assert top_debug_value(dut, 0) == 1
    return stats


async def top_load_and_run(dut, spi, kernel, expected_accs, expected_cycles=None):
    await spi_load_kernel(spi, kernel)
    status = await spi_read_status(spi)
    assert status & 1, "machine must remain halted during upload"
    assert ((status >> 2) & 0x1F) == 16, f"status load count expected 16, got {(status >> 2) & 0x1F}"
    stats = await top_run_loaded_kernel(dut, spi)
    if expected_cycles is not None:
        assert stats.cycles == expected_cycles, f"run cycles expected {expected_cycles}, got {stats.cycles}"
    status = await spi_read_status(spi)
    assert (status & 1) == 1 and ((status >> 1) & 1) == 0
    assert ((status >> 2) & 0x1F) == 0, "GO must clear host visibility load counter"
    got = [await spi_read_acc(spi, lane) for lane in range(4)]
    assert got == list(expected_accs), f"accumulators expected {[hex(x) for x in expected_accs]}, got {[hex(x) for x in got]}"
    if stats.final_instruction is not None:
        assert stats.final_instruction == kernel[0], (
            f"ring did not restore source orientation: mouth=0x{stats.final_instruction:04X}, expected slot0=0x{kernel[0]:04X}"
        )
    return stats