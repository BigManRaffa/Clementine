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
    # Normal broadcast LDI. bit[0]=0 keeps the old 8-bit immediate format.
    if not 0 <= rd < 8:
        raise ValueError("LDI rd must be 0..7")
    if not 0 <= imm8 < 256:
        raise ValueError("LDI immediate must be 0..255")
    return (OP_LDI << 12) | (rd << 9) | (imm8 << 1)

def MOV_HOST(rd: int) -> int:
    """Canonical MOV_HOST encoding: opcode 1001, bit[0]=1, payload bits zero."""
    if not 0 <= rd < 8:
        raise ValueError("MOV_HOST rd must be 0..7")
    return (OP_LDI << 12) | (rd << 9) | 1

def MOV_HOST_RAW(rd: int, ignored_payload: int) -> int:
    """MOV_HOST with deliberate junk in instruction[8:1].

    The architecture defines bit[0]=1 as host mode, so payload[8:1] must not
    affect the value written by the lanes.  This helper is for verification.
    """
    if not 0 <= rd < 8:
        raise ValueError("MOV_HOST rd must be 0..7")
    if not 0 <= ignored_payload < 256:
        raise ValueError("MOV_HOST payload must be 0..255")
    return (OP_LDI << 12) | (rd << 9) | (ignored_payload << 1) | 1

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

def exact_kernel(words: Sequence[int]) -> List[int]:
    body = [u16(x) for x in words]
    if not (1 <= len(body) <= 8):
        raise ValueError(f"expected 1..8 words, got {len(body)}")
    if ((body[-1] >> 12) & 0xF) != OP_HALT:
        raise ValueError("final semantic instruction must be HALT")
    return body

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
            _TEST_CLOCK_TASK.cancel()
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

def gate_level() -> bool:
    """True when the TinyTapeout gate-level netlist is under test."""
    return os.getenv("GATES", "").lower() in {"yes", "1", "true"}

# ---------------------------------------------------------------------------
# SPI master for the NEW compact host.
#
# command[2:0] is NOT shifted over MOSI anymore. The host presents command
# before CS falls. The SPI frame itself is the 16-bit data phase, MSB first.
# CS is the frame boundary; there is no on-chip transaction counter.
# ---------------------------------------------------------------------------

SPI_CMD_EXEC   = 0b000
SPI_CMD_GO     = 0b001
SPI_CMD_STATUS = 0b010
SPI_CMD_BUFFER = 0b011
SPI_CMD_ACC0   = 0b100
SPI_CMD_ACC1   = 0b101
SPI_CMD_ACC2   = 0b110
SPI_CMD_ACC3   = 0b111

class SpiMaster:
    def __init__(self, dut, top_level: bool = False):
        self.dut = dut
        self.top_level = top_level
        self._uio = 0
        self._ui = 0

    def _drive_top_uio_bit(self, idx: int, val: int):
        if val:
            self._uio |= 1 << idx
        else:
            self._uio &= ~(1 << idx)
        self.dut.uio_in.value = self._uio

    def set_command(self, command: int):
        command &= 0x7
        if self.top_level:
            self._ui = (self._ui & ~0x7) | command
            self.dut.ui_in.value = self._ui
        else:
            self.dut.command.value = command

    def set_cs(self, val: int):
        if self.top_level:
            self._drive_top_uio_bit(0, val)
        else:
            self.dut.spi_cs_n.value = val

    def set_mosi(self, val: int):
        if self.top_level:
            self._drive_top_uio_bit(1, val)
        else:
            self.dut.spi_mosi.value = val

    def set_sclk(self, val: int):
        if self.top_level:
            self._drive_top_uio_bit(3, val)
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

    async def _clock_bits(
        self,
        bits: Sequence[int],
        spi_hz: int,
        sample_miso: bool = True,
    ) -> List[int]:
        if spi_hz <= 0:
            raise ValueError(spi_hz)
        half_ns = 1e9 / (2.0 * spi_hz)
        if half_ns < 1:
            raise ValueError("SPI helper requires >=1 ns half-period")

        rx_bits: List[int] = []
        sample_delay = min(10.0, half_ns / 4.0)
        high_remainder = half_ns - sample_delay

        for out_bit in bits:
            self.set_mosi(int(out_bit) & 1)
            await Timer(half_ns, unit="ns")
            self.set_sclk(1)
            await Timer(sample_delay, unit="ns")
            if sample_miso:
                rx_bits.append(self.get_miso())
            if high_remainder > 0:
                await Timer(high_remainder, unit="ns")
            self.set_sclk(0)

        return rx_bits

    async def transaction(
        self,
        command: int,
        tx_word: int = 0,
        spi_hz: int = 5_000_000,
        phase_offset_ns: int = 0,
        command_change_after_cs: Optional[int] = None,
    ) -> int:
        if not (0 <= command < 8):
            raise ValueError(command)

        self.set_command(command)
        self.set_sclk(0)
        self.set_mosi(0)
        self.set_cs(1)
        if phase_offset_ns:
            await Timer(phase_offset_ns, unit="ns")

        # Sideband command is stable before CS falls.
        await ClockCycles(self.dut.clk, 2)
        self.set_cs(0)
        await ClockCycles(self.dut.clk, 4)

        if command_change_after_cs is not None:
            self.set_command(command_change_after_cs)
            await ClockCycles(self.dut.clk, 1)

        bits = [((tx_word >> n) & 1) for n in range(15, -1, -1)]
        # We physically sample MISO on every ordinary frame because that is
        # what a Mode-0 master does. Architecturally the returned bits matter
        # only for STATUS and ACC0..ACC3; EXEC and GO MISO are don't-care.
        rx_bits = await self._clock_bits(bits, spi_hz, sample_miso=True)

        half_ns = 1e9 / (2.0 * spi_hz)
        await Timer(half_ns, unit="ns")
        self.set_cs(1)
        self.set_mosi(0)
        await ClockCycles(self.dut.clk, 5)

        rx = 0
        for b in rx_bits:
            rx = (rx << 1) | b
        return rx

    async def raw_frame(
        self,
        command: int,
        data_bits: Sequence[int],
        spi_hz: int = 5_000_000,
        phase_offset_ns: int = 0,
        command_change_after_cs: Optional[int] = None,
        sample_miso: bool = False,
    ) -> List[int]:
        """Send an arbitrary-length CS-framed data phase.

        SPEC-valid ordinary transactions are 16 clocks. BUFFER (011) is the
        sole 32-clock frame because the host staging chain is four 8-bit lane
        slices. MISO is don't-care for BUFFER.
        """
        if not (0 <= command < 8):
            raise ValueError(command)
        if spi_hz <= 0:
            raise ValueError(spi_hz)

        self.set_command(command)
        self.set_sclk(0)
        self.set_mosi(0)
        self.set_cs(1)

        if phase_offset_ns:
            await Timer(phase_offset_ns, unit="ns")

        # Sideband command must be stable before CS falls.
        await ClockCycles(self.dut.clk, 2)
        self.set_cs(0)
        # Let the synchronized CS falling edge latch command[2:0].
        await ClockCycles(self.dut.clk, 4)

        if command_change_after_cs is not None:
            self.set_command(command_change_after_cs)
            await ClockCycles(self.dut.clk, 1)

        rx_bits = await self._clock_bits(
            data_bits, spi_hz, sample_miso=sample_miso
        )

        half_ns = 1e9 / (2.0 * spi_hz)
        await Timer(half_ns, unit="ns")
        self.set_cs(1)
        self.set_mosi(0)
        await ClockCycles(self.dut.clk, 5)
        return rx_bits

    async def load_host_buffer(
        self,
        lane_bytes: Sequence[int],
        spi_hz: int = 5_000_000,
        phase_offset_ns: int = 0,
        command_change_after_cs: Optional[int] = None,
    ):
        """Load [lane0,lane1,lane2,lane3], lane0 byte first, MSB first."""
        if len(lane_bytes) != 4:
            raise ValueError("host buffer requires exactly four lane bytes")

        bits = []
        for value in lane_bytes:
            value = int(value)
            if not 0 <= value < 256:
                raise ValueError("host buffer bytes must be 0..255")
            bits.extend((value >> n) & 1 for n in range(7, -1, -1))

        await self.raw_frame(
            SPI_CMD_BUFFER,
            bits,
            spi_hz=spi_hz,
            phase_offset_ns=phase_offset_ns,
            command_change_after_cs=command_change_after_cs,
            sample_miso=False,
        )

    async def short_frame(
        self,
        command: int,
        data_bits: Sequence[int],
        spi_hz: int = 5_000_000,
    ):
        """Characterize the counterless CS-framed implementation.

        Short frames are outside the normal command protocol. EXEC/GO/STATUS/
        ACC use 16 data clocks; BUFFER is the deliberate 32-clock exception.
        The RTL intentionally has no length counter.
        """
        if not (0 <= command < 8):
            raise ValueError(command)
        if len(data_bits) >= 16:
            raise ValueError("short_frame expects fewer than 16 bits")

        self.set_command(command)
        self.set_sclk(0)
        self.set_mosi(0)
        self.set_cs(1)
        await ClockCycles(self.dut.clk, 2)
        self.set_cs(0)
        await ClockCycles(self.dut.clk, 4)

        await self._clock_bits(data_bits, spi_hz, sample_miso=False)

        half_ns = 1e9 / (2.0 * spi_hz)
        await Timer(half_ns, unit="ns")
        self.set_cs(1)
        self.set_mosi(0)
        await ClockCycles(self.dut.clk, 5)

