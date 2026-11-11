"""Read-only candidate register interpretations and bounded capture profiles.

This module has no HA, transport, filesystem, or device dependencies. Addresses
are zero-based numeric words *after* transport decoding, not wire byte offsets.
Candidate GridBOSS record195 and FlexBOSS record252 are not installed-image
identities. Native sources cited below are independent evidence, not a claim
that a successful decoder invocation validates the map or a transaction.

The lower-priority additions require separate live qualification on deployed
0.2.4. Historical reads cover initialized containing ranges, not exact new
subrange timing. Only the listed request shapes are profiles; known_ranges is
an inventory, never permission to scan it or construct arbitrary requests.

GridBOSS current remains unsigned raw RMS: the existing HA signed /10 A and
public unsigned /100 A interpretations disagree. Neither is calibrated here;
this decoder does not change legacy HA values. Energy counters are aggregates,
not per-leg kWh: generator daily is50 and total84/85, not daily49. Corrected
GPIO holdings are4500..4509; historical4516 is outside initialized evidence.
"""

from collections.abc import Sequence
from typing import NamedTuple

REGISTER_MAP_REVISION = "candidate-gridboss195-flexboss252-2026-11-10.v1"

# The recorder owns scheduling. None means the configured inverter BMS period.
LOCAL_INVERTER_PRIMARY_BLOCKS = ((4, 0, 40),)
LOCAL_INVERTER_DYNAMIC_BLOCKS = ((4, 40, 40), (4, 120, 26))
GRIDBOSS_CURRENT_INTERPRETATION = (
    "unverified: legacy signed16/10 A; candidate unsigned RMS; "
    "ampere scale unresolved"
)
LOCAL_GRIDBOSS_PRIMARY_BLOCKS = ((4, 0, 40), (4, 120, 12))
LOCAL_GRIDBOSS_DYNAMIC_BLOCKS = ((4, 500, 100),)
# Optional short block read between the others while the recorder's GridBOSS
# fast lane is active: fast flags 568/569, selector 572, received control 577,
# packed status 578/579, battery 580/581, request state 585..587, warm-up 588.
# It is a sub-range of the dynamic 500/100 block, not a new register surface.
LOCAL_GRIDBOSS_FAST_BLOCK = (4, 568, 21)
LOCAL_INVERTER_MAINTENANCE = (
    (4, 80, 40, None),
    (3, 110, 125, 60.0),
    (3, 0, 20, 300.0),
    (3, 20, 90, 300.0),
    (3, 235, 102, 300.0),
    (4, 146, 94, 300.0),
    (3, 500, 112, 300.0),
    (3, 612, 112, 300.0),
    (3, 5000, 18, 300.0),
)
LOCAL_GRIDBOSS_MAINTENANCE = (
    (3, 4500, 10, 5.0),
    (3, 0, 20, 300.0),
    (3, 20, 90, 300.0),
    (3, 110, 125, 300.0),
    (3, 235, 88, 300.0),
    (3, 2000, 108, 300.0),
    (4, 40, 80, 300.0),
    (3, 4097, 41, 300.0),
)

_ROOT = "investigations/2026-11-08-firmware-reverse-engineering/"
_SURFACES = _ROOT + "direct-session/history-and-read-surfaces.md"
_CROSSCHECK = _ROOT + "register-map-crosscheck/native-comparison.md"
_GRID_STATE = _ROOT + "state-logging/firmware/REPORT.md"
_FLEX_SIGNALS = _ROOT + "direct-session/extended-signal-producers.md"
_FLEX_DECISIONS = _ROOT + "decision-tree/flexboss/REPORT.md"
_GRID_MAP = _ROOT + "state-logging/firmware/register-map.json"
_GRID_NATIVE = _ROOT + "state-logging/firmware/query2/instructions.tsv"
_GRID_PRODUCER = _ROOT + "decision-tree/gridboss-inputs/extraction/instructions.tsv"

