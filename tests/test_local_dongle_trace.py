"""Tests for bounded local dongle trace logging."""

from __future__ import annotations

import json

import pytest

from custom_components.eg4_monitor.local_dongle import (
    EG4LocalTelemetry,
)
from custom_components.eg4_monitor.local_dongle_trace import (
    ControllerObservationTracker,
    LocalDongleTraceWriter,
    telemetry_to_record,
)


def _telemetry() -> EG4LocalTelemetry:
    return EG4LocalTelemetry(
        host="127.0.0.1",
        port=8000,
        dongle_serial="DJ12345678",
        inverter_serial="INV1234567",
        battery_voltage=53.1,
        battery_soc=28.0,
        battery_soh=100.0,
        charge_power=3000.0,
        discharge_power=0.0,
        bms_parallel_count=5,
        bms_capacity_ah=1400.0,
        bms_current=25.0,
        bms_max_cell_voltage=3.33,
        bms_min_cell_voltage=3.32,
        bms_max_cell_temperature=31.0,
        bms_min_cell_temperature=29.0,
        bms_cycle_count=18.0,
        bms_available=True,
        status_code=0x20,
        grid_voltage=238.0,
        grid_frequency=60.0,
        core_registers=tuple(range(40)),
        bms_registers=tuple(range(40)),
    )


@pytest.mark.asyncio
async def test_trace_writer_writes_jsonl_without_secrets(tmp_path) -> None:
    writer = LocalDongleTraceWriter(tmp_path)
    record = {
        "sequence": 1,
        "role": "flexboss_master",
        "ok": True,
        "telemetry": telemetry_to_record(_telemetry()),
    }

    path = await writer.async_write(record)

    assert path == tmp_path / "local-dongle.jsonl"
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    decoded = json.loads(lines[0])
    assert decoded["schema"] == "eg4_local_dongle_trace.v1"
    assert decoded["telemetry"]["core"]["charge_power_w"] == 3000.0
    assert "password" not in lines[0].lower()
    assert "token" not in lines[0].lower()
    await writer.async_close()


@pytest.mark.asyncio
async def test_trace_writer_rotates_and_prunes(tmp_path) -> None:
    writer = LocalDongleTraceWriter(tmp_path, max_bytes=64 * 1024, max_files=2)
    payload = {"payload": "x" * 3000}

    for sequence in range(60):
        await writer.async_write({"sequence": sequence, **payload})

    files = sorted(tmp_path.glob("local-dongle*.jsonl"))
    assert writer.active_path.exists()
    assert len(files) <= 2
    assert all(path.stat().st_size <= 64 * 1024 for path in files)
    await writer.async_close()


def _controller_sample(
    *, role: str = "gridboss", decision: int = 0x0504
) -> dict:
    values = [0] * 100
    values[19] = decision
    return {
        "role": role,
        "sequence": 1,
        "ok": True,
        "sample_started_at": "2026-11-08T12:00:00+00:00",
        "sample_completed_at": "2026-11-08T12:00:01+00:00",
        "telemetry": {
            "decoded": {"generator_voltage_v": 240.0},
            "transfer": {
                "state": "generator_to_ups_candidate",
                "input_registers_valid": True,
            },
            "raw": {"frequency_registers_start_120": [0] * 12},
        },
        "diagnostics": [
            {
                "function": 4,
                "register": 500,
                "count": 100,
                "started_at": "2026-11-08T12:00:00.500+00:00",
                "completed_at": "2026-11-08T12:00:00.600+00:00",
                "duration_ms": 100.0,
                "ok": True,
                "values": values,
            }
        ],
        "ha_states": {"unrelated": "do not retain"},
    }


def test_controller_observation_catches_reason_without_state_change() -> None:
    tracker = ControllerObservationTracker()
    sample = _controller_sample()
    baseline = tracker.observe(sample)
    assert baseline is not None
    assert baseline["observation"] == "baseline"
    assert baseline["controller"]["decisions"]["519"] == {
        "state_byte": 4,
        "reason_byte": 5,
    }
    sample["diagnostics"][0]["values"][19] = 0x0604
    sample["sequence"] = 2
    change = tracker.observe(sample)
    assert change is not None
    assert change["observation"] == "controller_changed"
    assert change["controller"]["decisions"]["519"] == {
        "state_byte": 4,
        "reason_byte": 6,
    }
    assert "input.519" in change["changed_fields"]
    assert change["diagnostics"][0]["started_at"] == (
        "2026-11-08T12:00:00.500+00:00"
    )
    assert change["sample_completed_at"] == "2026-11-08T12:00:01+00:00"
    assert change["telemetry"]["decoded"]["generator_voltage_v"] == 240.0
    assert "ha_states" not in change
    assert baseline["controller"]["decisions"]["519"]["reason_byte"] == 5


def test_controller_observation_distinguishes_missing_and_zero() -> None:
    tracker = ControllerObservationTracker()
    sample = _controller_sample(decision=0)
    sample["diagnostics"][0].update(
        ok=False, values=None, error="TimeoutError: read timed out"
    )
    baseline = tracker.observe(sample)
    assert baseline is not None
    assert baseline["controller"]["input"]["519"] is None
    assert baseline["controller"]["decisions"]["519"] == {
        "state_byte": None,
        "reason_byte": None,
    }
    sample["diagnostics"][0].update(ok=True, values=[0] * 100)
    sample["diagnostics"][0].pop("error")
    resumed = tracker.observe(sample)
    assert resumed is not None
    assert resumed["observation"] == "availability_resumed"
    assert resumed["controller"]["input"]["519"] == 0
    assert resumed["controller"]["decisions"]["519"] == {
        "state_byte": 0,
        "reason_byte": 0,
    }
    assert tracker.observe(sample) is None