async def spi_load_kernel(spi: SpiMaster, words: Sequence[int], spi_hz: int = 5_000_000):
    if not (1 <= len(words) <= 8):
        raise ValueError("Clementine addressed-buffer kernel is 1..8 words")
    for word in words:
        await spi.transaction(SPI_CMD_EXEC, word, spi_hz=spi_hz)

async def spi_read_status(spi: SpiMaster, spi_hz: int = 5_000_000) -> int:
    return await spi.transaction(SPI_CMD_STATUS, 0, spi_hz=spi_hz)

async def spi_read_acc(spi: SpiMaster, lane: int, spi_hz: int = 5_000_000) -> int:
    if lane not in range(4):
        raise ValueError(lane)
    return await spi.transaction(SPI_CMD_ACC0 + lane, 0, spi_hz=spi_hz)

# ---------------------------------------------------------------------------
# Single-TOPLEVEL hierarchy harness
# ---------------------------------------------------------------------------
#
# TinyTapeout always compiles tb.v as TOPLEVEL=tb. RTL exhaustive tests operate
# on real production submodules beneath tb.tt_um_bigmanraffa_clm and force only
# target-child INPUT ports. Gate-level synthesis may flatten those submodules, so
# hierarchy tests are SKIP under GATES=yes and full-chip tests use only chip pins.
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
    """Drive the production decoder through the real addressed-buffer sources.

    Icarus does not reliably honor Force on a child input net continuously
    driven by its parent. Decoder.current_instruction comes from fetch slot0
    when logical_pc==0, while replay_state comes from fetch_seq.fsm_state.
    The exhaustive decoder test runs without a clock, so deposits into those
    real state elements remain stable without modifying RTL or tb.v.
    """
    def __init__(self, root, target, force_inputs, forced_registry):
        super().__init__(root, target, force_inputs, forced_registry)
        fetch = _core(root).fetch_seq
        try:
            self._instruction_source = fetch.slot0
            self._replay_source = fetch.fsm_state
            self._pc_source = fetch.logical_pc
        except Exception as exc:
            raise AssertionError(
                "Icarus did not expose fetch_seq slot0/fsm_state/logical_pc; "
                "decoder exhaustive test cannot be driven"
            ) from exc

        # current_instruction is an 8-slot mux indexed by logical_pc[2:0].
        # Hold it on slot0 for the entire combinational sweep.
        self._pc_source.value = Deposit(0)

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
            "laneid_mode",
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
            "instruction_valid", "instruction_in",
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
            "host_mode", "host_shift", "host_serial_in",
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
            "command",
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

    RTL: missing production hierarchy is a hard failure, so PASS means the body
    actually executed. GL: synthesized hierarchy is intentionally flattened, so
    hierarchy tests are registered as SKIP and architecture is tested at pins.
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
        # Synthesized GL netlists are flattened; decoder/lane/fetch/spi_host
        # instance hierarchy is not an architectural interface. These unit and
        # implementation tests remain exhaustive RTL regressions and are
        # reported as SKIP (never fake PASS) under GATES=yes.
        return cocotb.test(skip=gate_level())(guarded)

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
        "host_mode": 0,
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
        # ISA opcode 1001 has two architectural forms:
        #   bit0=0 -> LDI rd, imm8       (broadcast immediate)
        #   bit0=1 -> MOV_HOST rd        (per-lane staging byte)
        # Derive host_mode from the ISA sub-operation, not from an RTL gate.
        ldi_subop = instr & 1
        if ldi_subop == 0:
            exp["host_mode"] = 0
            exp["immediate_value"] = imm
        else:
            exp["host_mode"] = 1
            # MOV_HOST gets its architectural value from host_byte. Bits[8:1]
            # are payload/don't-care for the value actually written.
            exp["immediate_value"] = decoder_DONTCARE

        # Both forms intentionally reuse the LDI write datapath.
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

@hierarchy_test("clm_decoder")
async def decoder_ldi_mov_host_subop_architectural_contract(dut):
    """ARCHITECTURE PROOF: opcode 1001 bit0 selects LDI vs MOV_HOST."""
    cases = [
        (LDI(3, 0x00), 0, 0x00),
        (LDI(3, 0xFF), 0, 0xFF),
        (MOV_HOST(3), 1, decoder_DONTCARE),
        (MOV_HOST_RAW(3, 0xA5), 1, decoder_DONTCARE),
    ]

    for word, expected_host_mode, expected_imm in cases:
        dut.current_instruction.value = word
        dut.replay_state.value = 0
        await Timer(1, unit="ns")

        expected = decoder_expected_decoder(word, 0)
        assert expected["host_mode"] == expected_host_mode
        assert int(dut.host_mode.value) == expected_host_mode
        assert int(dut.select_immediate.value) == 1
        assert int(dut.register_write_enable.value) == 1
        assert int(dut.rd_address.value) == 3

        if expected_imm is not decoder_DONTCARE:
            assert int(dut.immediate_value.value) == expected_imm

