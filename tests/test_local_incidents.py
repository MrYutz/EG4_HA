"""Exercise incident capture/retention through real temporary recorder storage."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.eg4_monitor.local_dongle_trace import (
    ControllerObservationJournal,
    LocalDongleTraceWriter,
)
from custom_components.eg4_monitor.local_incidents import (
    IncidentRecorder,
    RecorderLimits,
)


class Clock:
    """Advance receive time without sleeps or fabricated device clock ordering."""

    def __init__(self):
        self.value = datetime(2026, 11, 9, 12, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    """Supply a fresh deterministic receive clock."""
    return Clock()


def limits(**changes):
    """Keep temporary-storage reserves independent of the developer machine."""
    return replace(RecorderLimits(), min_free_bytes=0, **changes)


def recorder(tmp_path, clock, **changes):
    """Construct the public synthetic recorder without performing storage I/O."""
    return IncidentRecorder(
        tmp_path / "owned", entry_id="synthetic-entry", session_id="session-one",
        build_id="synthetic-build", roles={"gridboss", "flexboss_master"},
        clock=clock, limits=limits(**changes), evidence_class="synthetic",
    )


def grid(clock, *, power=5000, frequency=60, output=240, **extra):
    """Represent an actual producer acquisition shape, including 120-V leg context."""
    return {
        "schema": "eg4_local_dongle_trace.v2", "kind": "acquisition", "role": "gridboss",
        "session_id": "session-one", "acquisition_id": f"grid-{clock().isoformat()}", "ok": True,
        "sample_started_at": clock().isoformat(), "sample_completed_at": clock().isoformat(),
        "telemetry": {"decoded": {
            "generator_voltage_v": 239, "generator_frequency_hz": frequency,
            "generator_power_w": power, "ups_voltage_v": output,
            "ups_l1_voltage_v": 120, "ups_l2_voltage_v": 120,
        }}, **extra,
    }


def flex(clock, *, output=244, ok=True, **extra):
    """Represent the site EPS_r input20 report, not either 120-V leg channel."""
    return {
        "schema": "eg4_local_dongle_trace.v2", "kind": "acquisition", "role": "flexboss_master",
        "session_id": "session-one", "acquisition_id": f"flex-{clock().isoformat()}", "ok": ok,
        "sample_started_at": clock().isoformat(), "sample_completed_at": clock().isoformat(),
        "telemetry": {"core": {"eps_voltage_r_v": output, "eps_voltage_s_v": 0, "eps_voltage_t_v": None}},
        **extra,
    }


def manifest(engine):
    """Read the exported public bundle manifest, not owned recorder internals."""
    path = engine.health["LatestIncidentPath"]
    assert path is not None
    return json.loads((Path(path) / "manifest.json").read_text())


def rows(engine, kind="records"):
    """Reconstruct retained manifest-referenced plain JSONL files."""
    document = manifest(engine)
    root = Path(engine.health["LatestIncidentPath"])
    return [
        json.loads(line)
        for item in document["files"] if item["kind"] == kind and item["retained"]
        for line in (root / item["path"]).read_text().splitlines()
    ]


@pytest.mark.asyncio
async def test_pre_post_overlap_persistent_anomaly_and_all_role_order(tmp_path, clock):
    """A persistent outage cannot close on elapsed post time or retrigger per poll."""
    engine = recorder(tmp_path, clock, pre_seconds=10, post_seconds=30)
    await engine.async_start()
    engine.observe(grid(clock))
    engine.observe(flex(clock))
    clock.advance(10)
    engine.observe(grid(clock, power=0, frequency=0))
    engine.observe(flex(clock, output=170))
    await engine.async_tick()
    incident_id = engine.health["ActiveIncidentId"]
    initial_count = manifest(engine)["trigger_count"]
    clock.advance(100)
    engine.observe(grid(clock, power=0, frequency=0))
    engine.observe(flex(clock, output=170))
    await engine.async_tick()
    assert engine.health["ActiveIncidentId"] == incident_id
    assert manifest(engine)["trigger_count"] == initial_count
    engine.observe(grid(clock))
    engine.observe(flex(clock))
    await engine.async_tick()
    clock.advance(29)
    await engine.async_tick()
    assert engine.health["ActiveIncidentId"] == incident_id
    clock.advance(1)
    await engine.async_tick()
    document = manifest(engine)
    assert engine.health["ActiveIncidentId"] is None
    assert document["state"] == "closed"
    assert document["missing_roles"] == []
    assert document["coverage"]["pre_start"] == "2026-11-09T12:00:00+00:00"
    received = [row for row in rows(engine) if row.get("kind") == "acquisition"]
    assert [row["role"] for row in received[:4]] == ["gridboss", "flexboss_master", "gridboss", "flexboss_master"]
    reasons = {row["reason"] for row in rows(engine, "triggers")}
    assert {"accepted_generator_power_lost", "voltage_frequency_inconsistent", "output_voltage_anomaly"} <= reasons
    assert len(rows(engine, "triggers")) == document["trigger_count"]
    await engine.async_close()


@pytest.mark.asyncio
async def test_power_loss_independent_of_frequency_and_240v_not_legs(tmp_path, clock):
    """239 V/qualified0 Hz is not engine-off, and120-V legs are not undervoltage."""
    engine = recorder(tmp_path, clock)
    await engine.async_start()
    engine.observe(grid(clock, frequency=0))
    engine.observe(flex(clock))
    await engine.async_tick()
    engine.observe(grid(clock, power=0, frequency=0))
    await engine.async_tick()
    events = rows(engine, "triggers")
    assert any(row["reason"] == "accepted_generator_power_lost" for row in events)
    assert not any(row["reason"] == "output_voltage_anomaly" for row in events)
    assert all(row["details"].get("mechanical_state") != "off" for row in events)
    engine.observe(flex(clock, output=282.9))
    await engine.async_tick()
    output = [row for row in rows(engine, "triggers") if row["reason"] == "output_voltage_anomaly"]
    assert output[-1]["details"]["reported_voltage_v"] == 282.9
    assert output[-1]["details"]["channel"] == "eps_voltage_r_v"
    await engine.async_close()


@pytest.mark.asyncio
async def test_low_requires_previously_live_but_high_triggers_initially(tmp_path, clock):
    """A never-live zero is startup context; an initial high report is an anomaly."""
    engine = recorder(tmp_path, clock)
    await engine.async_start()
    engine.observe(flex(clock, output=0))
    engine.observe(grid(clock, output=0, power=0))
    await engine.async_tick()
    assert engine.health["ActiveIncidentId"] is None
    engine.observe(flex(clock, output=264))
    engine.observe(flex(clock, output=180))
    assert engine.health["ActiveIncidentId"] is None
    engine.observe(flex(clock, output=179.9))
    await engine.async_tick()
    assert rows(engine, "triggers")[0]["reason"] == "output_voltage_anomaly"
    await engine.async_close()
    other = IncidentRecorder(tmp_path / "high", entry_id="entry", session_id="high", build_id="b", roles={"flexboss_master"}, limits=limits(), clock=clock)
    await other.async_start()
    other.observe(flex(clock, output=264.1))
    await other.async_tick()
    assert rows(other, "triggers")[0]["details"]["previously_live"] is False
    await other.async_close()


@pytest.mark.asyncio
async def test_raw_rejected_eps_and_pending_semantics_do_not_fake_recovery(tmp_path, clock):
    """Raw input20 survives rejection, while not_evaluated never clears a verdict."""
    engine = recorder(tmp_path, clock)
    await engine.async_start()
    words = [0] * 40
    words[20], words[23] = 2829, 6000
    transaction = {
        "schema": "eg4_local_dongle_trace.v2", "kind": "transaction", "role": "flexboss_master",
        "transaction_id": "t1", "acquisition_id": "a1", "ok": True,
        "started_at": clock().isoformat(), "completed_at": clock().isoformat(),
        "request": {"function": 4, "register": 0, "count": 40},
        "frames": [{"classification": "matching", "values": words, "semantic": "not_evaluated"}],
    }
    engine.observe(transaction)
    engine.observe({**transaction, "kind": "interpretation", "semantic": "rejected", "error": {"type": "ValueError"}})
    await engine.async_tick()
    initial = manifest(engine)["trigger_count"]
    engine.observe({**transaction, "transaction_id": "t2"})
    await engine.async_tick()
    assert manifest(engine)["trigger_count"] == initial
    assert any(row["reason"] == "output_voltage_anomaly" for row in rows(engine, "triggers"))
    assert any(row["reason"] == "semantic_rejection" for row in rows(engine, "triggers"))
    engine.observe({**transaction, "transaction_id": "t2", "kind": "interpretation", "semantic": "accepted"})
    await engine.async_tick()
    assert any(row["reason"] == "semantic_rejection_recovered" for row in rows(engine, "triggers"))
    await engine.async_close()


@pytest.mark.asyncio
async def test_journal_input6_changes_availability_and_complete_trigger_history(tmp_path, clock):
    """The real discrete journal callback supplies every input6/reason edge."""
    engine = recorder(tmp_path, clock, segment_bytes=1200)
    await engine.async_start()
    writer = LocalDongleTraceWriter(engine.journal_root, max_files=None, write_guard=engine.write_guard("journal"))
    journal = ControllerObservationJournal(writer)
    journal.set_observation_callback(engine.observe)
    primary = [0] * 40
    diagnostic = [0] * 40

    async def send(word, *, primary_ok=True, diagnostic_ok=True):
        """Send acquisitions through the existing journal's public seam."""
        primary[6] = word
        sample = flex(clock, ok=primary_ok)
        sample["primary_blocks"] = [{"function": 4, "register": 0, "count": 40, "values": list(primary), "ok": primary_ok, "semantic": "accepted"}]
        sample["diagnostics"] = [{"function": 4, "register": 40, "count": 40, "values": list(diagnostic) if diagnostic_ok else None, "ok": diagnostic_ok}]
        await journal.async_observe(sample)

    await send(0)
    await writer.async_sync()
    await engine.async_tick()
    assert engine.health["ActiveIncidentId"] is None
    for index in range(90):
        clock.advance(1)
        await send(index % 2 + 1)
    await send(0, primary_ok=False, diagnostic_ok=False)
    await send(0)
    await writer.async_sync()
    await engine.async_tick()
    document = manifest(engine)
    events = rows(engine, "triggers")
    assert len(events) == document["trigger_count"]
    assert len(document["triggers"]) <= 64
    assert len(document["files"]) > 3
    assert any(row["reason"] == "controller_changed" and "input.6.bits_0_1" in row["details"]["changed_fields"] for row in events)
    assert any(row["reason"] == "primary_availability_lost" for row in events)
    assert any(row["reason"] == "diagnostic_availability_lost" for row in events)
    assert any(row["reason"] == "primary_availability_lost_recovered" for row in events)
    assert all("sha256" not in item for item in document["files"])
    await writer.async_close()
    await engine.async_close()