def test_controller_observation_gap_and_resume_are_not_contact_transitions() -> None:
    tracker = ControllerObservationTracker()
    sample = _controller_sample()
    tracker.observe(sample)
    failed = {
        "role": "gridboss",
        "ok": False,
        "sequence": 2,
        "error": "TimeoutError: primary read",
    }
    lost = tracker.observe(failed)
    assert lost is not None
    assert lost["observation"] == "availability_lost"
    assert lost["controller"]["input"]["519"] is None
    assert tracker.observe(failed) is None
    resumed = tracker.observe(sample)
    assert resumed is not None
    assert resumed["observation"] == "availability_resumed"
    assert "transfer_transition" not in resumed
    assert tracker.observe(sample) is None


def test_controller_observation_suppresses_noise_and_tracks_roles() -> None:
    tracker = ControllerObservationTracker()
    sample = _controller_sample()
    tracker.observe(sample)
    sample["sequence"] = 100
    sample["sample_completed_at"] = "2026-11-08T12:10:00+00:00"
    sample["telemetry"]["decoded"]["generator_voltage_v"] = 239.7
    sample["diagnostics"][0]["duration_ms"] = 24.5
    sample["diagnostics"][0]["values"][93] = 123
    sample["diagnostics"][0]["values"][94] = 1
    assert tracker.observe(sample) is None
    other_role = _controller_sample(role="gridboss_secondary")
    assert tracker.observe(other_role)["observation"] == "baseline"
    sample["telemetry"]["transfer"]["input_registers_valid"] = False
    quality_change = tracker.observe(sample)
    assert quality_change is not None
    assert "transfer.input_registers_valid" in quality_change["changed_fields"]


def test_controller_observation_does_not_interpret_undefined_gpio_scratch() -> None:
    tracker = ControllerObservationTracker()
    sample = _controller_sample()
    sample["diagnostics"].append(
        {
            "function": 3,
            "register": 4516,
            "count": 10,
            "ok": True,
            "values": [0] * 10,
        }
    )
    tracker.observe(sample)
    sample["diagnostics"][-1]["values"][2] = 1
    assert tracker.observe(sample) is None


def test_controller_observation_retains_warning_only_eps_mismatch() -> None:
    tracker = ControllerObservationTracker()
    sample = {
        "role": "flexboss_slave",
        "ok": True,
        "telemetry": {"core": {"registers": [0] * 40}},
        "diagnostics": [
            {
                "function": 4, "register": 40, "count": 40,
                "ok": True, "values": [0] * 40,
            }
        ],
    }
    tracker.observe(sample)
    sample["diagnostics"][0]["values"][21] = 0x4000
    change = tracker.observe(sample)
    assert change is not None
    assert change["changed_fields"] == ["input.61"]


def test_controller_observation_retains_limiter_only_change() -> None:
    tracker = ControllerObservationTracker()
    values = [0] * 26
    values[23] = 22
    values[25] = 0x2720
    sample = {
        "role": "flexboss_master",
        "ok": True,
        "telemetry": {"core": {"registers": [0] * 40}},
        "diagnostics": [
            {
                "function": 4, "register": 120, "count": 26,
                "ok": True, "values": values,
            }
        ],
    }
    tracker.observe(sample)
    values[24] = 0x0600
    change = tracker.observe(sample)
    assert change is not None
    assert change["changed_fields"] == ["input.144"]


def test_controller_observation_captures_flexboss_faults_and_settings() -> None:
    tracker = ControllerObservationTracker()
    sample = {
        "role": "flexboss_master",
        "ok": True,
        "telemetry": {"core": {"registers": [0] * 40}},
        "diagnostics": [
            {
                "function": 4, "register": 40, "count": 40,
                "ok": True, "values": [0] * 40,
            },
            {
                "function": 3, "register": 110, "count": 125,
                "ok": True, "values": [0] * 125,
            },
        ],
    }
    tracker.observe(sample)
    sample["diagnostics"][0]["values"][22] = 8
    fault = tracker.observe(sample)
    assert fault is not None
    assert fault["controller"]["input"]["62"] == 8
    sample["diagnostics"][1]["values"][84] = 45
    setting = tracker.observe(sample)
    assert setting is not None
    assert setting["controller"]["holding"]["194"] == 45
    assert tracker.observe(sample) is None


@pytest.mark.asyncio
async def test_trace_writer_close_flushes_final_queued_record(tmp_path) -> None:
    writer = LocalDongleTraceWriter(tmp_path)
    for sequence in range(64):
        assert await writer.async_write({"sequence": sequence}, wait=False)
    await writer.async_close()
    lines = writer.active_path.read_text().splitlines()
    assert [json.loads(line)["sequence"] for line in lines] == list(range(64))
    await writer.async_close()
    assert await writer.async_write({"sequence": 64}, wait=False) is None


def test_controller_observation_failed_attempt_does_not_resume_missing_data() -> None:
    tracker = ControllerObservationTracker()
    sample = _controller_sample()
    sample["diagnostics"] = []
    tracker.observe(sample)
    sample["diagnostics"] = [
        {
            "function": 4,
            "register": 500,
            "count": 100,
            "ok": False,
            "values": None,
            "error": "TimeoutError: supplemental read",
        }
    ]
    failed = tracker.observe(sample)
    assert failed is not None
    assert failed["observation"] == "availability_changed"
    assert failed["controller"]["input"]["519"] is None
    assert failed["telemetry"]["decoded"]["generator_voltage_v"] == 240.0
    assert failed["ok"] is True
    assert tracker.observe(sample) is None