@hierarchy_test("clm_decoder")
async def exhaustive_decoder_architectural_contract(dut):
    """Exhaust the entire 16-bit ISA in normal and replay decoder states."""
    dut.current_instruction.value = 0
    dut.replay_state.value = 0
    await Timer(1, unit="ns")

    if exhaustive_enabled():
        instructions = range(0x10000)
    else:
        samples = set()
        payloads = (0x000, 0x001, 0x008, 0x010, 0x055, 0x0AA, 0x1FE, 0x1FF,
                    0x249, 0x492, 0x555, 0x7FF, 0xAAA, 0xFFF)
        for op in range(16):
            for payload in payloads:
                samples.add((op << 12) | payload)
            for rs in range(8):
                for rt in range(8):
                    samples.add((op << 12) | (3 << 9) | (rs << 6) | (rt << 3))
                    samples.add((op << 12) | (5 << 9) | (rs << 6) | (rt << 3) | 1)
        instructions = sorted(samples)

    checked = 0
    for replay in (0, 1):
        dut.replay_state.value = replay
        await Timer(1, unit="ns")

        for instr in instructions:
            dut.current_instruction.value = instr
            await Timer(1, unit="ns")
            expected = decoder_expected_decoder(instr, replay)
            for name, value in expected.items():
                decoder_check_signal(dut, name, value, instr, replay)
            checked += 1

    assert checked > 0

# ===========================================================================
# TEST_REGFILE  (REAL HIERARCHY: core.lane0.lane_regfile)
# ===========================================================================

async def regfile_start(dut):
    await ensure_clock(dut, 20)
    dut.write_enable.value = 0
    dut.write_address.value = 0
    dut.write_data.value = 0
    dut.laneid_mode.value = 0
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

@hierarchy_test("clm_regfile")
async def register_file_exhaustive_architectural_behavior(dut):
    """Exercise every stored register, all byte values, row routing and hard R0."""
    await regfile_start(dut)

    for addr in range(1, 8):
        await regfile_write_reg(dut, addr, 0)

    assert await regfile_read_reg(dut, 0) == 0

    values = range(256) if exhaustive_enabled() else exhaustive_values()
    for addr in range(1, 8):
        for value in values:
            await regfile_write_reg(dut, addr, value)
            got = await regfile_read_reg(dut, addr)
            assert got == value, (
                f"R{addr} write/read mismatch: expected 0x{value:02X}, got 0x{got:02X}"
            )
            assert await regfile_read_reg(dut, 0) == 0

    signatures = {
        1: 0x11, 2: 0x22, 3: 0x33, 4: 0x44,
        5: 0x55, 6: 0x66, 7: 0x77,
    }
    for addr, value in signatures.items():
        await regfile_write_reg(dut, addr, value)

    for row in range(4):
        dut.read_row_even.value = row
        dut.read_row_odd.value = row
        await Timer(1, unit="ns")
        even_reg = row << 1
        odd_reg = (row << 1) | 1
        expected_even = 0 if even_reg == 0 else signatures[even_reg]
        expected_odd = signatures[odd_reg]
        assert int(dut.read_data_even.value) == expected_even
        assert int(dut.read_data_odd.value) == expected_odd

    before = await regfile_snapshot(dut)
    for value in (0x00, 0x01, 0x7F, 0x80, 0xFF):
        await regfile_write_reg(dut, 0, value)
        after = await regfile_snapshot(dut)
        assert after == before
        assert after[0] == 0

    dut.write_enable.value = 0
    dut.write_address.value = 3
    dut.write_data.value = 0xEE
    before = await regfile_snapshot(dut)
    await RisingEdge(dut.clk)
    await ReadOnly()
    await Timer(1, unit="ns")
    after = await regfile_snapshot(dut)
    assert after == before

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
# NEW: 8-entry static addressed instruction buffer
# ===========================================================================

async def fetch_start(dut):
    await ensure_clock(dut, 20)
    dut.rst_n.value = 0
    dut.go.value = 0
    dut.instruction_valid.value = 0
    dut.instruction_in.value = 0
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
    dut.instruction_in.value = word & 0xFFFF
    dut.instruction_valid.value = 1
    await fetch_edge(dut)
    dut.instruction_valid.value = 0
    await Timer(1, unit="ns")

async def fetch_upload_image(dut, words):
    if not (1 <= len(words) <= 8):
        raise AssertionError("addressed instruction buffer accepts 1..8 words")
    for word in words:
        await fetch_upload_word(dut, word)
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
    """8 static slots, short kernels, HALT anywhere, bare-GO rerun."""
    await fetch_start(dut)

    assert int(dut.done.value) == 1
    assert int(dut.replay_state.value) == 0
    assert int(dut.instruction_commit.value) == 0
    if fetch_pc(dut) is not None:
        assert fetch_pc(dut) == 0

    full_words = [0x1101, 0x2202, 0x3303, 0x4404,
                  0x5505, 0x6606, 0x7707, 0x8808]
    await fetch_upload_image(dut, full_words)

    try:
        assert int(dut.write_pointer.value) == 0
    except AttributeError:
        pass

    assert int(dut.current_instruction.value) == full_words[0]

    await fetch_go(dut)
    assert int(dut.done.value) == 0
    assert int(dut.current_instruction.value) == full_words[0]

    # Walk all eight static addresses. Nothing is shifted or rotated.
    for pc in range(8):
        assert fetch_pc(dut) == pc
        assert int(dut.current_instruction.value) == full_words[pc]
        assert int(dut.instruction_commit.value) == 1
        dut.is_halt.value = 0
        await fetch_edge(dut)

    # The 4-bit logical PC continues; the 8-entry read mux uses pc[2:0].
    assert fetch_pc(dut) == 8
    assert int(dut.current_instruction.value) == full_words[0]

    # Return to halted/loading state without clearing the static slots.
    dut.rst_n.value = 0
    await fetch_edge(dut)
    dut.rst_n.value = 1
    await fetch_edge(dut)
    assert int(dut.done.value) == 1
    assert fetch_pc(dut) == 0

    # Short kernel: no 16-word padding; HALT is in slot 4.
    short_words = [0x9002, 0x1103, 0x2204, 0x3305, 0xF000]
    await fetch_upload_image(dut, short_words)
    assert int(dut.current_instruction.value) == short_words[0]

    try:
        assert int(dut.write_pointer.value) == 5
    except AttributeError:
        pass

    await fetch_go(dut)
    assert fetch_pc(dut) == 0
    try:
        assert int(dut.write_pointer.value) == 0
    except AttributeError:
        pass

    for pc, word in enumerate(short_words):
        assert fetch_pc(dut) == pc
        assert int(dut.current_instruction.value) == word
        dut.is_halt.value = int(pc == 4)
        assert int(dut.instruction_commit.value) == 1
        await fetch_edge(dut)

    assert int(dut.done.value) == 1
    assert fetch_pc(dut) == 5

    # Bare GO restores PC zero; static slots require no physical realignment.
    dut.is_halt.value = 0
    await fetch_go(dut)
    assert int(dut.done.value) == 0
    assert fetch_pc(dut) == 0
    assert int(dut.current_instruction.value) == short_words[0]

    for pc, word in enumerate(short_words):
        assert int(dut.current_instruction.value) == word
        dut.is_halt.value = int(pc == 4)
        await fetch_edge(dut)
    assert int(dut.done.value) == 1