@pytest.mark.asyncio
async def test_time_and_byte_prebuffer_limits_partial_roles_and_original_config_age(tmp_path, clock):
    """An old good setting is not replaced by a failed attempt or a fresh timestamp."""
    engine = recorder(tmp_path, clock, pre_seconds=10, pre_max_bytes=1400, post_seconds=1)
    context = {"roles": {"gridboss": {"3:18:1": {
        "status": "failed", "latest_attempt": {"transaction_id": "failed", "age_seconds": 5},
        "last_success": {"transaction_id": "old-good", "completed_at": "2026-11-09T11:00:00+00:00", "age_seconds": 3600},
    }}}, "role_freshness": {"gridboss": {"primary": {"age_seconds": 2}}}}
    engine.set_context_provider(lambda: context)
    await engine.async_start()
    engine.observe(grid(clock, payload="old" * 200))
    clock.advance(11)
    engine.observe(grid(clock, payload="new" * 200))
    engine.observe(grid(clock, payload="newer" * 100))
    assert engine.health["PrebufferBytes"] <= 1400
    engine.observe(grid(clock, power=0))
    await engine.async_tick()
    document = manifest(engine)
    assert document["missing_roles"] == ["flexboss_master"]
    assert document["coverage"]["memory_truncated"] is True
    assert document["truncated"] is True
    saved = document["configuration_context"]
    assert saved["roles"]["gridboss"]["3:18:1"]["last_success"]["transaction_id"] == "old-good"
    assert saved["roles"]["gridboss"]["3:18:1"]["last_success"]["age_seconds"] == 3600
    assert saved["roles"]["gridboss"]["3:18:1"]["status"] == "failed"
    assert not any(row.get("payload", "").startswith("old") for row in rows(engine))
    await engine.async_close()