# (function, start, count, transport). Adjacent Flex configuration banks remain
# separate because start500 and start612 each select their own serializer copy.
_KNOWN_RANGES = {
    "gridboss": (
        (4, 0, 132, "local_wrapper_or_standard"),
        (4, 500, 100, "local_wrapper_or_standard"),
        (3, 0, 323, "local_wrapper_or_standard"),
        (3, 2000, 108, "local_wrapper"),
        (3, 4097, 41, "local_wrapper"),
        (3, 4500, 10, "local_wrapper"),
    ),
    "inverter": (
        (4, 0, 240, "local_wrapper_or_standard"),
        (3, 0, 337, "local_wrapper_or_standard"),
        (3, 500, 112, "local_wrapper"),
        (3, 612, 112, "local_wrapper"),
        (3, 5000, 18, "local_wrapper_or_standard"),
    ),
}
_PROFILES = {
    "gridboss": (
        LOCAL_GRIDBOSS_PRIMARY_BLOCKS,
        LOCAL_GRIDBOSS_DYNAMIC_BLOCKS,
        LOCAL_GRIDBOSS_MAINTENANCE,
    ),
    "inverter": (
        LOCAL_INVERTER_PRIMARY_BLOCKS,
        LOCAL_INVERTER_DYNAMIC_BLOCKS,
        LOCAL_INVERTER_MAINTENANCE,
    ),
}


def _family(role: str) -> str:
    if role == "gridboss":
        return "gridboss"
    if role in ("master", "slave", "flexboss_master", "flexboss_slave"):
        return "inverter"
    return "unknown"


def capture_profile(role: str) -> dict:
    """Return a fresh JSON-friendly candidate inventory, not acquisition proof."""
    family = _family(role)
    primary, dynamic, maintenance = _PROFILES.get(family, ((), (), ()))
    return {
        "schema": "eg4_register_capture_profile.v1",
        "revision": REGISTER_MAP_REVISION,
        "family": family,
        "identity_status": "candidate_not_installed_match" if family != "unknown" else "unknown",
        "known_ranges": [
            {
                "function": function,
                "register": register,
                "count": count,
                "transport": transport,
                "evidence": [_SURFACES + ":84-119,162-166"],
            }
            for function, register, count, transport in _KNOWN_RANGES.get(family, ())
        ],
        "primary": [list(shape) for shape in primary],
        "dynamic": [list(shape) for shape in dynamic],
        "maintenance": [list(shape) for shape in maintenance],
        "fast": [list(LOCAL_GRIDBOSS_FAST_BLOCK)] if family == "gridboss" else [],
        "qualification": "reference_only; exact new shapes unqualified on live0.2.4",
        "request_constraints": [
            "Profiles use the local wrapper; inventory ranges are not request shapes.",
            "No blind scans, exact-start224/248, GridBOSS132..499 or4516, or5000/40.",
            "Flex500/112 and612/112 are separate exact-start local copies.",
            "Historical initialized-range reads do not qualify new exact-shape live performance.",
        ],
    }


class _Field(NamedTuple):
    name: str
    register: int
    width: int
    encoding: str
    unit: str | None
    status: str
    evidence: tuple[str, ...]


_FIELDS: dict[str, dict[tuple[int, int], _Field]] = {"gridboss": {}, "inverter": {}}


def _define(
    family: str,
    function: int,
    register: int,
    name: str,
    evidence: tuple[str, ...],
    *,
    width: int = 1,
    encoding: str = "raw",
    unit: str | None = None,
    status: str = "candidate_raw_units_unverified",
) -> None:
    field = _Field(name, register, width, encoding, unit, status, evidence)
    for address in range(register, register + width):
        _FIELDS[family][function, address] = field


_VOLTAGE_EVIDENCE = (_CROSSCHECK + ":68-91", _GRID_MAP + "#input1-9")
_CURRENT_EVIDENCE = (
    _CROSSCHECK + ":78-89",
    _GRID_PRODUCER + "#080361c8,08036dc0-08036dee",
)
_POWER_EVIDENCE = (_CROSSCHECK + ":93-108", _GRID_NATIVE + "#08021f3e,08021f42")
_ENERGY_EVIDENCE = (
    _CROSSCHECK + ":110-180",
    _GRID_PRODUCER + "#08032214,080324aa-08032524",
    _GRID_NATIVE + "#080221a0,0802245e,08022476",
)
_STATE_EVIDENCE = (_GRID_STATE + ":74-132", _GRID_MAP + "#input120-131,500-599")