@hierarchy_test("clm_fetch_seq")
async def cross_cutting_cycle_invariants(dut):
    """Stress replay/scan/reconvergence while static slots never move."""
    await fetch_start(dut)

    words = [0x5000 + i for i in range(8)]
    await fetch_upload_image(dut, words)
    await fetch_go(dut)

    for cycle in range(80):
        phase = cycle % 10
        dut.is_halt.value = 0
        dut.instruction_valid.value = 0
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
            dut.stack_top_target.value = fetch_pc(dut)
            dut.bank_conflict.value = 1

        await Timer(1, unit="ns")

        assert not (
            int(dut.operand_hold_load.value)
            and int(dut.operand_hold_use.value)
        )

        pc_before = fetch_pc(dut)
        instr_before = int(dut.current_instruction.value)
        assert instr_before == words[pc_before & 0x7]

        hold_load = int(dut.operand_hold_load.value)
        pop = int(dut.command_reconverge_pop.value)

        await fetch_edge(dut)

        pc_after = fetch_pc(dut)
        instr_after = int(dut.current_instruction.value)

        if hold_load or pop:
            assert pc_after == pc_before
            assert instr_after == instr_before
        elif pc_after != pc_before:
            assert pc_after == ((pc_before + 1) & 0xF)

        assert instr_after == words[pc_after & 0x7]

@hierarchy_test("clm_fetch_seq")
async def impl_regression_bank_conflict_capture_replay_shape(dut):
    """IMPLEMENTATION REGRESSION: preserve the current capture/replay FSM shape.

    This intentionally checks mechanism (capture cycle then replay cycle), not
    merely architectural correctness. A legal future FSM rewrite may require
    updating/removing this test while the architecture-result tests stay valid.
    """
    await fetch_start(dut)

    conflict_word = ADD(5, 1, 3)  # R1/R3 are both odd -> conflict
    await fetch_upload_image(dut, [conflict_word, HALT()])

    dut.rs_address.value = 1
    dut.rt_address.value = 3
    dut.bank_conflict.value = 1
    dut.is_halt.value = 0
    dut.any_lane_active.value = 1
    dut.stack_top_valid.value = 0

    await fetch_go(dut)
    assert int(dut.done.value) == 0
    assert fetch_pc(dut) == 0

    assert int(dut.replay_state.value) == 0
    assert int(dut.operand_hold_load.value) == 1
    assert int(dut.operand_hold_use.value) == 0
    assert int(dut.instruction_commit.value) == 0

    await fetch_edge(dut)
    assert fetch_pc(dut) == 0
    assert int(dut.replay_state.value) == 1

    assert int(dut.operand_hold_load.value) == 0
    assert int(dut.operand_hold_use.value) == 1
    assert int(dut.instruction_commit.value) == 1

    await fetch_edge(dut)
    assert fetch_pc(dut) == 1
    assert int(dut.replay_state.value) == 0
    assert int(dut.operand_hold_use.value) == 0

    dut.bank_conflict.value = 0
    dut.is_halt.value = 1
    # These are combinational fetch inputs. Leave ReadOnly and allow a delta/time
    # step before sampling instruction_commit, otherwise this assertion can see
    # the previous bank_conflict/is_halt combination.
    await Timer(1, unit="ns")
    assert int(dut.instruction_commit.value) == 1
    await fetch_edge(dut)
    assert int(dut.done.value) == 1
    assert fetch_pc(dut) == 2

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
    dut.host_mode.value = 0
    dut.host_shift.value = 0
    dut.host_serial_in.value = 0
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

async def lane_shift_host_byte(dut, value: int):
    """Shift one complete byte into the real lane host slice, MSB first."""
    if not 0 <= value < 256:
        raise ValueError(value)

    lane_inert(dut)
    for bit_index in range(7, -1, -1):
        dut.host_serial_in.value = (value >> bit_index) & 1
        dut.host_shift.value = 1
        await lane_stable_edge(dut)
        dut.host_shift.value = 0
        await Timer(1, unit="ns")
    lane_inert(dut)
    await Timer(1, unit="ns")

async def lane_mov_host(dut, reg: int, active: int = 1, commit: int = 1):
    """Drive the controls that decoder emits for MOV_HOST."""
    lane_inert(dut)
    dut.lane_active.value = active
    dut.instruction_commit.value = commit
    dut.register_write_enable.value = 1
    dut.rd_address.value = reg

    # Poison the ordinary immediate path. host_mode must override this with
    # host_byte or the test will immediately catch it.
    dut.immediate_value.value = 0x5A
    dut.select_immediate.value = 1
    dut.force_one_box2.value = 1
    dut.prepare_zero.value = 1
    dut.writeback_select.value = 0
    dut.host_mode.value = 1

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
    """Drive exactly the controls emitted by decoder for LANEID."""
    lane_inert(dut)
    dut.lane_active.value = active
    dut.instruction_commit.value = commit
    dut.register_write_enable.value = 1
    dut.rd_address.value = reg
    dut.laneid_mode.value = 1

    # LANEID is MOV bit0=1, not an immediate instruction. laneid_mode forces
    # the virtual R0 leaf to LANE_ID and lane_read_row_even to row0.
    dut.select_immediate.value = 0
    dut.force_one_box2.value = 1
    dut.prepare_zero.value = 1
    dut.writeback_select.value = 0
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

@hierarchy_test("clm_lane")
async def mov_host_lane_slice_exhaustive_8bit_mux_mask_commit_and_r0(dut):
    """Exhaust every host byte through the REAL lane0 slice and writeback path."""
    await lane_start(dut)

    # Every raw 8-bit value is legal now, including signed-negative encodings.
    values = range(256) if exhaustive_enabled() else exhaustive_values()
    for value in values:
        await lane_shift_host_byte(dut, value)
        assert int(dut.host_byte.value) == value
        assert int(dut.host_serial_out.value) == ((value >> 7) & 1)

        await lane_mov_host(dut, 4)
        assert await lane_read_reg(dut, 4) == value

    # host_mode=0 restores full-width ordinary LDI and ignores staged data.
    await lane_shift_host_byte(dut, 0xE1)
    await lane_ldi(dut, 4, 0xD3)
    assert await lane_read_reg(dut, 4) == 0xD3

    # MOV_HOST is still an ordinary SIMT instruction: mask and commit qualify it.
    await lane_shift_host_byte(dut, 0x91)
    await lane_mov_host(dut, 4, active=0)
    assert await lane_read_reg(dut, 4) == 0xD3

    await lane_mov_host(dut, 4, active=1, commit=0)
    assert await lane_read_reg(dut, 4) == 0xD3

    # R0 remains hardwired zero.
    await lane_mov_host(dut, 0)
    assert await lane_read_reg(dut, 0) == 0

@hierarchy_test("clm_lane")
async def impl_regression_operand_hold_replay_even_odd_r0_and_same_register(dut):
    """IMPLEMENTATION REGRESSION: preserve the current operand-hold replay path.

    Architectural same-bank correctness is tested separately at full-chip level.
    This test is allowed to fail after a legal microarchitectural replay rewrite.
    """
    await lane_start(dut)

    seeds = {
        1: 0x23, 2: 0xC8, 3: 0x05, 5: 0x81, 6: 0x11,
    }
    for reg, value in seeds.items():
        await lane_ldi(dut, reg, value)

    assert await lane_capture_replay_binary(dut, 1, 3, 7, "add") == u8(0x23 + 0x05)
    assert await lane_capture_replay_binary(dut, 2, 6, 4, "sub") == u8(0xC8 - 0x11)
    assert await lane_capture_replay_binary(dut, 0, 2, 5, "sub") == u8(0x00 - 0xC8)
    assert await lane_capture_replay_binary(dut, 3, 3, 2, "sub") == 0