@pytest.mark.asyncio
async def test_rotation_final_hashes_and_directory_sync_custody(tmp_path, clock):
    """Growing files have no hash; every final hash matches retained actual bytes."""
    engine = recorder(tmp_path, clock, pre_seconds=0, post_seconds=1, segment_bytes=1400)
    await engine.async_start()
    engine.observe(grid(clock))
    engine.observe(flex(clock, output=282.9))
    for index in range(12):
        engine.observe(grid(clock, payload=str(index) * 300))
    await engine.async_tick()
    assert all(not item["finalized"] and "sha256" not in item for item in manifest(engine)["files"])
    engine.observe(flex(clock))
    clock.advance(1)
    await engine.async_tick()
    document = manifest(engine)
    assert document["state"] == "closed"
    assert len(document["files"]) > 4
    root = Path(engine.health["LatestIncidentPath"])
    for item in document["files"]:
        assert item["finalized"] is True
        actual = (root / item["path"]).read_bytes()
        assert item["bytes"] == len(actual)
        assert item["sha256"] == hashlib.sha256(actual).hexdigest()
    await engine.async_close()


@pytest.mark.asyncio
async def test_owned_only_age_and_byte_retention_preserves_unrelated_and_symlinks(tmp_path, clock):
    """A matching filename alone cannot authorize deleting unrelated evidence."""
    engine = recorder(tmp_path, clock, raw_max_bytes=3000, raw_max_age_seconds=10, segment_bytes=1000)
    await engine.async_start()
    unrelated = engine.raw_root / "local-dongle-123.jsonl"
    unrelated.write_text('{"not":"recorder evidence"}\n')
    backup = engine.root / "backups"
    backup.mkdir()
    (backup / "local-dongle.jsonl").write_text("sealed\n")
    outside = tmp_path / "outside.jsonl"
    outside.write_text("untouched\n")
    link = engine.raw_root / "local-dongle-124.jsonl"
    link.symlink_to(outside)
    writer = LocalDongleTraceWriter(engine.raw_root, max_bytes=1000, max_files=None, write_guard=engine.write_guard("raw"))
    for index in range(15):
        await writer.async_write(grid(clock, payload=str(index) * 200))
    await writer.async_sync()
    await engine.async_tick()
    assert engine.health["RawBytes"] <= 3000
    retained = [path for path in engine.raw_root.glob("local-dongle*.jsonl") if path not in (unrelated, link)]
    assert retained
    for path in retained:
        os.utime(path, (clock().timestamp() - 11, clock().timestamp() - 11))
    await engine.async_tick()
    assert engine.health["RawBytes"] == 0
    assert unrelated.read_text() == '{"not":"recorder evidence"}\n'
    assert link.is_symlink() and outside.read_text() == "untouched\n"
    assert (backup / "local-dongle.jsonl").read_text() == "sealed\n"
    await writer.async_close()
    await engine.async_close()


@pytest.mark.asyncio
async def test_actual_raw_storage_metrics_exclude_queued_and_sample_original_intervals(tmp_path, clock):
    """Only actual retained bytes/timestamps appear as coverage in health."""
    engine = recorder(tmp_path, clock)
    await engine.async_start()
    engine.observe(grid(clock))
    await engine.async_tick()
    assert engine.health["RawBytes"] == 0
    writer = LocalDongleTraceWriter(engine.raw_root, max_bytes=700, max_files=None, write_guard=engine.write_guard("raw"))
    await writer.async_write(grid(clock))
    await engine.async_tick()
    clock.advance(10)
    await writer.async_write(grid(clock))
    await engine.async_tick()
    actual = sum(path.stat().st_size for path in engine.raw_root.glob("*.jsonl"))
    assert engine.health["RawBytes"] == actual
    assert engine.health["RawOldest"] == "2026-11-09T12:00:00+00:00"
    assert engine.health["RawNewest"] == "2026-11-09T12:00:10+00:00"
    assert engine.health["EstimatedRawRetentionHours"] is not None
    await writer.async_close()
    await engine.async_close()


@pytest.mark.asyncio
async def test_free_reserve_prunes_raw_before_incident_and_rejects_unsafe_writes(tmp_path, clock, monkeypatch):
    """Real unlinks free controlled capacity; unrelated files remain untouchable."""
    engine = recorder(tmp_path, clock, segment_bytes=1200)
    engine.limits = replace(engine.limits, min_free_bytes=1000)
    await engine.async_start()
    raw = LocalDongleTraceWriter(engine.raw_root, max_bytes=1200, max_files=None, write_guard=engine.write_guard("raw"))
    for index in range(5):
        await raw.async_write(grid(clock, payload=str(index) * 300))
    journal_writer = LocalDongleTraceWriter(engine.journal_root, max_files=None, write_guard=engine.write_guard("journal"))
    journal = ControllerObservationJournal(journal_writer)
    await journal.async_observe(grid(clock))
    await journal_writer.async_sync()
    unrelated = engine.root / "keep-evidence.txt"
    unrelated.write_text("sealed, unrelated evidence")
    engine.observe(flex(clock, output=282.9))
    await engine.async_tick()
    before = set(engine.raw_root.glob("*.jsonl"))
    journal_before = {path: path.read_bytes() for path in engine.journal_root.glob("*.jsonl")}
    marker_before = json.loads((engine.root / "recorder.json").read_text())
    capacity = sum(path.stat().st_size for path in engine.root.rglob("*") if path.is_file()) + 1600

    def usage(path):
        """Model external disk capacity using actual bytes/unlinks in this fixture."""
        used = sum(item.stat().st_size for item in engine.root.rglob("*") if item.is_file())
        return SimpleNamespace(total=capacity, used=used, free=max(0, capacity - used))

    monkeypatch.setattr("custom_components.eg4_monitor.local_incidents.shutil.disk_usage", usage)
    engine.observe(grid(clock, payload="pressure" * 60))
    await engine.async_tick()
    assert set(engine.raw_root.glob("*.jsonl")) < before
    assert "retention_eviction" not in manifest(engine)["incomplete_reasons"]
    marker_after = json.loads((engine.root / "recorder.json").read_text())
    assert {key: marker_after[key] for key in ("schema", "entry_id", "created_at")} == {key: marker_before[key] for key in ("schema", "entry_id", "created_at")}
    assert all(path.read_bytes() == content for path, content in journal_before.items())
    capacity = 0
    engine.observe(grid(clock, payload="cannot fit" * 100))
    await engine.async_tick()
    assert engine.health["State"] == "degraded"
    assert engine.health["LastError"]
    assert unrelated.read_text() == "sealed, unrelated evidence"
    monkeypatch.undo()
    await raw.async_close()
    await journal_writer.async_close()
    await engine.async_close()