for _register, _name in enumerate(
    (
        "grid_rms_voltage", "ups_rms_voltage", "generator_rms_voltage",
        "grid_l1_rms_voltage", "grid_l2_rms_voltage",
        "ups_l1_rms_voltage", "ups_l2_rms_voltage",
        "generator_l1_rms_voltage", "generator_l2_rms_voltage",
    ),
    start=1,
):
    _define("gridboss", 4, _register, _name, _VOLTAGE_EVIDENCE,
            encoding="tenths", unit="V", status="candidate_existing_voltage_convention")

_CIRCUITS = ("grid", "load", "generator", "ups", "smart_port1", "smart_port2", "smart_port3", "smart_port4")
for _index, _circuit in enumerate(_CIRCUITS):
    for _leg in (1, 2):
        _define("gridboss", 4, 10 + 2 * _index + _leg - 1,
                f"{_circuit}_l{_leg}_rms_current_raw", _CURRENT_EVIDENCE,
                status="candidate_unsigned_rms_calibration_disputed")
        _define("gridboss", 4, 26 + 2 * _index + _leg - 1,
                f"{_circuit}_l{_leg}_active_power", _POWER_EVIDENCE,
                encoding="signed16", unit="W", status="candidate_signed_power")

_ENERGY_CIRCUITS = (
    "load", "ups", "to_grid", "to_user", "generator",
    "smart_port1", "smart_port2", "smart_port3", "smart_port4",
    "ac_port1", "ac_port2", "ac_port3", "ac_port4",
)
for _index, _circuit in enumerate(_ENERGY_CIRCUITS):
    _daily = 42 + 2 * _index
    _total = 68 + 4 * _index
    _define("gridboss", 4, _daily, f"{_circuit}_daily_aggregate_raw",
            _ENERGY_EVIDENCE, status="candidate_aggregate_kwh_scale_unverified")
    _define("gridboss", 4, _total, f"{_circuit}_total_aggregate_raw",
            _ENERGY_EVIDENCE, width=2, encoding="low_first_u32",
            status="candidate_aggregate_kwh_scale_unverified")
    for _reserved in (_daily + 1, _total + 2, _total + 3):
        _define("gridboss", 4, _reserved, f"reserved_zero_{_reserved}",
                _ENERGY_EVIDENCE, encoding="reserved_zero", status="candidate_reserved_literal_zero")

for _register, _name in ((128, "phase_lock_frequency"), (129, "grid_frequency"), (130, "generator_frequency")):
    _define("gridboss", 4, _register, _name,
            (_CROSSCHECK + ":182-188", _GRID_NATIVE + "#0802281e-0802284e"),
            encoding="hundredths", unit="Hz", status="candidate_reported_frequency")

for _register, _name in (
    (120, "controller_b7c0_raw"), (121, "controller_b7b7_raw"),
    (126, "source_selector"), (127, "start_stop_arbitration_bits"),
    (131, "smart_port_configured_roles"),
    (567, "controller_b789_raw"), (568, "controller_00be_raw"),
    (569, "controller_00bf_raw"), (572, "source_selector_duplicate"),
    (574, "controller_00a6_raw"), (575, "controller_00a8_raw"),
    (576, "configuration_a621_bit0"), (577, "decision_predicate_bits"),
    (578, "controller_b530_raw"), (579, "controller_b540_raw"),
    (580, "controller_b788_raw"), (587, "controller_b564_raw"),
    (588, "warmup_ready_raw"),
):
    _define("gridboss", 4, _register, _name, _STATE_EVIDENCE)
for _register, _name in ((122, "aggregate_fault_bits"), (124, "aggregate_warning_bits")):
    _define("gridboss", 4, _register, _name, _STATE_EVIDENCE, width=2, encoding="low_first_u32")
for _index, _circuit in enumerate(("smart_port1", "smart_port2", "smart_port3", "smart_port4", "grid", "generator")):
    _define("gridboss", 4, 500 + _index, f"{_circuit}_close_command_path_count", _STATE_EVIDENCE)
    _define("gridboss", 4, 514 + _index, f"{_circuit}_last_written_decision",
            (_GRID_STATE + ":30-68",), encoding="state_reason",
            status="candidate_last_written_not_event_or_contact_state")