@hierarchy_test("clm_lane")
async def laneid_lane0_path_mask_commit_and_r0_contract(dut):
    """Call the real LANEID helper; full 0/1/2/3 coverage is at top level."""
    await lane_start(dut)

    await lane_ldi(dut, 4, 0xA5)
    await lane_laneid_write(dut, 4)
    assert await lane_read_reg(dut, 4) == 0

    await lane_ldi(dut, 4, 0x5A)
    await lane_laneid_write(dut, 4, active=0)
    assert await lane_read_reg(dut, 4) == 0x5A

    await lane_laneid_write(dut, 4, active=1, commit=0)
    assert await lane_read_reg(dut, 4) == 0x5A

    await lane_laneid_write(dut, 0)
    assert await lane_read_reg(dut, 0) == 0

# ===========================================================================
# TEST_SPI_HOST  (REAL HIERARCHY: core.spi_host)
# MOV_HOST architecture: 011 = 32-clock distributed host-buffer load
# ===========================================================================

async def spi_stable_edge(dut):
    await RisingEdge(dut.clk)
    await ReadOnly()
    await Timer(1, unit="ns")

async def spi_reset_spi(dut):
    await ensure_clock(dut, 20)
    dut.command.value = SPI_CMD_STATUS
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

def spi_expected_status(done: int) -> int:
    return ((0 if done else 1) << 1) | (1 if done else 0)

async def spi_record_pulse(dut, signal_name: str, stop, sample_name=None):
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
            if sample_name is not None:
                high_samples.append(int(getattr(dut, sample_name).value))
        elif current_width:
            widths.append(current_width)
            current_width = 0
    if current_width:
        widths.append(current_width)
    return widths, high_samples

async def spi_transact_with_pulse_record(
    spi,
    signal_name: str,
    command: int,
    word: int = 0,
    **kwargs,
):
    stop = [False]
    sample_name = "instruction_data" if signal_name == "instruction_valid" else None
    monitor = cocotb.start_soon(
        spi_record_pulse(spi.dut, signal_name, stop, sample_name)
    )
    rx = await spi.transaction(command, word, **kwargs)
    stop[0] = True
    await RisingEdge(spi.dut.clk)
    widths, samples = await monitor
    # spi_record_pulse() samples in ReadOnly. Move to a writable time slot
    # before the caller starts another SPI frame.
    await Timer(1, unit="ns")
    return rx, widths, samples

async def spi_record_host_stream(dut, stop):
    """Capture the serial bit presented on every host_shift pulse."""
    bits = []
    pulse_widths = []
    current_width = 0
    while not stop[0]:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if int(dut.host_shift.value):
            current_width += 1
            bits.append(int(dut.host_serial_out.value))
        elif current_width:
            pulse_widths.append(current_width)
            current_width = 0
    if current_width:
        pulse_widths.append(current_width)
    return pulse_widths, bits

@hierarchy_test("clm_spi_host")
async def reset_synchronizers_and_no_phantom_edges(dut):
    await spi_reset_spi(dut)

    assert int(dut.sclk_sync.value) == 0
    assert int(dut.cs_n_sync.value) == 0b111
    assert int(dut.mosi_sync.value) == 0
    assert int(dut.go.value) == 0
    assert int(dut.instruction_valid.value) == 0
    assert int(dut.host_shift.value) == 0

    # Capture a known non-buffer command.
    dut.command.value = SPI_CMD_STATUS
    dut.spi_cs_n.value = 0
    await ClockCycles(dut.clk, 5)
    assert int(dut.command_latched.value) == SPI_CMD_STATUS

    # A physical SCLK glitch entirely between core edges must not become a
    # synchronized rising edge and therefore must not assert host_shift.
    await FallingEdge(dut.clk)
    await Timer(2, unit="ns")
    dut.spi_mosi.value = 1
    dut.spi_sclk.value = 1
    await Timer(2, unit="ns")
    dut.spi_sclk.value = 0
    await ClockCycles(dut.clk, 5)
    assert int(dut.host_shift.value) == 0

    dut.spi_cs_n.value = 1
    await ClockCycles(dut.clk, 5)
    assert int(dut.go.value) == 0
    assert int(dut.instruction_valid.value) == 0

@hierarchy_test("clm_spi_host")
async def supported_spi_rates_phase_offsets_same_transaction_status_and_accumulator_reads(dut):
    """SPEC: sideband command, same-frame reads, Mode-0 timing over 1-5 MHz."""
    await spi_reset_spi(dut)
    spi = SpiMaster(dut)
    await spi.idle()

    for hz in (1_000_000, 2_000_000, 5_000_000):
        for offset in (0, 3, 7, 11, 17):
            status = await spi.transaction(
                SPI_CMD_STATUS,
                0xA55A,
                spi_hz=hz,
                phase_offset_ns=offset,
            )
            assert status == spi_expected_status(done=1), (
                f"status wrong at {hz} Hz phase {offset} ns: 0x{status:04X}"
            )

    lane_values = [0x0123, 0x4567, 0x89AB, 0xCDEF]
    for lane, expected in enumerate(lane_values):
        # SPEC: accumulator response belongs to THIS same 16-clock frame.
        rx = await spi.transaction(SPI_CMD_ACC0 + lane, 0xFFFF)
        assert rx == expected, (
            f"same-frame lane {lane} read expected 0x{expected:04X}, "
            f"got 0x{rx:04X}"
        )

    # SPEC: command is captured from sideband command/ui_in[2:0] at CS-fall.
    # Changing the external command pins after capture cannot redirect either
    # the response or behavior of the current frame.
    status_latched = await spi.transaction(
        SPI_CMD_STATUS,
        0,
        command_change_after_cs=SPI_CMD_ACC3,
    )
    assert status_latched == spi_expected_status(done=1), (
        "STATUS response changed after sideband command pins changed mid-frame"
    )

    acc0_latched = await spi.transaction(
        SPI_CMD_ACC0,
        0,
        command_change_after_cs=SPI_CMD_STATUS,
    )
    assert acc0_latched == lane_values[0], (
        "ACC0 response changed after sideband command pins changed mid-frame"
    )

    dut.sequencer_done.value = 0
    dut.lane2_accumulator.value = 0x1357
    assert await spi.transaction(SPI_CMD_STATUS, 0) == spi_expected_status(done=0)
    assert await spi.transaction(SPI_CMD_ACC2, 0) == 0x1357

@hierarchy_test("clm_spi_host")
async def exec_frames_all_data_patterns_and_pulses_instruction_valid_once(dut):
    await spi_reset_spi(dut)
    spi = SpiMaster(dut)
    await spi.idle()

    patterns = [
        0x0000, 0x1000, 0x2000, 0x3000,
        0x4000, 0x7000, 0x8000, LDI(4, 0xFF),
        MOV_HOST(4), MOV_HOST_RAW(4, 0xFF),
        0xA001, 0xB000, 0xD000, 0xE000,
        0xF000, 0xFFFF, 0x55AA, 0xA55A,
    ]

    for word in patterns:
        _rx_dont_care, widths, samples = await spi_transact_with_pulse_record(
            spi, "instruction_valid", SPI_CMD_EXEC, word
        )
        # SPEC: MISO is don't-care for EXEC. Only the write-side behavior
        # (one instruction_valid pulse carrying this word) is architectural.
        assert widths == [1]
        assert samples == [word]
        assert int(dut.go.value) == 0
        assert int(dut.host_shift.value) == 0