@pytest.mark.asyncio
async def test_incident_pressure_eviction_is_monotonic_across_active_manifest_rewrites(tmp_path, clock):
    """Open rotated segments count toward quota and forced loss is never overwritten."""
    engine = recorder(tmp_path, clock, incident_max_bytes=22000, segment_bytes=1800)
    await engine.async_start()
    engine.observe(flex(clock, output=282.9))
    for index in range(55):
        engine.observe(grid(clock, payload=f"{index:03}" * 200))
        if index % 5 == 0:
            await engine.async_tick()
    await engine.async_tick()
    document = manifest(engine)
    assert engine.health["IncidentBytes"] <= 22000
    assert document["complete"] is False and document["truncated"] is True
    assert "retention_eviction" in document["incomplete_reasons"]
    assert any(not item["retained"] for item in document["files"])
    old_gaps = document["gaps"]
    await engine.async_tick()
    updated = manifest(engine)
    assert "retention_eviction" in updated["incomplete_reasons"]
    assert all(gap in updated["gaps"] for gap in old_gaps)
    await engine.async_close()


@pytest.mark.asyncio
async def test_bounded_queue_and_serialization_loss_are_explicit_and_recoverable(tmp_path, clock):
    """Oversized received evidence cannot block acquisition or silently disappear."""
    engine = recorder(tmp_path, clock, max_queue_bytes=1800, max_queue_records=3)
    await engine.async_start()
    engine.observe(flex(clock, output=282.9, payload="x" * 3000))
    engine.observe({"kind": "acquisition", "role": "gridboss", "bad": float("nan")})
    assert engine.health["DroppedRecords"] >= 2
    assert engine.health["QueuedBytes"] <= 1800
    assert engine.health["QueueDepth"] <= 3
    await engine.async_tick()
    document = manifest(engine)
    assert document["complete"] is False and document["truncated"] is True
    assert "incident_spool_loss" in document["incomplete_reasons"]
    assert document.get("lost_record_count", 0) >= 2
    await engine.async_close()


@pytest.mark.asyncio
async def test_crash_restart_interrupts_old_bundle_and_new_session_warms_up(tmp_path, clock):
    """A prior open manifest is never turned into a complete tail on restart."""
    engine = recorder(tmp_path, clock)
    await engine.async_start()
    engine.observe(flex(clock, output=282.9))
    await engine.async_tick()
    old_path = Path(engine.health["LatestIncidentPath"])
    # Export/copy an actual open bundle to model the persisted filesystem after
    # abrupt process loss; close the first live owner before reopening the copy.
    copied = tmp_path / "restart"
    shutil.copytree(engine.root, copied)
    await engine.async_close()
    clock.advance(100)
    restarted = IncidentRecorder(copied, entry_id="synthetic-entry", session_id="session-two", build_id="b2", roles={"gridboss", "flexboss_master"}, limits=limits(), clock=clock, evidence_class="synthetic")
    await restarted.async_start()
    old = json.loads((copied / "incidents" / old_path.name / "manifest.json").read_text())
    assert old["state"] == "interrupted" and old["complete"] is False
    assert "recorder_restart" in old["incomplete_reasons"]
    assert old["coverage"]["actual_end"] == "2026-11-09T12:00:00+00:00"
    assert restarted.health["WarmingUp"] is True
    restarted.observe(flex(clock, output=282.9))
    await restarted.async_tick()
    fresh = manifest(restarted)
    assert fresh["session_id"] == "session-two"
    assert fresh["coverage"]["warmup"] is True
    assert any(isinstance(gap, dict) and gap.get("reason") == "recorder_restart" for gap in fresh["gaps"])
    await restarted.async_close()


@pytest.mark.asyncio
async def test_fsync_fault_never_leaves_final_hash_success(tmp_path, clock, monkeypatch):
    """An external synchronization failure is visible in health and saved custody."""
    engine = recorder(tmp_path, clock, pre_seconds=0, post_seconds=1)
    await engine.async_start()
    engine.observe(grid(clock))
    engine.observe(flex(clock, output=282.9))
    await engine.async_tick()
    engine.observe(flex(clock))
    clock.advance(1)
    finalized_path = Path(engine.health["LatestIncidentPath"])
    original = os.fsync

    def broken(descriptor):
        """Fail at the real operating-system synchronization boundary."""
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(os, "fsync", broken)
    await engine.async_tick()
    document = json.loads((finalized_path / "manifest.json").read_text())
    assert engine.health["State"] == "degraded"
    assert document["complete"] is False
    assert all(not item["finalized"] and "sha256" not in item for item in document["files"])
    assert any("sync" in reason for reason in document["incomplete_reasons"])
    monkeypatch.setattr(os, "fsync", original)
    await engine.async_close()