for _register in range(520, 555):
    _define("gridboss", 4, _register, f"reserved_zero_{_register}",
            _STATE_EVIDENCE, encoding="reserved_zero", status="candidate_reserved_literal_zero")

_IDENTITY_EVIDENCE = (_CROSSCHECK + ":55-60", _GRID_MAP + "#holding2-9,229")
_define("gridboss", 3, 2, "serial", _IDENTITY_EVIDENCE,
        width=5, encoding="text_low_byte_first", status="candidate_identity_not_image_attestation")
_define("gridboss", 3, 7, "firmware_version", _IDENTITY_EVIDENCE,
        width=3, encoding="version", status="candidate_identity_not_image_attestation")
_define("gridboss", 3, 229, "smart_load1_grid_on_configuration", _IDENTITY_EVIDENCE,
        encoding="bit4", status="candidate_configuration_not_contact_feedback")
for _register, _name in (
    (18, "operating_override_raw"), (20, "smart_port_configured_roles"),
    (37, "generator_voltage_lower_bound_raw"), (38, "generator_voltage_upper_bound_raw"),
    (170, "test_override_raw"), (226, "generator_warmup_configuration_raw"),
    (2102, "warning_mask_raw"),
):
    _define("gridboss", 3, _register, _name, (_CROSSCHECK + ":190-199", _SURFACES + ":158"))
for _register, _name in (
    (4097, "grid_rms_voltage_gain_raw"), (4098, "ups_rms_voltage_gain_raw"),
    (4099, "generator_rms_voltage_gain_raw"),
    (4110, "generator_l1_rms_current_gain_raw"), (4111, "generator_l2_rms_current_gain_raw"),
):
    _define("gridboss", 3, _register, _name, (_SURFACES + ":96", _CROSSCHECK + ":87-89"))
_define("gridboss", 3, 4500, "gpio_test_state_raw", (_SURFACES + ":110-119",))
for _register, _name in (
    (4501, "gpio_40011800_odr_bit11_command"), (4502, "gpio_40011800_odr_bit13_command"),
    (4503, "gpio_40011800_odr_bit8_command"), (4504, "gpio_40011800_odr_bit7_command"),
    (4505, "gpio_40011800_odr_bit15_command"), (4506, "gpio_40011800_odr_bit14_command"),
    (4508, "gpio_40011800_odr_bit1_command"), (4509, "gpio_40011400_odr_bit6_command"),
):
    _define("gridboss", 3, _register, _name, (_SURFACES + ":110-119",),
            status="candidate_output_command_not_contact_feedback")
_define("gridboss", 3, 4507, "reserved_zero_4507", (_SURFACES + ":119",),
        encoding="reserved_zero", status="candidate_reserved_literal_zero")

# Only independently traced causal labels are promoted for Flex. In particular
# 38/39 remain unknown raw, and no units are guessed for converter diagnostics.
for _register, _name in (
    (0, "converter_mode_status_raw"), (6, "redirected_fault_warning_status_raw"),
    (60, "arm_warning_low_raw"), (61, "converter_warning_high_raw"),
    (62, "arm_fault_low_raw"), (63, "converter_fault_high_raw"),
    (77, "derived_permission_context_raw"), (95, "selected_bms_permission_mask_raw"),
    (105, "packed_generator_request_context_raw"),
    (120, "converter_window_channel_127ac_raw"),
    (121, "converter_window_channel_11884_raw"),
    (122, "converter_selected_period_channel_raw"),
    (127, "converter_window_channel_11899_raw"),
    (128, "converter_window_channel_1189a_raw"),
    (142, "quantized_converter_command_pair_raw"),
    (143, "quantized_converter_float_state_pair_raw"),
    (144, "packed_limiter_result_codes_raw"),
    (145, "previous_mode_and_substate_diagnostic_raw"),
):
    _define("inverter", 4, _register, _name,
            (_FLEX_SIGNALS + ":15-84", _FLEX_DECISIONS + ":291-309"))
