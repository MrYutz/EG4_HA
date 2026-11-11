"""Real TCP and persisted evidence through the local recorder lifecycle."""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import threading
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from custom_components.eg4_monitor.eg4_logger import EG4TelemetryLogger
from custom_components.eg4_monitor.local_dongle import EG4GridBossDongle, EG4LocalDongle
from custom_components.eg4_monitor.local_dongle_trace import (
    ControllerObservationJournal,
    LocalDongleTraceWriter,
)
from custom_components.eg4_monitor.local_incidents import (
    IncidentRecorder,
    RecorderLimits,
)
from custom_components.eg4_monitor.local_recorder import EG4LocalRecorder
from custom_components.eg4_monitor.register_catalog import REGISTER_MAP_REVISION
from tests.test_local_dongle import _fixture_response


@pytest.mark.asyncio
async def test_healthy_role_keeps_recording_while_gridboss_request_is_held(tmp_path):
    held = asyncio.Event()
    release = asyncio.Event()
    healthy_twice = asyncio.Event()
    handlers = set()
    observations = []

    async def serve(reader, stream, *, stalled):
        task = asyncio.current_task()
        handlers.add(task)
        try:
            serial = "INV7654321" if stalled else "INV1234567"
            stream.write(_fixture_response(
                dongle_serial="DJ12345678", inverter_serial=serial,
                register=127, values=[0],
            ))
            await stream.drain()
            request = await reader.readexactly(38)
            if stalled:
                held.set()
                await release.wait()
                return
            register = int.from_bytes(request[32:34], "little")
            count = int.from_bytes(request[34:36], "little")
            words = [0] * count
            if register == 0 and request[21] == 4:
                words[4], words[10] = 520, 500
            stream.write(_fixture_response(
                dongle_serial="DJ12345678", inverter_serial=serial,
                register=register, values=words, function=request[21],
            ))
            await stream.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            stream.close()
            with contextlib.suppress(ConnectionError):
                await stream.wait_closed()
            handlers.discard(task)

    async def healthy(reader, stream):
        await serve(reader, stream, stalled=False)

    async def stalled(reader, stream):
        await serve(reader, stream, stalled=True)

    async def observed(record):
        if record is not None and record["role"] == "flexboss_master" and record["ok"]:
            observations.append(record)
            if len(observations) == 2:
                healthy_twice.set()

    good_server = await asyncio.start_server(healthy, "127.0.0.1", 0)
    slow_server = await asyncio.start_server(stalled, "127.0.0.1", 0)
    writer = LocalDongleTraceWriter(tmp_path)
    recorder = EG4LocalRecorder(
        devices={
            "flexboss_master": EG4LocalDongle(
                host="127.0.0.1", port=good_server.sockets[0].getsockname()[1], timeout=2,
            ),
            "gridboss": EG4GridBossDongle(
                host="127.0.0.1", port=slow_server.sockets[0].getsockname()[1], timeout=2,
            ),
        },
        configured_inverter_roles={"flexboss_master"}, writer=writer,
        on_update=observed, interval=0.01, bms_interval=0.05, build_id="offline-loopback",
    )
    try:
        await recorder.async_start()
        await asyncio.wait_for(held.wait(), 1)
        await asyncio.wait_for(healthy_twice.wait(), 1)
        assert release.is_set() is False
        first, second = observations[:2]
        assert first["acquisition_id"] != second["acquisition_id"]
        assert recorder.snapshot().sample.charge_power_w == 500.0
    finally:
        await recorder.async_close()
        release.set()
        await writer.async_close()
        for server in (good_server, slow_server):
            server.close()
            await server.wait_closed()
        await asyncio.gather(*handlers, return_exceptions=True)

    rows = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
    saved_ids = {
        row["acquisition_id"] for row in rows
        if row.get("kind") == "acquisition" and row.get("role") == "flexboss_master"
    }
    assert {first["acquisition_id"], second["acquisition_id"]} <= saved_ids


@contextlib.asynccontextmanager
async def _endpoint(respond, *, gridboss=False):
    """Serve real identity/read frames; None deliberately drops a response."""
    handlers = set()
    requests = []
    concurrency = {"active": 0, "maximum": 0}
    serial = "INV7654321" if gridboss else "INV1234567"

    async def handle(reader, stream):
        task = asyncio.current_task()
        handlers.add(task)
        active = False
        try:
            stream.write(_fixture_response(
                dongle_serial="DJ12345678", inverter_serial=serial,
                register=127, values=[0],
            ))
            await stream.drain()
            packet = await reader.readexactly(38)
            shape = (
                packet[21], int.from_bytes(packet[32:34], "little"),
                int.from_bytes(packet[34:36], "little"),
            )
            requests.append(shape)
            concurrency["active"] += 1
            active = True
            concurrency["maximum"] = max(concurrency["maximum"], concurrency["active"])
            values = await respond(shape)
            if values is not None:
                stream.write(_fixture_response(
                    dongle_serial="DJ12345678", inverter_serial=serial,
                    register=shape[1], values=values, function=shape[0],
                ))
                await stream.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if active:
                concurrency["active"] -= 1
            stream.close()
            with contextlib.suppress(ConnectionError):
                await stream.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    client_type = EG4GridBossDongle if gridboss else EG4LocalDongle
    device = client_type(
        host="127.0.0.1", port=server.sockets[0].getsockname()[1], timeout=2,
    )
    try:
        yield device, requests, concurrency
    finally:
        server.close()
        await server.wait_closed()
        for task in tuple(handlers):
            task.cancel()
        await asyncio.gather(*tuple(handlers), return_exceptions=True)


def _words(shape, *, charge=500, gridboss=False):
    values = [0] * shape[2]
    if shape == (4, 0, 40):
        if gridboss:
            # Reserved word0; grid1, UPS2, generator3; generator watts30/31.
            values[2], values[3] = 2400, 2400
            values[30], values[31] = 250, 250
        else:
            values[4], values[5], values[10] = 520, (100 << 8) | 70, charge
    elif shape == (4, 120, 12):
        values[8], values[10] = 6000, 6000  # Phase128 and qualified GEN130.
    elif shape == (4, 80, 40):
        values[16], values[17], values[18] = 5, 1400, 2500
        values[21], values[22] = 3297, 3278
        values[23], values[24], values[26] = 310, 290, 18
    return values