@pytest.mark.asyncio
async def test_symlink_destination_rejected_without_touching_external_file(tmp_path, clock):
    """A recorder-looking symlink cannot redirect append, ownership or pruning."""
    engine = recorder(tmp_path, clock)
    await engine.async_start()
    external = tmp_path / "external"
    external.write_text("preserved")
    (engine.raw_root / "local-dongle.jsonl").symlink_to(external)
    writer = LocalDongleTraceWriter(engine.raw_root, max_files=None, write_guard=engine.write_guard("raw"))
    assert await writer.async_write(grid(clock)) is None
    await engine.async_tick()
    assert external.read_text() == "preserved"
    assert engine.health["LastError"] is not None
    await writer.async_close()
    await engine.async_close()


@pytest.mark.asyncio
async def test_worker_filesystem_lock_never_blocks_observe_or_health(tmp_path, clock, monkeypatch):
    """A blocked actual fsync does not make source-loop observation take its lock."""
    engine = recorder(tmp_path, clock)
    await engine.async_start()
    engine.observe(flex(clock, output=282.9))
    await engine.async_tick()
    entered, release = threading.Event(), threading.Event()
    original = os.fsync

    def blocking(descriptor):
        """Hold an external filesystem boundary while the source loop progresses."""
        entered.set()
        if not release.wait(10):
            raise OSError("test failed to release filesystem boundary")
        return original(descriptor)

    monkeypatch.setattr(os, "fsync", blocking)
    pending = asyncio.create_task(engine.async_tick())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        engine.observe(grid(clock))
        assert engine.health["PrebufferBytes"] > 0
        assert not pending.done()
    finally:
        release.set()
        await pending
        monkeypatch.setattr(os, "fsync", original)
        await engine.async_close()


@pytest.mark.asyncio
async def test_staggered_diagnostic_baseline_is_not_an_outage_but_failed_attempt_is(tmp_path, clock):
    """Never attempted is None; real failed attempts and their resumption are edges."""
    engine = recorder(tmp_path, clock)
    await engine.async_start()
    writer = LocalDongleTraceWriter(engine.journal_root, max_files=None, write_guard=engine.write_guard("journal"))
    journal = ControllerObservationJournal(writer)
    journal.set_observation_callback(engine.observe)
    await journal.async_observe(flex(clock))
    sample = flex(clock, diagnostics=[{
        "function": 4, "register": 40, "count": 40,
        "ok": True, "values": [0] * 40,
    }])
    await journal.async_observe(sample)
    await writer.async_sync()
    await engine.async_tick()
    assert engine.health["ActiveIncidentId"] is None
    failed = flex(clock, diagnostics=[{
        "function": 4, "register": 40, "count": 40, "ok": False, "values": None,
    }])
    await journal.async_observe(failed)
    await journal.async_observe(failed)
    await writer.async_sync()
    await engine.async_tick()
    events = rows(engine, "triggers")
    assert len([row for row in events if row["reason"] == "diagnostic_availability_lost"]) == 1
    await journal.async_observe(sample)
    await writer.async_sync()
    await engine.async_tick()
    assert any(row["reason"] == "diagnostic_availability_lost_recovered" for row in rows(engine, "triggers"))
    await writer.async_close()
    await engine.async_close()


@pytest.mark.asyncio
async def test_recorder_loss_recovery_and_shutdown_flags_preserve_discrete_authority(tmp_path, clock):
    """Intentional role shutdown is not output loss; real storage loss still is."""
    engine = recorder(tmp_path, clock)
    await engine.async_start()
    engine.observe(grid(clock))
    engine.observe(flex(clock))

    def health(state, *, reason="periodic", primary=True):
        """Build the actual compact recorder health shape, not a physical reading."""
        return {
            "schema": "eg4_local_dongle_trace.v2", "kind": "recorder_health",
            "role": "recorder", "observed_at": clock().isoformat(), "reason": reason,
            "health": {
                "RawPersistence": {"State": state, "WriteErrors": 1 if state == "degraded" else 0},
                "JournalPersistence": {"State": "healthy"},
                "Roles": {"gridboss": {
                    "LastAttempt": clock().isoformat(), "PrimaryAvailable": primary,
                }},
            },
        }

    engine.observe(health("healthy", reason="shutdown", primary=False))
    assert engine.health["ActiveIncidentId"] is None
    engine.observe(health("degraded", reason="shutdown", primary=False))
    engine.observe(health("degraded", reason="shutdown", primary=False))
    await engine.async_tick()
    assert [row["reason"] for row in rows(engine, "triggers")].count("recorder_persistence_lost") == 1
    assert not any(row["reason"] == "primary_availability_lost" for row in rows(engine, "triggers"))
    engine.observe(health("healthy"))
    await engine.async_tick()
    assert any(row["reason"] == "recorder_persistence_lost_recovered" for row in rows(engine, "triggers"))
    await engine.async_close()


@pytest.mark.asyncio
async def test_first_trigger_configuration_is_preserved_and_later_context_is_saved(tmp_path, clock):
    """Updated observations supplement, rather than overwrite, first-trigger context."""
    engine = recorder(tmp_path, clock)
    version = {"value": 1}
    engine.set_context_provider(lambda: {
        "roles": {"gridboss": {"transaction_id": f"setting-{version['value']}", "age_seconds": 90}},
        "role_freshness": {},
    })
    await engine.async_start()
    engine.observe(flex(clock, output=282.9))
    await engine.async_tick()
    version["value"] = 2
    engine.observe(flex(clock))
    await engine.async_tick()
    assert manifest(engine)["configuration_context"]["roles"]["gridboss"]["transaction_id"] == "setting-1"
    contexts = [row for row in rows(engine) if row["kind"] == "incident_context"]
    assert {row["roles"]["gridboss"]["transaction_id"] for row in contexts} == {"setting-1", "setting-2"}
    await engine.async_close()