@hierarchy_test("clm_spi_host")
async def buffer_command_exact_32_host_shift_bits_and_no_exec_or_go(dut):
    """011 forwards exactly the 32 synchronized MOSI bits to the lane chain."""
    await spi_reset_spi(dut)
    spi = SpiMaster(dut)
    await spi.idle()

    lane_bytes = [0x12, 0x80, 0xFF, 0x35]
    expected_bits = []
    for value in lane_bytes:
        expected_bits.extend((value >> n) & 1 for n in range(7, -1, -1))

    for hz, offset in ((1_000_000, 0), (2_000_000, 7), (5_000_000, 17)):
        stop = [False]
        monitor = cocotb.start_soon(spi_record_host_stream(dut, stop))

        await spi.load_host_buffer(
            lane_bytes,
            spi_hz=hz,
            phase_offset_ns=offset,
        )

        stop[0] = True
        await RisingEdge(dut.clk)
        widths, got_bits = await monitor
        await Timer(1, unit="ns")

        assert got_bits == expected_bits, (
            f"buffer serial stream mismatch at {hz} Hz phase {offset}: "
            f"{got_bits}"
        )
        assert len(got_bits) == 32
        assert widths == [1] * 32
        assert int(dut.command_latched.value) == SPI_CMD_BUFFER
        assert int(dut.instruction_valid.value) == 0
        assert int(dut.go.value) == 0

@hierarchy_test("clm_spi_host")
async def buffer_command_holds_for_entire_data_phase(dut):
    """BUFFER remains active for the whole frame even if sideband command pins change during the data phase."""
    await spi_reset_spi(dut)
    spi = SpiMaster(dut)
    await spi.idle()

    lane_bytes = [0xDE, 0xAD, 0xBE, 0xEF]

    bits = []
    for value in lane_bytes:
        bits.extend(
            (value >> n) & 1
            for n in range(7, -1, -1)
        )

    stop = [False]
    host_monitor = cocotb.start_soon(
        spi_record_host_stream(dut, stop)
    )

    # Command is BUFFER during the required setup/acquisition period.
    # Once the data phase is underway, deliberately change the external
    # sideband command pins to EXEC.
    await spi.raw_frame(
        SPI_CMD_BUFFER,
        bits,
        command_change_after_cs=SPI_CMD_EXEC,
    )

    stop[0] = True
    await RisingEdge(dut.clk)

    widths, got_bits = await host_monitor
    await Timer(1, unit="ns")

    # Architectural contract: the already-started transaction remains BUFFER.
    assert got_bits == bits
    assert widths == [1] * 32
    assert int(dut.instruction_valid.value) == 0
    assert int(dut.go.value) == 0

@hierarchy_test("clm_spi_host")
async def all_eight_sideband_commands_and_back_to_back_frames(dut):
    """SPEC: all sideband commands are distinct; read commands answer same-frame."""
    await spi_reset_spi(dut)
    spi = SpiMaster(dut)
    await spi.idle()

    dut.lane0_accumulator.value = 0x1111
    dut.lane1_accumulator.value = 0x2222
    dut.lane2_accumulator.value = 0x3333
    dut.lane3_accumulator.value = 0x4444

    # 000 EXEC.
    _, widths, samples = await spi_transact_with_pulse_record(
        spi, "instruction_valid", SPI_CMD_EXEC, 0xCAFE
    )
    assert widths == [1] and samples == [0xCAFE]

    # 001 GO.
    for dummy in (0x0000, 0xFFFF, 0xA55A):
        _, widths, _ = await spi_transact_with_pulse_record(
            spi, "go", SPI_CMD_GO, dummy
        )
        assert widths == [1]

    # 010 STATUS.
    dut.sequencer_done.value = 1
    assert await spi.transaction(SPI_CMD_STATUS, 0) == spi_expected_status(done=1)

    # 011 BUFFER: 32 clocks, no instruction/go pulse.
    await spi.load_host_buffer([0x11, 0x22, 0x33, 0x44])
    assert int(dut.command_latched.value) == SPI_CMD_BUFFER
    assert int(dut.instruction_valid.value) == 0
    assert int(dut.go.value) == 0

    # 100..111 direct accumulator reads.
    for lane, expected in enumerate((0x1111, 0x2222, 0x3333, 0x4444)):
        assert await spi.transaction(SPI_CMD_ACC0 + lane, 0) == expected

    # Back-to-back EXEC frames still restart from CS framing only.
    for word in (0x0102, 0x0304, 0x0506, 0x0708):
        _, widths, samples = await spi_transact_with_pulse_record(
            spi, "instruction_valid", SPI_CMD_EXEC, word
        )
        assert widths == [1] and samples == [word]

# ===========================================================================
# TEST_TOP  (FIXED TINY TAPEOUT TOPLEVEL=tb)
# ===========================================================================

def top_core_handle(dut):
    """Return production Clementine top beneath TinyTapeout tb, or dut itself."""
    try:
        return dut.tt_um_bigmanraffa_clm
    except AttributeError:
        return dut

# ===========================================================================
# MOV_HOST FULL-CHIP TESTS
# ===========================================================================

async def mov_host_top_reset(dut):
    await ensure_clock(dut, 20)
    await Timer(1, unit="ns")
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0

    spi = SpiMaster(dut, top_level=True)
    spi.set_command(SPI_CMD_STATUS)
    spi.set_sclk(0)
    spi.set_mosi(0)
    spi.set_cs(1)

    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    await Timer(5 if gate_level() else 1, unit="ns")

    assert value_is_resolvable(dut.uo_out)
    assert (int(dut.uo_out.value) & 1) == 1
    return spi

@dataclass
class KernelRunObservation:
    saw_go: bool
    saw_active: bool
    cycles_active: int
    replay_samples: int
    pc_holds_while_active: int
    final_pc: Optional[int]

async def _monitor_real_go_run(dut, execution_timeout_cycles=64, go_timeout_cycles=512):
    """Require a real run: halted -> active -> halted.

    RTL additionally observes the internal GO pulse, replay state and logical PC.
    GL cannot rely on synthesized-away internal nets, so the externally visible
    DONE bit dropping is the proof that the GO transaction actually started the
    machine. If GO does nothing, DONE stays high and the pre-run timeout fires.
    """
    is_gl = gate_level()
    core = None if is_gl else top_core_handle(dut)
    saw_go = False
    saw_active = False
    cycles_active = 0
    replay_samples = 0
    pc_holds = 0
    prev_pc = None
    pre_go_cycles = 0

    while True:
        await RisingEdge(dut.clk)
        # Gate-level cells are compiled with UNIT_DELAY=#1. Sample external
        # outputs after a small in-cycle settle window instead of at the exact
        # clock edge; RTL keeps zero added delay.
        if is_gl:
            await Timer(5, unit="ns")
        await ReadOnly()

        # DONE is a real chip output (uo_out[0]) and therefore exists in RTL
        # and in the flattened gate-level netlist.
        done = bit(int(dut.uo_out.value), 0)

        if is_gl:
            go_now = 0
            replay_now = 0
            pc = None
        else:
            go_now = int(core.go.value)
            replay_now = int(core.replay_state.value)
            try:
                pc = int(core.fetch_seq.logical_pc.value)
            except Exception:
                pc = None

        if not saw_go:
            pre_go_cycles += 1
            if (not is_gl and go_now) or (is_gl and not done):
                # In RTL this means the actual GO pulse was seen. In GL the
                # equivalent external proof is DONE leaving the halted state.
                saw_go = True
            elif pre_go_cycles >= go_timeout_cycles:
                if is_gl:
                    raise AssertionError("GO transaction never caused DONE to leave halted state")
                raise AssertionError("GO pulse was never observed")

        if saw_go and not done:
            first_active_sample = not saw_active
            if first_active_sample:
                saw_active = True

            cycles_active += 1
            replay_samples += replay_now

            if (
                not first_active_sample
                and pc is not None
                and prev_pc is not None
                and pc == prev_pc
            ):
                pc_holds += 1
            prev_pc = pc

            if cycles_active > execution_timeout_cycles:
                raise AssertionError(
                    f"kernel stayed active longer than {execution_timeout_cycles} core cycles"
                )

        if saw_active and done:
            return KernelRunObservation(
                saw_go=True,
                saw_active=True,
                cycles_active=cycles_active,
                replay_samples=replay_samples,
                pc_holds_while_active=pc_holds,
                final_pc=pc,
            )