for _register, _name in (
    (110, "microgrid_shared_battery_flags_raw"),
    (137, "local_128_sample_channel_average_raw"),
    (170, "generator_command_word_raw"), (177, "generator_rating_configuration_raw"),
    (179, "boost_charge_discharge_flags_raw"),
    (194, "generator_start_voltage_threshold_raw"),
    (195, "generator_stop_voltage_threshold_raw"),
    (196, "generator_start_soc_threshold_raw"),
    (197, "generator_stop_soc_threshold_raw"), (226, "gridboss_role_flags_raw"),
):
    _define("inverter", 3, _register, _name,
            (_FLEX_DECISIONS + ":24-38,291-309", _FLEX_SIGNALS + ":98-100"))
for _register in range(5000, 5016):
    _define("inverter", 3, _register, f"received_node_mirror_or_command_staging_{_register}_raw",
            (_FLEX_SIGNALS + ":86-96",))
for _register in (5016, 5017):
    _define("inverter", 3, _register, f"transient_outbound_payload_{_register}_raw",
            (_FLEX_SIGNALS + ":86-96",))


def _decode_value(field: _Field, words: list[int]):
    encoding = field.encoding
    first = words[0]
    if encoding == "low_first_u32":
        return first + (words[1] << 16)  # Python integers cannot signed-overflow.
    if encoding == "signed16":
        return first - 65536 if first >= 32768 else first
    if encoding == "tenths":
        return first / 10
    if encoding == "hundredths":
        return first / 100
    if encoding == "bit4":
        return bool(first & 0x0010)
    if encoding == "state_reason":
        return {"state": first & 0xFF, "reason": first >> 8}
    if encoding in ("text_low_byte_first", "version"):
        text_words = words[:2] if encoding == "version" else words
        text = "".join(chr(byte) for word in text_words for byte in (word & 0xFF, word >> 8))
        return f"{text}-{words[2]:X}" if encoding == "version" else text
    return first


def decode_registers(role: str, function: int, register: int, values: Sequence[int]) -> list[dict]:
    """Project every supplied word without filling gaps or judging acquisition.

    A partial multiword field has value=None, status=incomplete, and lists only
    supplied addresses/raw words; expected_addresses identifies the full field.
    Invalid words (including bool) are retained verbatim with value=None and
    invalid_raw status, taking precedence over incompleteness. The caller owns
    source time, transaction validity, semantic rejection and retention quality.
    Unknown/uninitialized words are preserved, never silently coerced or masked.
    """
    family = _family(role)
    fields = _FIELDS.get(family, {})
    result = []
    index = 0
    while index < len(values):
        address = register + index
        field = fields.get((function, address))
        if field is None:
            initialized = any(
                function == fn and start <= address < start + count
                for fn, start, count, _transport in _KNOWN_RANGES.get(family, ())
            )
            field = _Field(
                f"unknown_raw_{function}_{address}", address, 1, "raw", None,
                "unknown_raw" if initialized or family == "unknown" else "uninitialized_raw",
                (_SURFACES + ":84-119",) if initialized else (),
            )
        count = min(field.register + field.width - address, len(values) - index)
        words = list(values[index:index + count])
        addresses = list(range(address, address + count))
        complete = address == field.register and count == field.width
        valid = all(type(word) is int and 0 <= word <= 0xFFFF for word in words)
        status = field.status
        value = None
        if not valid:
            status = "invalid_raw"
        elif not complete:
            status = "incomplete"
        else:
            value = _decode_value(field, words)
            if field.encoding == "reserved_zero" and value != 0:
                status = "candidate_reserved_literal_zero_mismatch"
        result.append({
            "name": field.name,
            "function": function,
            "register": field.register,
            "addresses": addresses,
            "raw": words,
            "value": value,
            "unit": field.unit,
            "interpretation_status": status,
            "evidence": list(field.evidence),
            "expected_addresses": list(range(field.register, field.register + field.width)),
            "register_map_revision": REGISTER_MAP_REVISION,
            "identity_status": "candidate_not_installed_match" if family != "unknown" else "unknown",
        })
        index += count
    return result