@pytest.mark.asyncio
async def test_external_hash_failure_is_not_finalized_success(tmp_path, clock, monkeypatch):
    """Hash custody fails independently of otherwise successful append/fsync."""
    engine = recorder(tmp_path, clock, pre_seconds=0, post_seconds=1)
    await engine.async_start()
    engine.observe(grid(clock))
    engine.observe(flex(clock, output=282.9))
    await engine.async_tick()
    exported = Path(engine.health["LatestIncidentPath"])
    engine.observe(flex(clock))
    clock.advance(1)

    original_open = Path.open

    class ReadFault:
        """Keep a real descriptor/fsync while failing the external byte-read seam."""

        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, *args):
            raise OSError("synthetic filesystem read failure during hashing")

    def faulty_open(path, mode="r", *args, **kwargs):
        """Open actual files, injecting failure only when exported bytes are read."""
        stream = original_open(path, mode, *args, **kwargs)
        if mode == "rb" and exported in path.parents and path.suffix == ".jsonl":
            return ReadFault(stream)
        return stream

    monkeypatch.setattr(Path, "open", faulty_open)
    await engine.async_tick()
    document = json.loads((exported / "manifest.json").read_text())
    assert document["complete"] is False
    assert "incident_hash_or_sync_failed" in document["incomplete_reasons"]
    assert all(not item["finalized"] and "sha256" not in item for item in document["files"])
    assert engine.health["State"] == "degraded"
    monkeypatch.undo()
    await engine.async_close()


@pytest.mark.asyncio
async def test_unowned_nonempty_root_is_never_adopted_or_pruned(tmp_path, clock):
    """A missing ownership marker is not permission to claim historical captures."""
    root = tmp_path / "owned"
    raw = root / "raw"
    raw.mkdir(parents=True)
    historical = raw / "local-dongle.jsonl"
    original = json.dumps(grid(clock)) + "\n"
    historical.write_text(original)
    engine = recorder(tmp_path, clock, raw_max_bytes=0)
    await engine.async_start()
    assert engine.health["State"] == "degraded"
    assert not (root / "recorder.json").exists()
    assert historical.read_text() == original
    await engine.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("original", [
    '["unrelated", "not recorder evidence"]\n', '{"unrelated":', "",
])
async def test_guard_never_appends_or_rotates_an_unrelated_active_basename(tmp_path, clock, original):
    """Reserved-looking names do not authorize changing non-recorder content."""
    engine = recorder(tmp_path, clock)
    await engine.async_start()
    unrelated = engine.raw_root / "local-dongle.jsonl"
    unrelated.write_text(original)
    writer = LocalDongleTraceWriter(engine.raw_root, max_bytes=1, max_files=None, write_guard=engine.write_guard("raw"))
    assert await writer.async_write(grid(clock)) is None
    assert unrelated.read_text() == original
    assert list(engine.raw_root.iterdir()) == [unrelated]
    await writer.async_close()
    await engine.async_close()


@pytest.mark.asyncio
async def test_symlink_root_cannot_claim_or_modify_external_storage(tmp_path, clock):
    """Ownership and pruning never follow a root symlink into another tree."""
    external = tmp_path / "external-storage"
    external.mkdir()
    preserved = external / "sealed.jsonl"
    preserved.write_text("original evidence")
    (tmp_path / "owned").symlink_to(external, target_is_directory=True)
    engine = recorder(tmp_path, clock, raw_max_bytes=0)
    await engine.async_start()
    assert engine.health["State"] == "degraded"
    assert preserved.read_text() == "original evidence"
    assert list(external.iterdir()) == [preserved]
    await engine.async_close()