async def mov_host_go_and_wait_halt(
    dut,
    spi,
    execution_timeout_cycles=64,
    go_timeout_cycles=512,
    expected_final_pc=None,
    require_replay=False,
):
    # The monitor starts BEFORE GO. At 5 MHz the 16-clock GO SPI frame alone is
    # about 160 core clocks, so GO detection has a separate large timeout. The
    # shorter execution timeout begins only after the actual GO pulse is seen.
    monitor = cocotb.start_soon(
        _monitor_real_go_run(
            dut,
            execution_timeout_cycles=execution_timeout_cycles,
            go_timeout_cycles=go_timeout_cycles,
        )
    )
    await Timer(1, unit="ns")
    await spi.transaction(SPI_CMD_GO, 0)
    observation = await monitor

    # _monitor_real_go_run() returns immediately after a ReadOnly sample of
    # DONE. Leave ReadOnly before a caller is allowed to drive ui_in/uio_in
    # for the next BUFFER/EXEC/read transaction.
    await Timer(1, unit="ns")

    assert observation.saw_go
    assert observation.saw_active
    if expected_final_pc is not None and observation.final_pc is not None:
        assert observation.final_pc == expected_final_pc, (
            f"final logical PC expected {expected_final_pc}, got {observation.final_pc}"
        )
    if require_replay:
        # IMPLEMENTATION REGRESSION ONLY. Architectural correctness does not
        # require a particular replay-state encoding or visible PC-hold shape.
        assert not gate_level(), "require_replay is RTL-only; GL tests must check architectural results"
        assert observation.replay_samples >= 1, "bank-conflict run never entered current replay mechanism"
        assert observation.pc_holds_while_active >= 1, "current capture/replay mechanism never held logical PC"
    return observation

@cocotb.test()
async def laneid_all_four_lanes_end_to_end(dut):
    """ARCHITECTURE PROOF: LANEID exposes physical lane IDs 0/1/2/3 at pins."""
    try:
        spi = await mov_host_top_reset(dut)

        # Copy LANEID through a GPR into the externally readable accumulator so
        # the same proof works on RTL and the flattened gate-level netlist.
        kernel = exact_kernel([
            LANEID(1),
            CLRACC(),
            LDAC(1, high=0),
            HALT(),
        ])
        await spi_load_kernel(spi, kernel)
        await mov_host_go_and_wait_halt(dut, spi, expected_final_pc=4)

        got = [await spi_read_acc(spi, lane) for lane in range(4)]
        assert got == [0, 1, 2, 3], f"LANEID expected [0,1,2,3], got {got}"
    finally:
        stop_test_clock()

@cocotb.test(skip=gate_level())
async def impl_regression_bank_conflict_replay_full_chip_shape(dut):
    """IMPLEMENTATION REGRESSION: current full-chip replay mechanism is visible.

    This test intentionally depends on RTL-visible replay/PC shape and is skipped
    for GL. The architectural same-bank SUB result is proven separately at pins.
    """
    try:
        spi = await mov_host_top_reset(dut)

        a = [0x21, 0x80, 0x05, 0xFF]
        b = [0x03, 0x7F, 0x09, 0x01]

        await spi.load_host_buffer(a)
        await spi_load_kernel(spi, exact_kernel([MOV_HOST(1), HALT()]))
        await mov_host_go_and_wait_halt(dut, spi, expected_final_pc=2)

        await spi.load_host_buffer(b)
        kernel = exact_kernel([
            MOV_HOST(3),
            SUB(5, 1, 3),
            CLRACC(),
            LDAC(5, high=0),
            HALT(),
        ])
        await spi_load_kernel(spi, kernel)
        obs = await mov_host_go_and_wait_halt(
            dut,
            spi,
            expected_final_pc=5,
            require_replay=True,
        )

        assert obs.replay_samples == 1, (
            f"one conflicted instruction should produce exactly one replay cycle, "
            f"observed {obs.replay_samples}"
        )

        expected = [u8(x - y) for x, y in zip(a, b)]
        got = [await spi_read_acc(spi, lane) for lane in range(4)]
        assert got == expected, f"replay SUB expected {expected}, got {got}"
    finally:
        stop_test_clock()

@cocotb.test()
async def bank_conflict_same_bank_sub_architectural_result(dut):
    """ARCHITECTURE PROOF: same-bank SUB produces the correct four lane results.

    No replay-state, operand-hold, PC-hold, internal cycle count or GPR hierarchy
    is required. The result is copied to accumulators and read through SPI.
    """
    try:
        spi = await mov_host_top_reset(dut)

        a = [0x21, 0x80, 0x05, 0xFF]
        b = [0x03, 0x7F, 0x09, 0x01]

        await spi.load_host_buffer(a)
        await spi_load_kernel(spi, exact_kernel([MOV_HOST(1), HALT()]))
        await mov_host_go_and_wait_halt(dut, spi, expected_final_pc=2)

        await spi.load_host_buffer(b)
        await spi_load_kernel(spi, exact_kernel([
            MOV_HOST(3),
            SUB(5, 1, 3),
            CLRACC(),
            LDAC(5, high=0),
            HALT(),
        ]))
        await mov_host_go_and_wait_halt(dut, spi, expected_final_pc=5)

        expected = [u8(x - y) for x, y in zip(a, b)]
        got = [await spi_read_acc(spi, lane) for lane in range(4)]
        assert got == expected, f"same-bank SUB expected {expected}, got {got}"
    finally:
        stop_test_clock()

@cocotb.test()
async def simt_ifp_else_reconvergence_architectural_kernel(dut):
    """ARCHITECTURE PROOF: real lanes take different IFP/ELSE paths and reconverge.

    A setup kernel preloads branch constants. The divergence kernel then loads
    accumulator=0x11 on the true side and accumulator=0x22 on the false side.
    Final values are read only through SPI, so this proof survives GL flattening.
    """
    try:
        spi = await mov_host_top_reset(dut)

        # Preload branch constants without relying on any internal GPR probe.
        await spi_load_kernel(spi, exact_kernel([
            LDI(4, 0x11),
            LDI(5, 0x22),
            HALT(),
        ]))
        await mov_host_go_and_wait_halt(dut, spi, expected_final_pc=3)

        # All 8 static slots are used.
        kernel = exact_kernel([
            LANEID(1),
            LDI(2, 2),
            CMP(0b00, 1, 2),   # signed LT: lanes 0/1 true, 2/3 false
            IFP(5),
            LDAC(4, high=0),   # true side -> 0x11
            ELSE(7),
            LDAC(5, high=0),   # false side -> 0x22
            HALT(),            # reached at reconvergence boundary
        ])

        await spi_load_kernel(spi, kernel)
        await mov_host_go_and_wait_halt(
            dut,
            spi,
            execution_timeout_cycles=64,
            go_timeout_cycles=512,
            expected_final_pc=8,
        )

        got = [await spi_read_acc(spi, lane) for lane in range(4)]
        assert got == [0x11, 0x11, 0x22, 0x22], (
            f"IFP/ELSE lane split expected [17,17,34,34], got {got}"
        )
    finally:
        stop_test_clock()