class _LoggerConsumer:
    """Production logger consumes the recorder sample at each public update."""

    def __init__(self, *, paced=True):
        self.recorder = None
        self.logger = EG4TelemetryLogger("loopback")
        self.updates = asyncio.Queue()
        self.permits = asyncio.Queue() if paced else None

    async def __call__(self, record):
        snapshot = self.recorder.snapshot()
        self.logger.process(snapshot.sample)
        self.updates.put_nowait((record, snapshot, self.logger.snapshot()))
        if self.permits is not None:
            await self.permits.get()

    async def next(self):
        return await asyncio.wait_for(self.updates.get(), 2)

    def advance(self):
        self.permits.put_nowait(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("gridboss", [False, True])
async def test_startup_profiles_are_literal_safe_and_one_maintenance_per_round(gridboss):
    schedule_time = [0.0]
    async def respond(shape):
        return _words(shape, gridboss=gridboss)

    async with _endpoint(respond, gridboss=gridboss) as (device, requests, concurrency):
        role = "gridboss" if gridboss else "flexboss_master"
        consumer = _LoggerConsumer()
        recorder = consumer.recorder = EG4LocalRecorder(
            devices={role: device},
            configured_inverter_roles=set() if gridboss else {role},
            writer=None, on_update=consumer, interval=0.005, clock=lambda: schedule_time[0],
        )
        primary = [(4, 0, 40), (4, 120, 12), (4, 500, 100)] if gridboss else [
            (4, 0, 40), (4, 40, 40), (4, 120, 26),
        ]
        maintenance = [
            (3, 4500, 10), (3, 0, 20), (3, 20, 90), (3, 110, 125),
            (3, 235, 88), (3, 2000, 108), (4, 40, 80), (3, 4097, 41),
        ] if gridboss else [
            (4, 80, 40), (3, 110, 125), (3, 0, 20), (3, 20, 90), (3, 235, 102),
            (4, 146, 94), (3, 500, 112), (3, 612, 112), (3, 5000, 18),
        ]
        try:
            await recorder.async_start()
            await recorder.async_start()
            for index, shape in enumerate(maintenance):
                record, snapshot, _ = await consumer.next()
                assert record["ok"] is True
                assert record["maintenance_attempted"] == ":".join(map(str, shape))
                assert len(record["transaction_ids"]) == len(primary) + 1
                assert record["primary_source"]["acquired_at"] == min(
                    block["completed_at"] for block in record["primary_blocks"]
                )
                if index < len(maintenance) - 1:
                    consumer.advance()
            assert requests == [shape for item in maintenance for shape in [*primary, item]]
            assert concurrency["maximum"] == 1
            assert snapshot.health["Roles"][role]["MissingProfileBlocks"] == []
            assert snapshot.health["Roles"][role]["State"] == "healthy"
            assert snapshot.sample.process_generator is gridboss
            assert snapshot.sample.process_charge is not gridboss
            assert all(fn in {3, 4} for fn, _, _ in requests)
            assert not {224, 248, 4516, 1100} & {start for _, start, _ in requests}
            initial_settings = snapshot.settings[role]
            schedule_time[0] = 300
            for shape in maintenance:
                consumer.advance()
                refreshed, snapshot, _ = await consumer.next()
                assert refreshed["maintenance_attempted"] == ":".join(map(str, shape))
                assert len(refreshed["transaction_ids"]) == len(primary) + 1
            assert requests == 2 * [shape for item in maintenance for shape in [*primary, item]]
            for key, context in snapshot.settings[role].items():
                assert context["target_interval_seconds"] == (
                    5 if key == "3:4500:10" else 60 if key == "3:110:125" and not gridboss else 300
                )
                assert context["last_success"]["acquisition_id"] != initial_settings[key]["last_success"]["acquisition_id"]
        finally:
            await recorder.async_close()
            await recorder.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("gridboss", [False, True])
async def test_slow_input_inventory_retains_source_through_failure_without_blocking_peer(
    tmp_path, gridboss,
):
    """Real slow-bank failures retain raw history, not BMS/configuration values."""
    role = "gridboss" if gridboss else "flexboss_master"
    peer = "flexboss_master" if gridboss else "gridboss"
    shape = (4, 40, 80) if gridboss else (4, 146, 94)
    key = ":".join(map(str, shape))
    schedule_time = [0.0]
    attempts = Counter()
    held, peer_progress = asyncio.Event(), asyncio.Event()
    updates, permits = asyncio.Queue(), asyncio.Queue()
    peer_records = []

    async def respond(request):
        attempts[request] += 1
        if request == shape and attempts[request] == 2:
            held.set()
            await asyncio.wait_for(peer_progress.wait(), 2)
            return None
        words = _words(request, gridboss=gridboss)
        if request == shape:
            words[-1] = 321
        return words

    async def healthy(request):
        return _words(request, gridboss=not gridboss)

    async def consume(record):
        assert record is not None
        if record["role"] == role:
            updates.put_nowait((record, recorder.snapshot()))
            await permits.get()
        else:
            peer_records[:] = [record]
            if held.is_set():
                peer_progress.set()

    async def next_record():
        return await asyncio.wait_for(updates.get(), 2)

    writer = LocalDongleTraceWriter(tmp_path)
    async with _endpoint(respond, gridboss=gridboss) as (device, requests, concurrency):
        async with _endpoint(healthy, gridboss=not gridboss) as (other, _, peer_concurrency):
            recorder = EG4LocalRecorder(
                devices={role: device, peer: other},
                configured_inverter_roles={"flexboss_master"},
                writer=writer, on_update=consume, interval=0.005,
                bms_interval=7, clock=lambda: schedule_time[0],
                build_id="slow-inventory-source",
            )
            try:
                await recorder.async_start()
                while True:
                    initial, snapshot = await next_record()
                    if not snapshot.health["Roles"][role]["MissingProfileBlocks"]:
                        break
                    permits.put_nowait(None)
                baseline = initial["inventory_context"][key]["last_success"]
                assert baseline["values"][-1] == 321
                assert baseline["build_id"] == "slow-inventory-source"
                assert baseline["register_map_revision"] == REGISTER_MAP_REVISION
                assert shape in requests
                assert key not in initial["configuration_context"]
                assert initial["inventory_context"][key]["target_interval_seconds"] == 300
                assert initial["inventory_context"][key]["failures"] == 0
                if gridboss:
                    assert initial["bms_context"] == {}
                    assert initial["bms_sampled"] is False
                else:
                    assert initial["bms_context"]["register"] == 80
                    assert initial["bms_context"]["last_success"]["count"] == 40
                schedule_time[0] = 300
                while True:
                    permits.put_nowait(None)
                    failed, snapshot = await next_record()
                    if failed.get("maintenance_attempted") == key:
                        break
                context = failed["inventory_context"][key]
                assert held.is_set() and peer_progress.is_set()
                assert peer_records[-1]["role"] == peer and peer_records[-1]["ok"] is True
                assert failed["ok"] is True and failed["partial"] is True
                assert snapshot.health["Roles"][role]["PrimaryAvailable"] is True
                assert failed["primary_source"]["acquisition_id"] == failed["acquisition_id"]
                assert snapshot.sample.charge_power_w == 500
                assert snapshot.sample.generator_voltage == 240
                assert context["status"] == "failed" and context["failures"] == 1
                assert context["latest_attempt"]["ok"] is False
                assert context["latest_attempt"]["values"] is None
                assert context["last_success"]["transaction_id"] == baseline["transaction_id"]
                assert context["last_success"]["acquired_at"] == baseline["acquired_at"]
                assert context["last_success"]["values"][-1] == 321
                assert context["last_success"]["age_seconds"] > baseline["age_seconds"]
                diagnostic = next(
                    block for block in failed["diagnostics"]
                    if (block["function"], block["register"], block["count"]) == shape
                )
                assert diagnostic["current_acquisition"] is True
                assert diagnostic["transaction_id"] == context["latest_attempt"]["transaction_id"]
                assert diagnostic["values"] is None
                assert key in snapshot.health["Roles"][role]["FailedProfileBlocks"]
                assert concurrency["maximum"] == peer_concurrency["maximum"] == 1
                permits.put_nowait(None)
                reused, _ = await next_record()
                retained = reused["inventory_context"][key]
                assert retained["last_success"]["acquired_at"] == baseline["acquired_at"]
                assert retained["last_success"]["age_seconds"] >= context["last_success"]["age_seconds"]
                assert retained["failures"] == 1 and attempts[shape] == 2
            finally:
                await recorder.async_close()
                await writer.async_close()
    rows = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
    assert all(row["build_id"] == "slow-inventory-source" for row in rows)
    assert all(row["register_map_revision"] == REGISTER_MAP_REVISION for row in rows)
    transactions = [row for row in rows if row["kind"] == "transaction"]
    assert transactions and all("capture_profile" not in row for row in transactions)
    startup = next(row for row in rows if row["kind"] == "recorder_health" and row["reason"] == "startup")
    assert startup["health"]["ConfiguredRoles"] == ["flexboss_master", "gridboss"]
    profile = startup["health"]["Roles"][role]["capture_profile"]
    assert profile["revision"] == REGISTER_MAP_REVISION and profile["enabled"] is True
    assert [*shape, 300.0] in profile["maintenance"]
    assert [*shape, 300.0] in profile["effective_periods"]
    assert [4, 0, 40, 0.005] in profile["effective_periods"]
    inverter_profile = startup["health"]["Roles"]["flexboss_master"]["capture_profile"]
    assert [4, 80, 40, 7.0] in inverter_profile["effective_periods"]


@pytest.mark.asyncio
async def test_failed_configuration_keeps_aged_last_good_and_unique_deadline():
    schedule_time = [0.0]
    attempts = Counter()

    async def respond(shape):
        attempts[shape] += 1
        if shape == (3, 110, 125):
            if attempts[shape] == 2:
                return None
            values = _words(shape)
            values[0] = 7 if attempts[shape] == 1 else 9
            return values
        return _words(shape)

    async with _endpoint(respond) as (device, requests, _):
        consumer = _LoggerConsumer()
        recorder = consumer.recorder = EG4LocalRecorder(
            devices={"flexboss_master": device}, configured_inverter_roles={"flexboss_master"},
            writer=None, on_update=consumer, interval=0.005, clock=lambda: schedule_time[0],
        )
        try:
            await recorder.async_start()
            while True:
                _, initial, _ = await consumer.next()
                if not initial.health["Roles"]["flexboss_master"]["MissingProfileBlocks"]:
                    break
                consumer.advance()
            baseline = initial.settings["flexboss_master"]["3:110:125"]["last_success"]
            schedule_time[0] = 60
            consumer.advance()
            bms_record, _, _ = await consumer.next()
            assert bms_record["maintenance_attempted"] == "4:80:40"
            consumer.advance()
            failed_record, failed, _ = await consumer.next()
            context = failed.settings["flexboss_master"]["3:110:125"]
            assert context["status"] == "failed"
            assert context["latest_attempt"]["ok"] is False
            assert context["latest_attempt"]["values"] is None
            assert context["last_success"]["acquisition_id"] == baseline["acquisition_id"]
            assert context["last_success"]["values"][0] == 7
            aged = recorder.snapshot(datetime.fromisoformat(baseline["acquired_at"]) + timedelta(seconds=120))
            assert aged.settings["flexboss_master"]["3:110:125"]["last_success"]["age_seconds"] == 120
            assert failed_record["configuration_context"]["3:110:125"]["status"] == "failed"
            assert failed_record["partial"] is True
            assert "3:110:125" in failed.health["Roles"]["flexboss_master"]["FailedProfileBlocks"]
            consumer.advance()
            reused, _, _ = await consumer.next()
            assert "maintenance_attempted" not in reused
            assert reused["configuration_context"]["3:110:125"]["last_success"]["transaction_id"] == baseline["transaction_id"]
            assert attempts[(3, 110, 125)] == 2
            schedule_time[0] = 120
            consumer.advance()
            await consumer.next()  # Oldest due BMS, then holding110/125.
            consumer.advance()
            recovered, snapshot, _ = await consumer.next()
            context = recovered["configuration_context"]["3:110:125"]
            assert context["status"] == "ok"
            assert context["last_success"]["observed_changed"] is True
            assert context["last_success"]["values"][0] == 9
            assert context["effective_change_time_known"] is False
            assert context["last_success"]["acquisition_id"] != baseline["acquisition_id"]
            assert attempts[(3, 110, 125)] == 3
            assert snapshot.health["Roles"]["flexboss_master"]["FailedProfileBlocks"] == []
            # Scheduling-clock jumps do not masquerade as measured physical cadence.
            assert snapshot.health["Roles"]["flexboss_master"]["MaxSpacingSeconds"] < 2
            assert requests.count((3, 110, 125)) == 3
        finally:
            await recorder.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stalled_gridboss", [False, True])
async def test_maintenance_stall_does_not_block_other_role_or_retime_primary(stalled_gridboss):
    held, release = asyncio.Event(), asyncio.Event()
    stall_shape = (3, 4500, 10) if stalled_gridboss else (4, 80, 40)

    async def slow(shape):
        if shape == stall_shape:
            held.set()
            await release.wait()
        return _words(shape, gridboss=stalled_gridboss)

    async def healthy(shape):
        return _words(shape, gridboss=not stalled_gridboss)

    async with _endpoint(slow, gridboss=stalled_gridboss) as (slow_device, _, slow_concurrency):
        async with _endpoint(healthy, gridboss=not stalled_gridboss) as (good_device, _, good_concurrency):
            consumer = _LoggerConsumer(paced=False)
            devices = {
                "gridboss": slow_device if stalled_gridboss else good_device,
                "flexboss_master": good_device if stalled_gridboss else slow_device,
            }
            recorder = consumer.recorder = EG4LocalRecorder(
                devices=devices, configured_inverter_roles={"flexboss_master"},
                writer=None, on_update=consumer, interval=0.005,
            )
            try:
                await recorder.async_start()
                await asyncio.wait_for(held.wait(), 1)
                first, before, _ = await consumer.next()
                second, after, _ = await consumer.next()
                expected_role = "flexboss_master" if stalled_gridboss else "gridboss"
                assert first["role"] == second["role"] == expected_role
                assert first["acquisition_id"] != second["acquisition_id"]
                slow_source_before = before.gridboss_freshness["Source"] if stalled_gridboss else before.local_freshness["Sources"]["flexboss_master"]
                slow_source_after = after.gridboss_freshness["Source"] if stalled_gridboss else after.local_freshness["Sources"]["flexboss_master"]
                assert slow_source_before == slow_source_after
                assert release.is_set() is False
                assert slow_concurrency["maximum"] == good_concurrency["maximum"] == 1
            finally:
                await recorder.async_close()
                release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("persist", [False, True])
async def test_young_cache_cannot_hide_semantic_failure_or_bridge_charge_energy(tmp_path, persist):
    core_reads = 0

    async def respond(shape):
        nonlocal core_reads
        values = _words(shape, charge=3600)
        if shape == (4, 0, 40):
            core_reads += 1
            if core_reads == 4:
                values[0], values[6] = 0x8200, 3
        return values

    writer = LocalDongleTraceWriter(tmp_path) if persist else None
    records = []
    async with _endpoint(respond) as (device, requests, _):
        consumer = _LoggerConsumer()
        recorder = consumer.recorder = EG4LocalRecorder(
            devices={"flexboss_master": device}, configured_inverter_roles={"flexboss_master"},
            writer=writer, on_update=consumer, interval=0.02,
        )
        try:
            await recorder.async_start()
            first, snapshot, state = await consumer.next()
            records.append(first)
            assert state["Charging"]["ActiveSession"] is None
            for _ in range(5):
                consumer.logger.process(recorder.snapshot().sample)
            assert consumer.logger.snapshot()["Charging"]["ActiveSession"] is None
            consumer.advance()
            second, _, state = await consumer.next()
            records.append(second)
            session_id = state["Charging"]["ActiveSession"]["session_id"]
            consumer.advance()
            third, previous, state = await consumer.next()
            records.append(third)
            energy = state["Charging"]["ActiveSession"]["energy_estimated_kwh"]
            assert energy > 0
            original = previous.sample.charge_role_context["flexboss_master"]
            consumer.advance()
            interruption, failed, state = await consumer.next()
            assert interruption is None
            assert requests[-1] == (4, 0, 40)  # No later diagnostic/maintenance work yet.
            assert failed.local_freshness["SampleAgesSeconds"]["flexboss_master"] < 1
            assert failed.local_freshness["PrimaryAvailable"]["flexboss_master"] is False
            assert failed.inverters == {}
            context = failed.sample.charge_role_context["flexboss_master"]
            assert context["acquisition_id"] == original["acquisition_id"]
            assert context["acquired_at"] == original["acquired_at"]
            assert context["charge_power_w"] == 3600
            assert context["available"] is False
            assert failed.sample.charge_power_w is None
            assert state["Charging"]["ActiveSession"]["session_id"] == session_id
            assert state["Charging"]["ActiveSession"]["interrupted"] is True
            assert state["Charging"]["ActiveSession"]["energy_estimated_kwh"] == energy
            consumer.advance()
            rejected, _, state = await consumer.next()
            records.append(rejected)
            assert rejected["ok"] is False
            assert "telemetry" not in rejected
            primary, = rejected["primary_blocks"]
            assert primary["values"][6] == 3
            assert primary["quality"] == {"structural": "accepted", "semantic": "rejected"}
            consumer.advance()
            resumed, restored, state = await consumer.next()
            records.append(resumed)
            active = state["Charging"]["ActiveSession"]
            assert active["session_id"] == session_id
            assert active["interrupted"] is True
            assert active["energy_estimated_kwh"] == energy
            assert restored.sample.charge_role_context["flexboss_master"]["acquisition_id"] != original["acquisition_id"]
            consumer.advance()
            _, _, state = await consumer.next()
            assert state["Charging"]["ActiveSession"]["energy_estimated_kwh"] > energy
        finally:
            await recorder.async_close()
            if writer:
                await writer.async_close()

    if writer:
        rows = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
        saved = {row["acquisition_id"] for row in rows if row["kind"] == "acquisition"}
        assert {record["acquisition_id"] for record in records} <= saved
        raw = next(row for row in rows if row.get("transaction_id") == primary["transaction_id"]
                   and row["kind"] == "transaction")
        interpreted = next(row for row in rows if row.get("transaction_id") == primary["transaction_id"]
                           and row["kind"] == "interpretation")
        assert rows.index(raw) < rows.index(interpreted)
        assert raw["completed_at"] == primary["completed_at"]
        assert raw["frames"][-1]["values"][6] == 3
        assert raw["session_id"] == recorder.session_id


@pytest.mark.asyncio
async def test_failed_primary_clears_charge_stop_dwell_before_zero_recovery(monkeypatch):
    core_reads = 0
    monkeypatch.setattr(
        "custom_components.eg4_monitor.eg4_logger.CHARGE_STOP_MIN_DWELL_SECONDS", 0.05,
    )

    async def respond(shape):
        nonlocal core_reads
        if shape == (4, 0, 40):
            core_reads += 1
            if core_reads == 4:
                return None
        return _words(shape, charge=500 if core_reads < 3 else 0)

    async with _endpoint(respond) as (device, _, _):
        consumer = _LoggerConsumer()
        recorder = consumer.recorder = EG4LocalRecorder(
            devices={"flexboss_master": device}, configured_inverter_roles={"flexboss_master"},
            writer=None, on_update=consumer, interval=0.06,
        )
        try:
            await recorder.async_start()
            for index in range(3):
                _, _, state = await consumer.next()
                if index < 2:
                    consumer.advance()
            assert state["Charging"]["ActiveSession"] is not None
            consumer.advance()
            interruption, _, state = await consumer.next()
            assert interruption is None
            consumer.advance()
            await consumer.next()  # Final failed round.
            consumer.advance()
            _, _, recovered = await consumer.next()
            assert recovered["Charging"]["LastSession"] is None
            assert recovered["Charging"]["ActiveSession"]["interrupted"] is True
            consumer.advance()
            _, _, stopped = await consumer.next()
            assert stopped["Charging"]["ActiveSession"] is None
            assert stopped["Charging"]["LastSession"] is not None
        finally:
            await recorder.async_close()


@pytest.mark.asyncio
async def test_gridboss_partial_failure_retains_raw_power_and_transfer_uncertainty():
    primary_reads = 0

    async def respond(shape):
        nonlocal primary_reads
        if shape == (4, 0, 40):
            primary_reads += 1
        if shape == (4, 120, 12) and primary_reads == 2:
            return None
        values = _words(shape, gridboss=True)
        if primary_reads >= 3 and shape == (4, 0, 40):
            values[1], values[3], values[30], values[31] = 2400, 0, 0, 0
        if primary_reads >= 3 and shape == (4, 120, 12):
            values[9], values[10] = 6000, 0
        return values

    async with _endpoint(respond, gridboss=True) as (device, _, _):
        consumer = _LoggerConsumer()
        recorder = consumer.recorder = EG4LocalRecorder(
            devices={"gridboss": device}, configured_inverter_roles=set(),
            writer=None, on_update=consumer, interval=0.005,
        )
        try:
            await recorder.async_start()
            _, before, state = await consumer.next()
            assert state["Generator"]["TransferObservation"] == "generator_to_ups_candidate"
            original = before.sample.generator_acquisition_id
            consumer.advance()
            interruption, failed, state = await consumer.next()
            assert interruption is None
            assert failed.gridboss is None
            assert failed.sample.generator_acquisition_id == original
            assert failed.sample.generator_acquired_at == before.sample.generator_acquired_at
            assert failed.sample.process_charge is False
            consumer.advance()
            rejected, _, _ = await consumer.next()
            assert rejected["partial"] is True
            power, frequency = rejected["primary_blocks"]
            assert power["register"] == 0 and power["ok"] is True
            assert power["values"][3] == 2400
            assert frequency["register"] == 120 and frequency["ok"] is False
            consumer.advance()
            _, resumed, state = await consumer.next()
            assert resumed.sample.generator_acquisition_id != original
            assert state["Generator"]["TransferObservation"] == "grid_to_ups_candidate"
            transfers = [
                event for event in state["RecentEvents"]
                if event["event_type"] == "eg4_monitor_generator_transfer_observation_changed"
            ]
            assert transfers[-1]["from"] == "generator_to_ups_candidate"
            assert transfers[-1]["to"] == "grid_to_ups_candidate"
            assert transfers[-1]["intervening_unknown"] is True
        finally:
            await recorder.async_close()


@pytest.mark.asyncio
async def test_bms_failure_and_age_cannot_authorize_cached_corroboration():
    schedule_time = [0.0]
    bms_reads = 0

    async def respond(shape):
        nonlocal bms_reads
        if shape == (4, 80, 40):
            bms_reads += 1
            if bms_reads == 2:
                return None
        return _words(shape)

    async with _endpoint(respond) as (device, _, _):
        consumer = _LoggerConsumer()
        recorder = consumer.recorder = EG4LocalRecorder(
            devices={"flexboss_master": device}, configured_inverter_roles={"flexboss_master"},
            writer=None, on_update=consumer, interval=0.005, clock=lambda: schedule_time[0],
        )
        try:
            await recorder.async_start()
            first, before, _ = await consumer.next()
            assert before.inverters["flexboss_master"].bms_available is True
            original = first["bms_source"]
            aged = recorder.snapshot(
                datetime.fromisoformat(original["acquired_at"]) + timedelta(seconds=16),
            )
            assert aged.local_freshness["BMSSources"]["flexboss_master"]["available"] is False
            while before.health["Roles"]["flexboss_master"]["MissingProfileBlocks"]:
                consumer.advance()
                _, before, _ = await consumer.next()
            schedule_time[0] = 5
            consumer.advance()
            failed, snapshot, _ = await consumer.next()
            assert failed["maintenance_attempted"] == "4:80:40"
            assert failed["bms_context"]["status"] == "failed"
            assert failed["bms_context"]["available"] is False
            assert failed["bms_source"]["acquisition_id"] == original["acquisition_id"]
            assert failed["bms_source"]["used_in_telemetry"] is False
            assert snapshot.inverters["flexboss_master"].bms_available is False
            assert snapshot.sample.charge_source_available is True
            assert snapshot.local_freshness["BMSSources"]["flexboss_master"]["source"]["acquisition_id"] == original["acquisition_id"]
        finally:
            await recorder.async_close()


@pytest.mark.asyncio
async def test_missing_configured_master_and_zero_slave_never_become_complete_zero():
    async def respond(shape):
        return _words(shape, charge=0)

    async with _endpoint(respond) as (device, _, _):
        consumer = _LoggerConsumer()
        recorder = consumer.recorder = EG4LocalRecorder(
            devices={"flexboss_slave": device},
            configured_inverter_roles={"flexboss_master", "flexboss_slave"},
            writer=None, on_update=consumer, interval=0.005,
        )
        try:
            await recorder.async_start()
            _, snapshot, state = await consumer.next()
            assert snapshot.sample.charge_power_w is None
            assert snapshot.sample.charge_source_available is False
            assert snapshot.sample.charge_role_context["flexboss_slave"]["charge_power_w"] == 0
            assert snapshot.sample.charge_role_context["flexboss_master"]["available"] is False
            assert snapshot.local_freshness["MissingRoles"] == ["flexboss_master"]
            assert state["Charging"]["Quality"] == "interrupted"
            assert snapshot.health["Roles"]["flexboss_master"]["State"] == "degraded"
            assert snapshot.health["ConfiguredRoles"] == ["flexboss_master", "flexboss_slave"]
            assert snapshot.health["ActiveRoles"] == ["flexboss_slave"]
            missing_profile = snapshot.health["Roles"]["flexboss_master"]["capture_profile"]
            assert missing_profile["enabled"] is False
            assert missing_profile["effective_periods"] == []
            assert missing_profile["family"] == "inverter"
        finally:
            await recorder.async_close()


@pytest.mark.asyncio
async def test_zero_qualified_frequency_keeps_generator_presence_unknown_not_off():
    async def respond(shape):
        values = _words(shape, gridboss=True)
        if shape == (4, 0, 40):
            values[3], values[30], values[31] = 2390, 0, 0
        elif shape == (4, 120, 12):
            values[10] = 0
        return values

    async with _endpoint(respond, gridboss=True) as (device, _, _):
        consumer = _LoggerConsumer()
        recorder = consumer.recorder = EG4LocalRecorder(
            devices={"gridboss": device}, configured_inverter_roles=set(),
            writer=None, on_update=consumer, interval=0.005,
        )
        try:
            await recorder.async_start()
            await consumer.next()
            consumer.advance()
            _, _, state = await consumer.next()
            generator = state["Generator"]
            assert generator["PresenceState"] == "present"
            assert generator["SupplyState"] == "not_supplying"
            assert generator["RunState"] == "unknown"
            assert generator["Quality"] == "interrupted"
            assert generator["Reason"] != "generator_voltage_absent"
        finally:
            await recorder.async_close()


@pytest.mark.asyncio
async def test_callback_error_is_visible_while_capture_continues_to_filesystem(tmp_path):
    async def respond(shape):
        return _words(shape)

    second = asyncio.Event()
    third = asyncio.Event()
    release = asyncio.Event()
    failures = 0
    writer = LocalDongleTraceWriter(tmp_path)
    async with _endpoint(respond) as (device, _, _):
        async def broken_consumer(record):
            nonlocal failures
            if record is not None:
                failures += 1
                if failures == 2:
                    second.set()
                    await release.wait()
                if failures == 3:
                    third.set()
                    await release.wait()
                raise RuntimeError("consumer offline")

        recorder = EG4LocalRecorder(
            devices={"flexboss_master": device}, configured_inverter_roles={"flexboss_master"},
            writer=writer, on_update=broken_consumer, interval=0.005,
        )
        try:
            await recorder.async_start()
            await asyncio.wait_for(second.wait(), 1)
            health = recorder.snapshot().health
            assert health["State"] == "degraded"
            assert health["Roles"]["flexboss_master"]["CallbackError"] == "RuntimeError: consumer offline"
            release.set()
            await asyncio.wait_for(third.wait(), 1)
        finally:
            await recorder.async_close()
            await writer.async_close()
    rows = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
    acquisitions = [row for row in rows if row["kind"] == "acquisition" and row["ok"]]
    assert len(acquisitions) >= 3
    assert len({row["acquisition_id"] for row in acquisitions}) == len(acquisitions)


@pytest.mark.asyncio
async def test_transfer_changes_reach_analyzer_without_hiding_intervening_gap(tmp_path):
    from tests.test_analyze_local_dongle_trace import ANALYZER

    phase = "generator"

    async def respond(shape):
        if phase == "failed" and shape == (4, 120, 12):
            return None
        values = _words(shape, gridboss=True)
        if phase != "generator":
            if shape == (4, 0, 40):
                values[1], values[30], values[31] = 2400, 0, 0
            elif shape == (4, 120, 12):
                values[9] = 6000
        return values

    writer = LocalDongleTraceWriter(tmp_path)
    async with _endpoint(respond, gridboss=True) as (device, _, _):
        consumer = _LoggerConsumer()
        recorder = consumer.recorder = EG4LocalRecorder(
            devices={"gridboss": device}, configured_inverter_roles=set(),
            writer=writer, on_update=consumer, interval=0.005,
        )
        try:
            await recorder.async_start()
            baseline, _, _ = await consumer.next()
            assert baseline.get("transfer_transition") is None
            phase = "grid"
            consumer.advance()
            changed, _, _ = await consumer.next()
            assert changed.get("transfer_transition") == {
                "from": "generator_to_ups_candidate",
                "to": "grid_to_ups_candidate",
                "intervening_unknown": False,
            }
            phase = "failed"
            consumer.advance()
            interrupted, _, _ = await consumer.next()
            assert interrupted is None
            consumer.advance()
            failed, _, _ = await consumer.next()
            assert failed["ok"] is False
            phase = "generator"
            consumer.advance()
            resumed, _, _ = await consumer.next()
            assert resumed["transfer_transition"] == {
                "from": "grid_to_ups_candidate",
                "to": "generator_to_ups_candidate",
                "intervening_unknown": True,
            }
        finally:
            await recorder.async_close()
            await writer.async_close()
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(ANALYZER), str(writer.active_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    assert "transitions=2" in stdout.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_stream", ["raw", "journal"])
async def test_real_storage_failure_recovers_health_without_hiding_healthy_capture(
    tmp_path, failed_stream, caplog,
):
    """A real unusable directory loses writes, not device observations."""
    raw_root, journal_root = tmp_path / "raw", tmp_path / "journal"
    blocked = raw_root if failed_stream == "raw" else journal_root
    blocked.write_text("not a directory")
    raw = LocalDongleTraceWriter(raw_root)
    journal_writer = LocalDongleTraceWriter(journal_root, session_id=raw.session_id)
    journal = ControllerObservationJournal(journal_writer)
    # Isolate the raw/journal fault from the independent incident storage owner.
    incidents = IncidentRecorder(
        tmp_path / "incident-store",
        entry_id="storage-recovery",
        session_id=raw.session_id,
        build_id="temporary-storage-recovery",
        roles={"flexboss_master"},
        limits=RecorderLimits(min_free_bytes=0),
    )
    notifications = []

    async def respond(shape):
        return _words(shape)

    async with _endpoint(respond) as (device, _, concurrency):
        consumer = _LoggerConsumer()

        async def health_update():
            notifications.append(recorder.snapshot().health)

        recorder = consumer.recorder = EG4LocalRecorder(
            devices={"flexboss_master": device}, configured_inverter_roles={"flexboss_master"},
            writer=raw, journal=journal, incidents=incidents,
            on_update=consumer, on_health_update=health_update,
            interval=0.005, build_id="temporary-storage-recovery",
        )
        captured = []
        try:
            await recorder.async_start()
            # Finish the real startup maintenance profile while storage fails.
            while True:
                record, snapshot, _ = await consumer.next()
                captured.append(record["acquisition_id"])
                assert record["ok"] is True
                assert snapshot.sample.charge_power_w == 500
                if not snapshot.health["Roles"]["flexboss_master"]["MissingProfileBlocks"]:
                    break
                consumer.advance()
            await raw.async_sync()
            await journal_writer.async_sync()
            failed = recorder.snapshot().health
            key = "RawPersistence" if failed_stream == "raw" else "JournalPersistence"
            loss = failed[key]
            assert failed["State"] == "degraded"
            assert loss["WriteErrors"] > 0
            assert loss["LastError"]
            assert loss["PersistedRecords"] == 0
            assert loss["LastSuccessfulPersistence"] is None
            assert failed["Roles"]["flexboss_master"]["State"] == "healthy"
            if failed_stream == "journal":
                assert loss["ContinuityBroken"] is True
            # Flush the transition health attempt too. Its failure must not
            # generate a self-triggering stream of further health attempts.
            for _ in range(3):
                await raw.async_sync()
                await journal_writer.async_sync()
            assert len(notifications) < 12
            assert len([entry for entry in caplog.records
                        if "persistence" in entry.message]) <= 2

            blocked.unlink()
            consumer.advance()
            resumed, _, _ = await consumer.next()
            await raw.async_sync()
            await journal_writer.async_sync()
            recovered = recorder.snapshot().health
            assert recovered["State"] == "healthy"
            assert recovered[key]["State"] == "healthy"
            assert recovered[key]["WriteErrors"] >= loss["WriteErrors"]
            assert recovered[key]["LastError"] == loss["LastError"]
            assert recovered[key]["LastSuccessfulPersistence"]
            assert recovered[key]["LastSuccessfulPersistenceAgeSeconds"] >= 0
            assert recovered["JournalPersistence"]["ContinuityBroken"] is False
            assert concurrency["maximum"] == 1
        finally:
            await recorder.async_close()
            await raw.async_close()
            await journal_writer.async_close()
        assert device.evidence_sink is None

    rows = [json.loads(line) for line in raw.active_path.read_text().splitlines()]
    health_rows = [row for row in rows if row["kind"] == "recorder_health"]
    assert health_rows[-1]["reason"] == "shutdown"
    assert any(row["health"][key]["State"] == "healthy" for row in health_rows)
    assert all(row["role"] == "recorder" and row["session_id"] == raw.session_id
               and row["build_id"] == "temporary-storage-recovery" for row in health_rows)
    assert all("values" not in json.dumps(row) and "frames" not in json.dumps(row)
               for row in health_rows)
    assert any(row.get("acquisition_id") == resumed["acquisition_id"]
               and row["kind"] == "acquisition" for row in rows)
    journal_rows = [json.loads(line) for line in journal_writer.active_path.read_text().splitlines()]
    assert all(row["session_id"] == raw.session_id for row in journal_rows)
    if failed_stream == "journal":
        assert any(row["observation"] == "recovery_baseline" for row in journal_rows)
    else:
        assert any(row["observation"] == "baseline" for row in journal_rows)


@pytest.mark.asyncio
async def test_stalled_raw_filesystem_never_blocks_role_or_journal_receipts(tmp_path, monkeypatch):
    """Block real file opening on its worker; loopback capture still advances."""
    raw = LocalDongleTraceWriter(tmp_path / "raw", max_queue_records=128)
    journal_writer = LocalDongleTraceWriter(
        tmp_path / "journal", session_id=raw.session_id,
    )
    journal = ControllerObservationJournal(journal_writer)
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original_open = Path.open

    def gated_open(path, *args, **kwargs):
        if path == raw.active_path and args and args[0] == "ab" and not release.is_set():
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise TimeoutError("test filesystem gate was not released")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", gated_open)

    async def respond(shape):
        return _words(shape)

    async with _endpoint(respond) as (device, _, concurrency):
        consumer = _LoggerConsumer()
        recorder = consumer.recorder = EG4LocalRecorder(
            devices={"flexboss_master": device}, configured_inverter_roles={"flexboss_master"},
            writer=raw, journal=journal, on_update=consumer, interval=0.005,
        )
        try:
            await recorder.async_start()
            await asyncio.wait_for(entered.wait(), 2)
            captured = []
            for index in range(3):
                record, snapshot, _ = await consumer.next()
                captured.append(record["acquisition_id"])
                assert record["ok"] is True
                assert snapshot.sample.charge_power_w == 500
                if index != 2:
                    consumer.advance()
            await journal_writer.async_sync()
            health = recorder.snapshot().health
            assert raw.health["PersistedRecords"] == 0
            assert raw.health["LastSuccessfulPersistence"] is None
            assert 0 < raw.health["QueueDepth"] <= 128
            assert health["RawPersistence"]["State"] != "healthy"
            assert journal.health["PersistedRecords"] > 0
            assert len(set(captured)) == 3
            assert concurrency["maximum"] == 1
            release.set()
            await raw.async_sync()
        finally:
            release.set()
            await recorder.async_close()
            await raw.async_close()
            await journal_writer.async_close()
    rows = [json.loads(line) for line in raw.active_path.read_text().splitlines()]
    saved = {row["acquisition_id"] for row in rows if row["kind"] == "acquisition"}
    assert set(captured) <= saved
    journal_rows = [json.loads(line) for line in journal_writer.active_path.read_text().splitlines()]
    assert journal_rows
    assert rows[0]["kind"] == "recorder_health"
    assert rows[0]["reason"] == "startup"
    assert rows[0]["health"]["RawPersistence"]["PersistedRecords"] == 0
    assert rows[-1]["reason"] == "shutdown"


@pytest.mark.asyncio
async def test_disabled_persistence_is_explicit_without_health_role_callbacks():
    updates, health_updates = [], []

    async def observed(record):
        updates.append(record)

    async def health_update():
        health_updates.append(recorder.snapshot().health)
        published.set()

    published = asyncio.Event()
    recorder = EG4LocalRecorder(
        devices={}, configured_inverter_roles=set(), writer=None,
        on_update=observed, on_health_update=health_update,
    )
    try:
        await recorder.async_start()
        await asyncio.wait_for(published.wait(), 1)
        health = recorder.snapshot().health
        assert health["State"] == "degraded"
        assert health["RawPersistence"]["Enabled"] is False
        assert health["JournalPersistence"]["Enabled"] is False
        assert health["RawPersistence"]["LastSuccessfulPersistence"] is None
        assert health_updates
        assert updates == []
    finally:
        await recorder.async_close()


@pytest.mark.asyncio
async def test_repeated_semantic_rejection_does_not_republish_settled_health(tmp_path):
    """Pending decode cannot manufacture recovery between identical failures."""
    async def respond(shape):
        values = _words(shape)
        if shape == (4, 0, 40):
            values[0] = 33280
        return values

    raw = LocalDongleTraceWriter(tmp_path)
    async with _endpoint(respond) as (device, _, _):
        consumer = _LoggerConsumer()
        recorder = consumer.recorder = EG4LocalRecorder(
            devices={"flexboss_master": device}, configured_inverter_roles={"flexboss_master"},
            writer=raw, on_update=consumer, interval=0.005,
        )

        async def completed_acquisition():
            """Let immediate primary interruptions reach the final role record."""
            while True:
                record, snapshot, _ = await consumer.next()
                if record is not None:
                    assert record["ok"] is False
                    return snapshot
                consumer.advance()

        def saved_health():
            rows = [json.loads(line) for line in raw.active_path.read_text().splitlines()]
            return [
                row for row in rows
                if row["kind"] == "recorder_health" and row["reason"] != "periodic"
            ]

        try:
            await recorder.async_start()
            while True:
                snapshot = await completed_acquisition()
                if not snapshot.health["Roles"]["flexboss_master"]["MissingProfileBlocks"]:
                    break
                consumer.advance()
            await raw.async_sync()
            before = saved_health()
            for _ in range(3):
                consumer.advance()
                await completed_acquisition()
                await raw.async_sync()
            after = saved_health()
            assert len(after) == len(before)
            assert recorder.snapshot().sample.charge_power_w is None
            assert before[-1]["health"]["State"] == after[-1]["health"]["State"] == "degraded"
        finally:
            await recorder.async_close()
            await raw.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", ["raw", "journal"])
async def test_shutdown_health_survives_full_outstanding_queue(tmp_path, monkeypatch, stream):
    """Graceful close waits for real capacity instead of dropping its final row."""
    raw = LocalDongleTraceWriter(
        tmp_path / "raw", max_queue_records=1 if stream == "raw" else 1024,
    )
    journal_writer = (
        LocalDongleTraceWriter(tmp_path / "journal", max_queue_records=1)
        if stream == "journal" else None
    )
    journal = ControllerObservationJournal(journal_writer) if journal_writer else None
    blocked = raw if stream == "raw" else journal_writer
    assert blocked is not None
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original_open = Path.open

    def gated_open(path, *args, **kwargs):
        if path == blocked.active_path and args and args[0] == "ab" and not release.is_set():
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise TimeoutError("test filesystem gate was not released")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", gated_open)

    async def role_update(record):
        raise AssertionError("health must not invent an acquisition for an absent device")

    recorder = EG4LocalRecorder(
        devices={}, configured_inverter_roles=set(), writer=raw, journal=journal, on_update=role_update,
    )
    close_task = None
    try:
        if journal is not None:
            from tests.test_local_dongle_trace import _controller_sample
            await journal.async_observe(_controller_sample())
        await recorder.async_start()
        await asyncio.wait_for(entered.wait(), 2)
        assert blocked.health["PersistedRecords"] == 0
        close_task = asyncio.create_task(recorder.async_close())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(close_task), 0.05)
    finally:
        release.set()
        if close_task is not None:
            await close_task
        else:
            await recorder.async_close()
        await raw.async_close()
        if journal_writer is not None:
            await journal_writer.async_close()
    rows = [json.loads(line) for line in raw.active_path.read_text().splitlines()]
    assert rows[-1]["kind"] == "recorder_health"
    assert rows[-1]["reason"] == "shutdown"
    assert raw.health["DroppedRecords"] == 0
    if journal_writer is not None:
        shutdown = rows[-1]["health"]["JournalPersistence"]
        assert shutdown["PendingObservations"] == 0
        assert shutdown["PersistedRecords"] == 1
        assert journal_writer.health["DroppedRecords"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("gridboss", [False, True])
async def test_filtered_measurements_do_not_manufacture_incident_recoveries(tmp_path, gridboss):
    """Identical raw evidence and decoded masking cannot alternate availability."""
    role = "gridboss" if gridboss else "flexboss_master"
    incidents = IncidentRecorder(
        tmp_path / "capture", entry_id="synthetic-filtered",
        session_id="filtered-session", build_id="filtered-build", roles={role},
        limits=RecorderLimits(min_free_bytes=0), evidence_class="synthetic",
    )
    raw = LocalDongleTraceWriter(
        incidents.raw_root, max_files=None, write_guard=incidents.write_guard("raw"),
    )
    journal_writer = LocalDongleTraceWriter(
        incidents.journal_root, max_files=None, write_guard=incidents.write_guard("journal"),
    )
    journal = ControllerObservationJournal(journal_writer)

    async def respond(shape):
        values = _words(shape, gridboss=gridboss)
        if shape == (4, 0, 40):
            if gridboss:
                values[4] = 65535  # One invalid leg masks otherwise valid aggregates.
            else:
                values[20], values[23] = 3300, 6000  # Raw overvoltage, decoded unavailable.
        return values

    def triggers():
        root = Path(incidents.health["LatestIncidentPath"])
        manifest = json.loads((root / "manifest.json").read_text())
        return [
            json.loads(line)
            for item in manifest["files"] if item["kind"] == "triggers" and item["retained"]
            for line in (root / item["path"]).read_text().splitlines()
        ]

    async with _endpoint(respond, gridboss=gridboss) as (device, _, _):
        consumer = _LoggerConsumer()
        recorder = consumer.recorder = EG4LocalRecorder(
            devices={role: device}, configured_inverter_roles=set() if gridboss else {role},
            writer=raw, journal=journal, incidents=incidents, on_update=consumer,
            interval=0.005, build_id="filtered-build",
        )
        try:
            await recorder.async_start()
            await consumer.next()
            await incidents.async_tick()
            before = triggers()
            consumer.advance()
            _, snapshot, _ = await consumer.next()
            await incidents.async_tick()
            assert [row["reason"] for row in triggers()] == [row["reason"] for row in before]
            if gridboss:
                assert snapshot.gridboss.input_registers_valid is False
                assert snapshot.gridboss.ups_voltage is None
            else:
                assert snapshot.inverters[role].eps_voltage_r is None
                assert any(
                    row["reason"] == "output_voltage_anomaly"
                    and row["details"]["reported_voltage_v"] == 330
                    for row in before
                )
        finally:
            await recorder.async_close()
            await raw.async_close()
            await journal_writer.async_close()