@pytest.mark.asyncio
async def test_cancelled_tick_keeps_owned_finalization_ahead_of_shutdown(tmp_path, clock, monkeypatch):
    """Cancelling the publisher cannot close a writer ahead of queued custody work."""
    engine = recorder(tmp_path, clock, pre_seconds=0, post_seconds=1)
    await engine.async_start()
    engine.observe(grid(clock))
    engine.observe(flex(clock, output=282.9))
    await engine.async_tick()
    exported = Path(engine.health["LatestIncidentPath"])
    engine.observe(flex(clock))
    clock.advance(1)
    entered, release = threading.Event(), threading.Event()
    original = os.fsync

    def blocking(descriptor):
        """Hold the real fsync while its source-loop caller is cancelled."""
        entered.set()
        if not release.wait(10):
            raise OSError("test failed to release finalization")
        return original(descriptor)

    monkeypatch.setattr(os, "fsync", blocking)
    tick = asyncio.create_task(engine.async_tick())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        tick.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tick
        closing = asyncio.create_task(engine.async_close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await closing
    finally:
        release.set()
        monkeypatch.setattr(os, "fsync", original)
        await engine.async_close()
    document = json.loads((exported / "manifest.json").read_text())
    assert document["state"] == "closed"
    assert document["files"]
    for item in document["files"]:
        assert item["finalized"] is True
        assert item["sha256"] == hashlib.sha256((exported / item["path"]).read_bytes()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [5000, -5000])
async def test_accepted_power_uses_magnitude_and_preserves_signed_boundaries(tmp_path, clock, initial):
    """Direction changes are not loss; either sign obeys the same100/50-W band."""
    engine = recorder(tmp_path, clock)
    await engine.async_start()
    for power in (initial, -initial, 100 if initial > 0 else -100, 75, -75, 50):
        engine.observe(grid(clock, power=power))
        clock.advance(1)
    await engine.async_tick()
    events = rows(engine, "triggers")
    losses = [row for row in events if row["reason"] == "accepted_generator_power_lost"]
    assert len(losses) == 1
    assert losses[0]["details"]["power_w"] == 50
    engine.observe(grid(clock, power=-75))
    engine.observe(grid(clock, power=-100))
    engine.observe(grid(clock, power=100))
    engine.observe(grid(clock, power=-50))
    await engine.async_tick()
    events = rows(engine, "triggers")
    losses = [row for row in events if row["reason"] == "accepted_generator_power_lost"]
    assert [row["details"]["power_w"] for row in losses] == [50, -50]
    recoveries = [row for row in events if row["reason"] == "accepted_generator_power_lost_recovered"]
    assert len(recoveries) == 1
    assert recoveries[0]["details"]["power_w"] == -100
    saved = [row["telemetry"]["decoded"]["generator_power_w"] for row in rows(engine) if row.get("role") == "gridboss" and row.get("kind") == "acquisition"]
    assert initial in saved and -initial in saved and -50 in saved
    await engine.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", ["raw", "journal"])
@pytest.mark.parametrize("prefix_bytes", [0, 17])
@pytest.mark.parametrize("prior_rows", [False, True])
async def test_owned_empty_or_partial_first_append_resumes_after_restart(tmp_path, clock, monkeypatch, stream, prefix_bytes, prior_rows):
    """A durable pre-append claim separates crash bytes from an unrelated basename."""
    engine = recorder(tmp_path, clock)
    await engine.async_start()
    stream_root = engine.raw_root if stream == "raw" else engine.journal_root
    target = stream_root / "local-dongle.jsonl"
    original_open = Path.open
    damaged = b""

    class FirstAppendFault:
        """Fail an actual first append after zero or a bounded prefix of its bytes."""

        def __init__(self, destination):
            self.destination = destination

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.destination.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.destination, name)

        def write(self, payload):
            nonlocal damaged
            damaged = bytes(payload[:prefix_bytes])
            self.destination.write(damaged)
            self.destination.flush()
            raise OSError("synthetic crash during first append")

    def crashing_open(path, mode="r", *args, **kwargs):
        """Inject only at the real rolling stream's append descriptor."""
        destination = original_open(path, mode, *args, **kwargs)
        return FirstAppendFault(destination) if path == target and mode == "ab" else destination

    writer = LocalDongleTraceWriter(stream_root, max_files=None, write_guard=engine.write_guard(stream))
    if prior_rows:
        assert await writer.async_write(grid(clock)) is not None
        clock.advance(1)
    previous = target.read_bytes() if target.exists() else b""
    monkeypatch.setattr(Path, "open", crashing_open)
    assert await writer.async_write(grid(clock)) is None
    damaged = previous + damaged
    assert target.read_bytes() == damaged
    await engine.async_tick()
    # Reopen the same filesystem after stopping the old owners. Copying a file
    # deliberately changes its inode and cannot prove an empty file's ownership.
    copied = engine.root
    monkeypatch.undo()
    await writer.async_close()
    await engine.async_close()

    clock.advance(10)
    restarted = IncidentRecorder(copied, entry_id="synthetic-entry", session_id="session-two", build_id="b2", roles={"gridboss", "flexboss_master"}, limits=limits(), clock=clock, evidence_class="synthetic")
    await restarted.async_start()
    destination = restarted.raw_root if stream == "raw" else restarted.journal_root
    resumed = LocalDongleTraceWriter(destination, max_files=None, session_id="session-two", write_guard=restarted.write_guard(stream))
    assert await resumed.async_write(grid(clock, session_id="session-two")) is not None
    assert await resumed.async_sync() is True
    current = destination / "local-dongle.jsonl"
    saved = [json.loads(line) for line in current.read_text().splitlines()]
    assert len(saved) == 1 and saved[0]["session_id"] == "session-two"
    assert saved[0]["sample_started_at"] == ("2026-11-09T12:00:11+00:00" if prior_rows else "2026-11-09T12:00:10+00:00")
    preserved = [path for path in destination.glob("local-dongle-*.jsonl") if path.read_bytes() == damaged]
    assert preserved
    await resumed.async_close()
    await restarted.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", ["raw", "journal"])
async def test_intentional_owned_eviction_does_not_break_dirty_file_sync(tmp_path, clock, stream):
    """Native retention may remove flushed rotations without a false fsync failure."""
    engine = recorder(tmp_path, clock, raw_max_bytes=1600, journal_max_bytes=1600)
    await engine.async_start()
    destination = engine.raw_root if stream == "raw" else engine.journal_root
    writer = LocalDongleTraceWriter(destination, max_bytes=700, max_files=None, write_guard=engine.write_guard(stream))
    for index in range(6):
        assert await writer.async_write(grid(clock, sequence=index)) is not None
    assert len(list(destination.glob("*.jsonl"))) < 6
    assert await writer.async_sync() is True
    assert writer.health["WriteErrors"] == 0
    assert writer.health["State"] == "healthy"
    await writer.async_close()
    await engine.async_close()


@pytest.mark.asyncio
async def test_evicted_sole_primary_updates_retained_roles_and_precoverage(tmp_path, clock):
    """Rejected master rows cannot replace its evicted sole successful primary."""
    engine = recorder(tmp_path, clock, segment_bytes=1000, incident_max_age_seconds=10)
    await engine.async_start()
    engine.observe(flex(clock, payload="sole-success" * 500))
    clock.advance(1)
    engine.observe(grid(clock, output=282.9))
    await engine.async_tick()
    first = manifest(engine)
    root = Path(engine.health["LatestIncidentPath"])
    sole = [
        root / item["path"] for item in first["files"]
        if item["kind"] == "records" and item["retained"]
        and any(row.get("role") == "flexboss_master" and row.get("ok") is True for row in (
            json.loads(line) for line in (root / item["path"]).read_text().splitlines()
        ))
    ]
    assert len(sole) == 1 and sole[0].name != "local-dongle.jsonl"
    clock.advance(1)
    engine.observe(flex(clock, ok=False, output=None))
    engine.observe(grid(clock, output=282.9))
    await engine.async_tick()
    old = clock().timestamp() - 11
    os.utime(sole[0], (old, old))
    await engine.async_tick()
    document = manifest(engine)
    assert not sole[0].exists()
    assert document["observed_roles"] == ["gridboss"]
    assert document["missing_roles"] == ["flexboss_master"]
    assert document["coverage"]["actual_start"] == "2026-11-09T12:00:01+00:00"
    assert document["coverage"]["pre_start"] == "2026-11-09T12:00:01+00:00"
    assert document["coverage"]["by_role"]["flexboss_master"]["start"] == "2026-11-09T12:00:02+00:00"
    assert document["original_coverage"]["actual_start"] == "2026-11-09T12:00:00+00:00"
    assert "flexboss_master" in document["original_observed_roles"]
    assert "retention_eviction" in document["incomplete_reasons"]
    assert any(row.get("role") == "flexboss_master" and row.get("ok") is False for row in rows(engine))
    clock.advance(1)
    engine.observe(grid(clock, output=282.9))
    await engine.async_tick()
    rewritten = manifest(engine)
    assert rewritten["missing_roles"] == ["flexboss_master"]
    assert rewritten["coverage"]["actual_start"] == "2026-11-09T12:00:01+00:00"
    assert rewritten["original_coverage"]["actual_start"] == "2026-11-09T12:00:00+00:00"
    await engine.async_close()