@cocotb.test()
async def mov_host_real_chain_lane0_first_order_full_overwrite_and_no_fetch_write(dut):
    """ARCHITECTURE PROOF: BUFFER order/overwrite and no instruction corruption."""
    try:
        spi = await mov_host_top_reset(dut)

        first = [0x12, 0x34, 0x56, 0x78]
        await spi.load_host_buffer(first, spi_hz=5_000_000)

        kernel = exact_kernel([
            MOV_HOST(1),
            CLRACC(),
            LDAC(1, high=0),
            HALT(),
        ])
        await spi_load_kernel(spi, kernel)
        await mov_host_go_and_wait_halt(dut, spi, expected_final_pc=4)
        got_first = [await spi_read_acc(spi, lane) for lane in range(4)]
        assert got_first == first, f"first BUFFER load expected {first}, got {got_first}"

        # A complete second 32-bit BUFFER load must overwrite all four slices.
        # Do NOT upload instructions again: bare GO must rerun the same kernel.
        # Therefore this also proves BUFFER did not write/corrupt the fetch image.
        second = [0x80, 0xFF, 0x00, 0x7F]
        await spi.load_host_buffer(second, spi_hz=1_000_000, phase_offset_ns=7)
        await mov_host_go_and_wait_halt(dut, spi, expected_final_pc=4)
        got_second = [await spi_read_acc(spi, lane) for lane in range(4)]
        assert got_second == second, (
            f"second BUFFER overwrite/rerun expected {second}, got {got_second}"
        )
    finally:
        stop_test_clock()

@cocotb.test()
async def mov_host_decoder_contract_and_normal_ldi_restored_full_width(dut):
    """Prove MOV_HOST payload-ignore and restored full-width LDI at chip boundary."""
    try:
        spi = await mov_host_top_reset(dut)

        staged = [0x12, 0x34, 0x56, 0x78]
        await spi.load_host_buffer(staged)

        # Dirty payload proves bits[8:1] do not supply MOV_HOST's data value.
        word = MOV_HOST_RAW(5, 0xD6)
        mov_kernel = exact_kernel([
            word,
            CLRACC(),
            LDAC(5, high=0),
            HALT(),
        ])

        await spi_load_kernel(spi, mov_kernel)

        await mov_host_go_and_wait_halt(
            dut,
            spi,
            expected_final_pc=4,
        )

        got_host = [
            await spi_read_acc(spi, lane)
            for lane in range(4)
        ]

        assert got_host == staged, (
            f"MOV_HOST_RAW payload affected host value: "
            f"expected {staged}, got {got_host}"
        )

        # Normal LDI must still carry the entire 8-bit immediate.
        normal = LDI(5, 0xFF)
        ldi_kernel = exact_kernel([
            normal,
            CLRACC(),
            LDAC(5, high=0),
            HALT(),
        ])

        await spi_load_kernel(spi, ldi_kernel)

        await mov_host_go_and_wait_halt(
            dut,
            spi,
            expected_final_pc=4,
        )

        got_ldi = [
            await spi_read_acc(spi, lane)
            for lane in range(4)
        ]

        assert got_ldi == [0x00FF] * 4, (
            f"full-width LDI expected {[0xFF] * 4}, "
            f"got {got_ldi}"
        )

    finally:
        stop_test_clock()

@cocotb.test()
async def mov_host_four_arbitrary_lane_bytes_two_sources_add_end_to_end(dut):
    """Stage two independent 4-byte vectors and perform real four-lane ADD."""
    try:
        spi = await mov_host_top_reset(dut)

        a = [0x11, 0x80, 0xFE, 0x7F]
        b = [0x01, 0xFF, 0x05, 0x81]
        expected = [u8(x + y) for x, y in zip(a, b)]

        # Stage A and copy all four lane-local bytes into R1 simultaneously.
        await spi.load_host_buffer(a)
        stage_a = exact_kernel([
            MOV_HOST(1),
            HALT(),
        ])
        await spi_load_kernel(spi, stage_a)
        await mov_host_go_and_wait_halt(dut, spi, expected_final_pc=2)

        # New staging data does not disturb R1. Copy B into R2, add, expose R3.
        await spi.load_host_buffer(b)
        stage_b = exact_kernel([
            MOV_HOST(2),
            ADD(3, 1, 2),
            CLRACC(),
            LDAC(3, high=0),
            HALT(),
        ])
        await spi_load_kernel(spi, stage_b)
        await mov_host_go_and_wait_halt(dut, spi, expected_final_pc=5)

        got = [await spi_read_acc(spi, lane) for lane in range(4)]
        assert got == expected, (
            f"MOV_HOST ADD expected {expected}, got {got}"
        )
    finally:
        stop_test_clock()

@cocotb.test()
async def mov_host_buffer_persists_and_can_feed_multiple_registers_without_reload(dut):
    """MOV_HOST reads staging state; it does not consume or clear the buffer."""
    try:
        spi = await mov_host_top_reset(dut)
        values = [0x00, 0x7F, 0x80, 0xFF]
        await spi.load_host_buffer(values)

        kernel = exact_kernel([
            MOV_HOST(1),
            MOV_HOST(2),
            ADD(3, 1, 2),
            CLRACC(),
            LDAC(3, high=0),
            HALT(),
        ])
        await spi_load_kernel(spi, kernel)
        await mov_host_go_and_wait_halt(dut, spi, expected_final_pc=6)

        expected = [u8(v + v) for v in values]
        got = [await spi_read_acc(spi, lane) for lane in range(4)]
        assert got == expected
    finally:
        stop_test_clock()

@cocotb.test()
async def normal_ldi_broadcast_full_8bit_ignores_staged_host_bytes(dut):
    """host_mode=0 must make ordinary LDI completely independent of host buffer."""
    try:
        spi = await mov_host_top_reset(dut)
        await spi.load_host_buffer([0x00, 0x55, 0xAA, 0xFF])

        kernel = exact_kernel([
            LDI(4, 0xD3),
            CLRACC(),
            LDAC(4, high=0),
            HALT(),
        ])
        await spi_load_kernel(spi, kernel)
        await mov_host_go_and_wait_halt(dut, spi, expected_final_pc=4)

        got = [await spi_read_acc(spi, lane) for lane in range(4)]
        assert got == [0x00D3] * 4
    finally:
        stop_test_clock()

@cocotb.test()
async def hardware_mirror_single_mac_runs_once(dut):
    """Mirror the exact hardware sequence: load 5 words, GO, read lane0."""
    try:
        spi = await mov_host_top_reset(dut)

        for word in [0x8000, 0x9202, 0x9402, 0x7050, 0xF000]:
            await spi.transaction(SPI_CMD_EXEC, word)

        await spi.transaction(SPI_CMD_GO, 0)
        await ClockCycles(dut.clk, 200)

        acc = await spi_read_acc(spi, 0)
        assert acc == 1, f"MAC ran {acc} times, expected exactly 1"

    finally:
        stop_test_clock()