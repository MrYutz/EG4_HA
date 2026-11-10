"""Tests for bounded local dongle trace logging."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from custom_components.eg4_monitor.local_dongle import (
    EG4LocalTelemetry,
)
from custom_components.eg4_monitor.local_dongle_trace import (
    ControllerObservationJournal,
    ControllerObservationTracker,
    LocalDongleTraceWriter,
    PersistenceResult,
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
    assert decoded["schema"] == "eg4_local_dongle_trace.v2"
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


@pytest.mark.asyncio
async def test_journal_disk_failure_recovers_identical_observation(tmp_path) -> None:
    root = tmp_path / "blocked"
    root.write_text("not a directory")
    writer = LocalDongleTraceWriter(root, session_id="synthetic-journal")
    journal = ControllerObservationJournal(writer)
    sample = _controller_sample()
    try:
        await journal.async_observe(sample)
        assert await writer.async_sync() is False
        failed = journal.health
        assert failed["State"] == "degraded"
        assert failed["ContinuityBroken"] is True
        assert failed["WriteErrors"] == 1
        assert failed["PersistedRecords"] == 0
        assert failed["PersistedRoles"] == []
        root.unlink()
        await journal.async_observe(sample)
        assert await writer.async_sync() is True
        recovered = journal.health
        assert recovered["State"] == "healthy"
        assert recovered["ContinuityBroken"] is False
        assert recovered["WriteErrors"] == 1
        assert recovered["LastError"] == failed["LastError"]
        assert recovered["LastSuccessfulPersistence"] is not None
        assert recovered["PersistedRoles"] == ["gridboss"]
        saved = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
        assert len(saved) == 1
        assert saved[0]["observation"] == "recovery_baseline"
        assert saved[0]["session_id"] == "synthetic-journal"
        assert saved[0]["controller"]["input"]["519"] == 0x0504
    finally:
        await writer.async_close()


@contextmanager
def _gated_appends(monkeypatch, destination):
    """Delay only the external append boundary, preserving genuine file output."""
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    original = Path.open

    def open_path(path, mode="r", *args, **kwargs):
        if path == destination and mode == "ab":
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(10):
                raise TimeoutError("synthetic append gate was not released")
        return original(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", open_path)
        try:
            yield entered, release
        finally:
            release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limits",
    [{"max_queue_records": 1}, {"max_queue_records": 16, "max_queue_bytes": 4096}],
    ids=["record-count", "serialized-bytes"],
)
async def test_trace_writer_bounds_inflight_work_and_retains_loss(
    tmp_path, monkeypatch, limits,
) -> None:
    writer = LocalDongleTraceWriter(tmp_path, **limits)
    receipts: list[PersistenceResult] = []
    callback_threads = []
    loop_thread = threading.get_ident()

    def completed(result):
        callback_threads.append(threading.get_ident())
        receipts.append(result)

    try:
        with _gated_appends(monkeypatch, writer.active_path) as (entered, release):
            assert await writer.async_write(
                {"sequence": 1, "payload": "x" * 2048},
                wait=False, on_complete=completed,
            ) == writer.active_path
            await asyncio.wait_for(entered.wait(), 2)
            queued = writer.health
            assert queued["State"] == "starting"
            assert queued["QueueDepth"] == 1
            assert 2048 < queued["QueuedBytes"] <= writer.max_queue_bytes
            assert queued["PersistedRecords"] == 0
            assert queued["LastSuccessfulPersistence"] is None
            assert receipts == []
            assert await writer.async_write(
                {"sequence": 2, "payload": "x" * 2048},
                wait=False, on_complete=completed,
            ) is None
            failed = writer.health
            assert failed["DroppedRecords"] == 1
            assert failed["QueueDepth"] == 1
            assert failed["QueuedBytes"] == queued["QueuedBytes"]
            release.set()
            assert await writer.async_sync() is True
        # This write predates the queue loss: it must not clear the newer loss.
        assert writer.health["State"] == "degraded"
        assert len(receipts) == 1
        assert receipts[0].path == writer.active_path
        assert receipts[0].error is None
        assert receipts[0].completed_at
        assert callback_threads == [loop_thread]
        assert await writer.async_write({"sequence": 3}) == writer.active_path
        recovered = writer.health
        assert recovered["State"] == "healthy"
        assert recovered["DroppedRecords"] == 1
        assert recovered["WriteErrors"] == 0
        assert recovered["LastError"] == failed["LastError"]
        assert recovered["QueueDepth"] == recovered["QueuedBytes"] == 0
        saved = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
        assert [row["sequence"] for row in saved] == [1, 3]
    finally:
        await writer.async_close()


@pytest.mark.asyncio
async def test_journal_queue_loss_requires_new_generation_recovery(
    tmp_path, monkeypatch,
) -> None:
    writer = LocalDongleTraceWriter(tmp_path, max_queue_records=1)
    journal = ControllerObservationJournal(writer)
    transitions = []
    journal.set_health_callback(lambda: transitions.append(journal.health["State"]))
    sample = _controller_sample()
    try:
        with _gated_appends(monkeypatch, writer.active_path) as (entered, release):
            await journal.async_observe(sample)
            await asyncio.wait_for(entered.wait(), 2)
            sample["diagnostics"][0]["values"][19] = 0x0604
            await journal.async_observe(sample)
            assert journal.health["PendingObservations"] == 1
            assert journal.health["ContinuityBroken"] is True
            assert journal.health["PersistedRoles"] == []
            release.set()
            assert await writer.async_sync() is True
        # The older baseline was saved, but it cannot acknowledge the newer loss.
        assert journal.health["ContinuityBroken"] is True
        assert journal.health["State"] == "degraded"
        with _gated_appends(monkeypatch, writer.active_path) as (entered, release):
            await journal.async_observe(sample)
            await asyncio.wait_for(entered.wait(), 2)
            for _ in range(5):
                await journal.async_observe(sample)
            assert journal.health["DroppedRecords"] == 1
            assert journal.health["PendingObservations"] == 1
            # Lose a newer change while the recovery baseline is still in flight.
            sample["diagnostics"][0]["values"][19] = 0x0704
            await journal.async_observe(sample)
            assert journal.health["DroppedRecords"] == 2
            release.set()
            assert await writer.async_sync() is True
        assert journal.health["ContinuityBroken"] is True
        await journal.async_observe(sample)
        assert await writer.async_sync() is True
        assert journal.health["State"] == "healthy"
        assert journal.health["ContinuityBroken"] is False
        assert journal.health["PendingObservations"] == 0
        assert journal.health["DroppedRecords"] == 2
        assert transitions == ["degraded", "healthy"]
        saved = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
        assert [row["controller"]["input"]["519"] for row in saved] == [
            0x0504, 0x0604, 0x0704,
        ]
        assert [row["observation"] for row in saved] == [
            "baseline", "recovery_baseline", "recovery_baseline",
        ]
        assert saved[-1]["changed_fields"] == []
    finally:
        await writer.async_close()


@pytest.mark.asyncio
async def test_journal_preserves_pending_reason_changes_in_order(
    tmp_path, monkeypatch,
) -> None:
    writer = LocalDongleTraceWriter(tmp_path)
    journal = ControllerObservationJournal(writer)
    sample = _controller_sample()
    try:
        with _gated_appends(monkeypatch, writer.active_path) as (entered, release):
            await journal.async_observe(sample)
            await asyncio.wait_for(entered.wait(), 2)
            for reason in (0x0604, 0x0704):
                sample["diagnostics"][0]["values"][19] = reason
                await journal.async_observe(sample)
            await journal.async_observe(sample)
            assert journal.health["PendingObservations"] == 3
            assert journal.health["PersistedRoles"] == []
            release.set()
            assert await writer.async_sync() is True
        saved = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
        assert [row["controller"]["input"]["519"] for row in saved] == [
            0x0504, 0x0604, 0x0704,
        ]
        assert [row["changed_fields"] for row in saved[1:]] == [
            ["input.519"], ["input.519"],
        ]
        assert journal.health["PersistedRoles"] == ["gridboss"]
        assert journal.health["PendingObservations"] == 0
    finally:
        await writer.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("low_bit", [1, 2], ids=["bit0", "bit1"])
async def test_journal_persists_input6_bits_but_not_high_bit_or_analog_noise(
    tmp_path, low_bit,
) -> None:
    values = [0] * 40
    values[0], values[6] = 33280, 0x4000
    analog = [0] * 26
    sample = {
        "role": "flexboss_master", "ok": False,
        "primary_blocks": [{
            "function": 4, "register": 0, "count": 40, "ok": True,
            "semantic": "rejected", "values": values,
            "transaction_id": "synthetic-rejected-core",
            "started_at": "2026-11-09T12:00:00+00:00",
            "completed_at": "2026-11-09T12:00:01+00:00",
        }],
        "diagnostics": [{
            "function": 4, "register": 120, "count": 26,
            "ok": True, "values": analog,
        }],
    }
    writer = LocalDongleTraceWriter(tmp_path)
    journal = ControllerObservationJournal(writer)
    try:
        await journal.async_observe(sample)
        assert await writer.async_sync() is True
        values[6] = 0x8000
        analog[22], analog[23] = 123, 456
        await journal.async_observe(sample)
        assert await writer.async_sync() is True
        assert journal.health["PersistedRecords"] == 1
        values[6] |= low_bit
        await journal.async_observe(sample)
        assert await writer.async_sync() is True
        saved = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
        assert [row["controller"]["input"]["6"] for row in saved] == [
            0x4000, 0x8000 | low_bit,
        ]
        assert saved[-1]["changed_fields"] == ["input.6.bits_0_1"]
        assert saved[-1]["controller"]["input6_semantics"] == {
            "trigger_mask": 3, "basis": "candidate_firmware",
            "installed_semantics": "unverified", "physical_meaning": "unknown",
        }
        assert all(row["availability"]["primary"] is False for row in saved)
        assert saved[-1]["primary_blocks"][0]["semantic"] == "rejected"
        assert saved[-1]["primary_blocks"][0]["transaction_id"] == (
            "synthetic-rejected-core"
        )
        assert saved[-1]["diagnostics"][0]["values"][22:24] == [123, 456]
    finally:
        await writer.async_close()


@pytest.mark.asyncio
async def test_sync_delivers_receipts_before_offloop_fsync_and_retains_failure(
    tmp_path, monkeypatch,
) -> None:
    writer = LocalDongleTraceWriter(tmp_path)
    receipts = []
    original = os.fsync
    loop_thread = threading.get_ident()
    fail_once = True
    sync_threads = []
    receipt_counts_at_sync = []
    receipt_paths_at_sync = []

    def sync(descriptor):
        nonlocal fail_once
        sync_threads.append(threading.get_ident())
        receipt_counts_at_sync.append(len(receipts))
        receipt_paths_at_sync.append(receipts[0].path if receipts else None)
        if fail_once:
            fail_once = False
            raise OSError("synthetic filesystem synchronization failure")
        return original(descriptor)

    monkeypatch.setattr(os, "fsync", sync)
    try:
        assert await writer.async_write(
            {"sequence": 1}, wait=False, on_complete=receipts.append,
        ) == writer.active_path
        assert await writer.async_sync() is False
        failed = writer.health
        assert failed["WriteErrors"] == 1
        assert failed["PersistedRecords"] == 1
        assert failed["State"] == "degraded"
        assert await writer.async_sync() is True
        assert writer.health["State"] == "degraded"
        assert await writer.async_write({"sequence": 2}) == writer.active_path
        assert writer.health["State"] == "healthy"
        assert writer.health["WriteErrors"] == 1
        assert writer.health["LastError"] == failed["LastError"]
        assert sync_threads
        assert all(identity != loop_thread for identity in sync_threads)
        assert all(count == 1 for count in receipt_counts_at_sync)
        assert all(path == writer.active_path for path in receipt_paths_at_sync)
    finally:
        await writer.async_close()


@pytest.mark.asyncio
async def test_cancelled_waiter_and_close_still_persist_and_deliver_receipt(
    tmp_path, monkeypatch,
) -> None:
    writer = LocalDongleTraceWriter(tmp_path)
    receipts = []
    closing_started = asyncio.Event()

    async def close():
        closing_started.set()
        await writer.async_close()

    try:
        with _gated_appends(monkeypatch, writer.active_path) as (entered, release):
            waiter = asyncio.create_task(writer.async_write(
                {"sequence": 1}, on_complete=receipts.append,
            ))
            await asyncio.wait_for(entered.wait(), 2)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            closer = asyncio.create_task(close())
            await closing_started.wait()
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer
            assert receipts == []
            release.set()
            await writer.async_close()
        assert len(receipts) == 1
        assert receipts[0].path == writer.active_path
        assert receipts[0].error is None
        saved = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
        assert [row["sequence"] for row in saved] == [1]
        assert saved[0]["queued_at"] <= saved[0]["persistence_started_at"]
        assert saved[0]["persistence_started_at"] <= receipts[0].completed_at
        assert writer.health["QueueDepth"] == writer.health["QueuedBytes"] == 0
        assert await writer.async_sync() is True
    finally:
        await writer.async_close()


@pytest.mark.asyncio
async def test_rejected_encoding_size_and_closed_output_have_no_receipts(
    tmp_path,
) -> None:
    writer = LocalDongleTraceWriter(tmp_path, max_queue_bytes=512)
    receipts = []
    try:
        assert await writer.async_write(
            {"payload": "x" * 4096}, wait=False, on_complete=receipts.append,
        ) is None
        assert await writer.async_write(
            {"invalid": float("nan")}, wait=False, on_complete=receipts.append,
        ) is None
        assert writer.health["DroppedRecords"] == 2
        assert writer.health["PersistedRecords"] == 0
        assert writer.health["QueueDepth"] == writer.health["QueuedBytes"] == 0
        assert receipts == []
        assert not writer.active_path.exists()
        assert await writer.async_write(
            {"sequence": 3}, on_complete=receipts.append,
        ) == writer.active_path
        await writer.async_close()
        assert await writer.async_write(
            {"sequence": 4}, wait=False, on_complete=receipts.append,
        ) is None
        assert len(receipts) == 1
        assert writer.health["DroppedRecords"] == 3
        assert writer.health["PersistedRecords"] == 1
        assert writer.health["State"] == "degraded"
        assert writer.health["LastError"] == "trace writer is closed"
        saved = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
        assert [row["sequence"] for row in saved] == [3]
    finally:
        await writer.async_close()


@pytest.mark.asyncio
async def test_write_failure_receipt_runs_on_source_loop_and_preserves_history(
    tmp_path,
) -> None:
    root = tmp_path / "blocked"
    root.write_text("not a directory")
    writer = LocalDongleTraceWriter(root)
    receipts = []
    callback_threads = []
    loop_thread = threading.get_ident()

    def completed(result):
        receipts.append(result)
        callback_threads.append(threading.get_ident())

    try:
        assert await writer.async_write(
            {"sequence": 1}, on_complete=completed,
        ) is None
        assert len(receipts) == 1
        assert receipts[0].path is None
        assert receipts[0].error is not None
        assert receipts[0].completed_at
        assert callback_threads == [loop_thread]
        failed = writer.health
        assert failed["PersistedRecords"] == 0
        assert failed["LastSuccessfulPersistence"] is None
        assert failed["WriteErrors"] == 1
        root.unlink()
        assert await writer.async_write(
            {"sequence": 2}, on_complete=completed,
        ) == writer.active_path
        assert len(receipts) == 2
        assert callback_threads == [loop_thread, loop_thread]
        assert writer.health["State"] == "healthy"
        assert writer.health["WriteErrors"] == 1
        assert writer.health["LastError"] == failed["LastError"]
        saved = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
        assert [row["sequence"] for row in saved] == [2]
    finally:
        await writer.async_close()