@pytest.mark.asyncio
async def test_expired_closed_metadata_retires_without_touching_unrelated_content(tmp_path, clock):
    """Expired context-heavy tombstones cannot permanently block rolling appends."""
    engine = recorder(tmp_path, clock, pre_seconds=0, post_seconds=1, incident_max_age_seconds=10)
    engine.set_context_provider(lambda: {"roles": {"gridboss": {"settings": list(range(4000))}}, "role_freshness": {}})
    await engine.async_start()
    engine.observe(grid(clock))
    engine.observe(flex(clock, output=282.9))
    engine.observe(flex(clock))
    clock.advance(1)
    await engine.async_tick()
    root = Path(engine.health["LatestIncidentPath"])
    assert manifest(engine)["state"] == "closed"
    unrelated = root / "keep-original.txt"
    unrelated.write_text("unrelated retained investigation")
    unrelated_named = root / "records" / "local-dongle-123.jsonl"
    unrelated_named.write_text('{"not":"recorder output"}\n')
    external = tmp_path / "sealed.jsonl"
    external.write_text("sealed outside bundle")
    symlink = root / "records" / "local-dongle-124.jsonl"
    symlink.symlink_to(external)
    for item in manifest(engine)["files"]:
        if item["retained"]:
            path = root / item["path"]
            old = clock().timestamp() - 11
            os.utime(path, (old, old))
    await engine.async_tick()
    tombstone = json.loads((root / "manifest.json").read_text())
    assert tombstone["configuration_context"]["roles"]["gridboss"]["settings"][-1] == 3999
    assert "retention_eviction" in tombstone["incomplete_reasons"]
    assert not any(item["retained"] for item in tombstone["files"])
    assert (root / "manifest.json").stat().st_size > 8000

    engine.limits = replace(engine.limits, incident_max_bytes=8000)
    clock.advance(11)
    for stream, destination in (("raw", engine.raw_root), ("journal", engine.journal_root)):
        writer = LocalDongleTraceWriter(destination, max_files=None, write_guard=engine.write_guard(stream))
        assert await writer.async_write(grid(clock)) is not None
        assert await writer.async_sync() is True
        assert writer.health["State"] == "healthy"
        await writer.async_close()
    await engine.async_tick()
    assert not (root / "manifest.json").exists()
    assert engine.health["IncidentBytes"] == 0
    assert engine.health["State"] == "healthy"
    assert unrelated.read_text() == "unrelated retained investigation"
    assert unrelated_named.read_text() == '{"not":"recorder output"}\n'
    assert symlink.is_symlink() and external.read_text() == "sealed outside bundle"
    await engine.async_close()


@pytest.mark.asyncio
async def test_old_started_open_incident_is_not_retired_with_metadata(tmp_path, clock):
    """A persistent anomaly remains open even beyond the metadata retention age."""
    engine = recorder(tmp_path, clock, incident_max_age_seconds=10, segment_bytes=1000)
    await engine.async_start()
    engine.observe(flex(clock, output=282.9))
    await engine.async_tick()
    root = Path(engine.health["ActiveIncidentPath"])
    clock.advance(100)
    engine.observe(flex(clock, output=282.9))
    await engine.async_tick()
    assert engine.health["ActiveIncidentPath"] == str(root)
    assert manifest(engine)["state"] == "open"
    assert (root / "manifest.json").exists()
    await engine.async_close()


@pytest.mark.asyncio
async def test_metadata_expiry_waits_for_pending_finalization(tmp_path, clock, monkeypatch):
    """Even an aged closed_at cannot retire a bundle before its owned drain settles."""
    engine = recorder(tmp_path, clock, pre_seconds=0, post_seconds=1, incident_max_age_seconds=10)
    await engine.async_start()
    engine.observe(grid(clock))
    engine.observe(flex(clock, output=282.9))
    await engine.async_tick()
    root = Path(engine.health["ActiveIncidentPath"])
    engine.observe(flex(clock))
    clock.advance(1)
    entered, release = threading.Event(), threading.Event()
    original = os.fsync

    def blocking(descriptor):
        """Hold actual synchronization while receive time passes the expiry horizon."""
        entered.set()
        if not release.wait(10):
            raise OSError("test failed to release metadata finalization")
        return original(descriptor)

    monkeypatch.setattr(os, "fsync", blocking)
    finalizing = asyncio.create_task(engine.async_tick())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        clock.advance(11)
        release.set()
        await finalizing
    finally:
        release.set()
        monkeypatch.setattr(os, "fsync", original)
    document = json.loads((root / "manifest.json").read_text())
    assert document["state"] == "closed" and document["files"]
    assert all(item["finalized"] for item in document["files"] if item["retained"])
    await engine.async_tick()
    assert not (root / "manifest.json").exists()
    await engine.async_close()


@pytest.mark.asyncio
async def test_rotation_supports_nonreentrant_write_guards(tmp_path, clock):
    """One guard surrounds each actual operation without requiring recursive locks."""
    mutex = threading.Lock()

    @contextmanager
    def guard(path, size):
        """Reject nested acquisitions rather than hanging the writer on a deadlock."""
        if not mutex.acquire(blocking=False):
            raise OSError("write guard was entered recursively")
        try:
            yield
        finally:
            mutex.release()

    writer = LocalDongleTraceWriter(tmp_path / "guarded", max_bytes=700, max_files=None, write_guard=guard)
    for sequence in range(4):
        assert await writer.async_write(grid(clock, sequence=sequence, payload="x" * 600)) is not None
    assert await writer.async_sync() is True
    output = list((tmp_path / "guarded").glob("*.jsonl"))
    assert len(output) > 1
    assert sorted(json.loads(line)["sequence"] for path in output for line in path.read_text().splitlines()) == [0, 1, 2, 3]
    assert writer.health["WriteErrors"] == 0
    await writer.async_close()
