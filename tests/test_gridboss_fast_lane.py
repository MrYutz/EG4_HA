"""Held-open dongle connection and the recorder's GridBOSS fast lane over real TCP."""
from __future__ import annotations

import asyncio
import contextlib
import json

import pytest

from custom_components.eg4_monitor.local_dongle import EG4GridBossDongle, EG4LocalDongle
from custom_components.eg4_monitor.local_dongle_trace import LocalDongleTraceWriter
from custom_components.eg4_monitor.local_recorder import EG4LocalRecorder
from custom_components.eg4_monitor.register_catalog import (
    LOCAL_GRIDBOSS_FAST_BLOCK,
    capture_profile,
)
from tests.test_local_dongle import _fixture_response

DONGLE = "DJ12345678"
SERIAL = "INV7654321"
FAST = LOCAL_GRIDBOSS_FAST_BLOCK


@contextlib.asynccontextmanager
async def _persistent_endpoint(respond, *, client_type=EG4GridBossDongle, timeout=2.0):
    """Greet each connection, then answer any number of requests on it.

    ``respond(connection_index, shape)`` returns register words, ``None`` to
    stay silent, or a list of raw frames to send verbatim.
    """
    handlers = set()
    log = {"connections": 0, "requests": [], "closed": 0}

    async def handle(reader, stream):
        task = asyncio.current_task()
        handlers.add(task)
        log["connections"] += 1
        index = log["connections"]
        try:
            stream.write(_fixture_response(
                dongle_serial=DONGLE, inverter_serial=SERIAL, register=0, values=[33280] * 80,
                function=3,
            ))
            await stream.drain()
            while True:
                packet = await reader.readexactly(38)
                shape = (
                    packet[21], int.from_bytes(packet[32:34], "little"),
                    int.from_bytes(packet[34:36], "little"),
                )
                log["requests"].append((index, shape))
                answer = await respond(index, shape)
                if answer is None:
                    continue
                if answer and isinstance(answer[0], bytes):
                    for frame in answer:
                        stream.write(frame)
                else:
                    stream.write(_fixture_response(
                        dongle_serial=DONGLE, inverter_serial=SERIAL,
                        register=shape[1], values=answer, function=shape[0],
                    ))
                await stream.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            log["closed"] += 1
            stream.close()
            with contextlib.suppress(ConnectionError):
                await stream.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    device = client_type(
        host="127.0.0.1", port=server.sockets[0].getsockname()[1], timeout=timeout,
    )
    try:
        yield device, log
    finally:
        with contextlib.suppress(Exception):
            await device.async_release()
        server.close()
        await server.wait_closed()
        for task in tuple(handlers):
            task.cancel()
        await asyncio.gather(*tuple(handlers), return_exceptions=True)


def _gridboss_words(shape, *, generator=2400):
    values = [0] * shape[2]
    if shape == (4, 0, 40):
        values[2], values[3] = 2400, generator
        values[30], values[31] = 250, 250
    elif shape == (4, 120, 12):
        values[8], values[10] = 6000, 6000
    elif shape == FAST:
        values[1], values[4], values[18], values[19] = 0, 2, 0x8200, 3
    return values


def _lane(recorder):
    """Read fast-lane health now; production bounds this summary to 30 s."""
    recorder._health_cache = None
    return recorder.snapshot().health["Roles"]["gridboss"]["FastLane"]


async def _until(predicate, seconds=3.0):
    deadline = asyncio.get_running_loop().time() + seconds
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_keep_open_reuses_one_connection_and_release_closes_it():
    evidence = []

    async def respond(_index, shape):
        return list(range(shape[2]))

    async def sink(record):
        evidence.append(record)

    async with _persistent_endpoint(respond, client_type=EG4LocalDongle) as (device, log):
        device.evidence_sink = sink
        device.keep_open = True
        for _ in range(3):
            frame = await device.async_read_register_block(568, 21)
            assert list(frame.values) == list(range(21))
        assert log["connections"] == 1
        assert [row["connection"] for row in evidence] == ["new", "held", "held"]
        # Only the first read waited for the greeting frame.
        assert [len(row["frames"]) for row in evidence] == [2, 1, 1]
        assert all(row["observed_dongle_serial"] == DONGLE for row in evidence)
        assert all(row["observed_inverter_serial"] == SERIAL for row in evidence)

        await device.async_release()
        await _until(lambda: log["closed"] == 1)

        device.keep_open = False
        await device.async_read_register_block(568, 21)
        await _until(lambda: log["closed"] == 2)
        assert log["connections"] == 2
        assert evidence[-1]["connection"] == "new"


@pytest.mark.asyncio
async def test_default_client_still_uses_one_connection_per_read():
    async def respond(_index, shape):
        return [7] * shape[2]

    async with _persistent_endpoint(respond, client_type=EG4LocalDongle) as (device, log):
        await device.async_read_register_block(0, 40)
        await device.async_read_register_block(0, 40)
        await _until(lambda: log["closed"] == 2)
        assert log["connections"] == 2


@pytest.mark.asyncio
async def test_failed_held_read_closes_connection_so_late_reply_cannot_pair():
    release_late = asyncio.Event()

    async def respond(index, shape):
        if index == 1 and len([r for r in seen if r[0] == 1]) == 1:
            seen.append((index, shape))
            return [111] * shape[2]
        if index == 1:
            # Second request on the first connection: answer far too late.
            seen.append((index, shape))
            await release_late.wait()
            return [666] * shape[2]
        seen.append((index, shape))
        return [222] * shape[2]

    seen: list = [(1, None)]
    async with _persistent_endpoint(respond, client_type=EG4LocalDongle) as (device, log):
        device.keep_open = True
        first = await device.async_read_register_block(568, 21)
        assert set(first.values) == {111}
        with pytest.raises(TimeoutError):
            await device.async_read_register_block(568, 21, timeout=0.1)
        assert device._held is None
        release_late.set()
        third = await device.async_read_register_block(568, 21)
        assert set(third.values) == {222}
        assert log["connections"] == 2


@pytest.mark.asyncio
async def test_unrequested_frames_on_held_connection_are_kept_but_not_matched():
    evidence = []

    async def sink(record):
        evidence.append(record)

    async def respond(_index, shape):
        if len(evidence) == 0:
            return [5] * shape[2]
        unrelated = _fixture_response(
            dongle_serial=DONGLE, inverter_serial=SERIAL, register=0, values=[9] * 100,
        )
        reply = _fixture_response(
            dongle_serial=DONGLE, inverter_serial=SERIAL, register=shape[1],
            values=[6] * shape[2], function=shape[0],
        )
        return [unrelated, reply]

    async with _persistent_endpoint(respond, client_type=EG4LocalDongle) as (device, log):
        device.evidence_sink = sink
        device.keep_open = True
        await device.async_read_register_block(568, 21)
        second = await device.async_read_register_block(568, 21)
        assert set(second.values) == {6}
        assert log["connections"] == 1
        classes = [frame["classification"] for frame in evidence[1]["frames"]]
        assert classes[-1] == "matching" and len(classes) == 2 and classes[0] != "matching"


def test_fast_block_is_inside_the_dynamic_block_and_listed_in_the_profile():
    assert FAST == (4, 568, 21)
    assert 500 <= FAST[1] and FAST[1] + FAST[2] <= 600
    assert capture_profile("gridboss")["fast"] == [list(FAST)]
    assert capture_profile("flexboss_master")["fast"] == []


def test_recorder_rejects_unknown_fast_mode():
    async def noop(_record):
        return None

    with pytest.raises(ValueError):
        EG4LocalRecorder(
            devices={}, configured_inverter_roles=set(), writer=None,
            on_update=noop, gridboss_fast_mode="sometimes",
        )


@pytest.mark.asyncio
async def test_fast_lane_interleaves_reads_on_one_held_connection(tmp_path):
    records = asyncio.Queue()
    permits = asyncio.Queue()

    async def respond(_index, shape):
        return _gridboss_words(shape)

    async def consume(record):
        records.put_nowait(record)
        await permits.get()

    async with _persistent_endpoint(respond) as (device, log):
        writer = LocalDongleTraceWriter(tmp_path)
        recorder = EG4LocalRecorder(
            devices={"gridboss": device}, configured_inverter_roles=set(), writer=writer,
            on_update=consume, interval=0.005, gridboss_fast_mode="always",
            gridboss_fast_reads=2,
        )
        try:
            await recorder.async_start()
            first = await asyncio.wait_for(records.get(), 3)
            assert first["ok"] is True
            shapes = [shape for _, shape in log["requests"]]
            assert shapes == [
                (4, 0, 40), FAST, (4, 120, 12), FAST, FAST,
                (4, 500, 100), FAST, FAST, (3, 4500, 10), FAST, FAST,
            ]
            # Fast reads are their own acquisitions: profile accounting is unchanged.
            assert len(first["transaction_ids"]) == 4
            assert first["failed_transaction_ids"] == []
            assert first["errors"] == []
            assert log["connections"] == 1
            lane = _lane(recorder)
            assert lane["Mode"] == "always" and lane["Active"] is True
            assert lane["Samples"] == 7 and lane["Failures"] == 0
            assert lane["LastConnection"] == "held"
            assert lane["SpacingSamples"] == 6
            permits.put_nowait(None)
            second = await asyncio.wait_for(records.get(), 3)
            assert second["ok"] is True
            assert log["connections"] == 1
        finally:
            permits.put_nowait(None)
            await recorder.async_close()
            await writer.async_close()
        await _until(lambda: log["closed"] == 1)

    rows = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
    fast_rows = [row for row in rows if row.get("kind") == "transaction" and row.get("lane") == "fast"]
    assert len(fast_rows) >= 7
    assert all(row["ok"] and row["connection"] == "held" for row in fast_rows)
    assert all(
        (row["request"]["function"], row["request"]["register"], row["request"]["count"]) == FAST
        for row in fast_rows
    )
    assert fast_rows[0]["frames"][-1]["values"][18] == 0x8200
    profile_ids = {
        tid for row in rows if row.get("kind") == "acquisition" for tid in row["transaction_ids"]
    }
    assert not profile_ids & {row["transaction_id"] for row in fast_rows}


@pytest.mark.asyncio
async def test_generator_mode_follows_generator_voltage_and_releases_connection():
    generator = {"volts": 20}
    records = asyncio.Queue()
    permits = asyncio.Queue()

    async def respond(_index, shape):
        return _gridboss_words(shape, generator=generator["volts"])

    async def consume(record):
        records.put_nowait(record)
        await permits.get()

    async def next_round():
        permits.put_nowait(None)
        return await asyncio.wait_for(records.get(), 3)

    async with _persistent_endpoint(respond) as (device, log):
        recorder = EG4LocalRecorder(
            devices={"gridboss": device}, configured_inverter_roles=set(), writer=None,
            on_update=consume, interval=0.005, gridboss_fast_mode="generator",
            gridboss_fast_reads=1, gridboss_fast_hold=0.0,
        )
        try:
            await recorder.async_start()
            await asyncio.wait_for(records.get(), 3)
            await next_round()
            # Generator absent: no fast reads, one connection per read as before.
            assert FAST not in [shape for _, shape in log["requests"]]
            assert log["connections"] == len(log["requests"])
            lane = _lane(recorder)
            assert lane["Active"] is False and lane["Samples"] == 0

            generator["volts"] = 2400
            await next_round()  # this round observes the generator
            before = len(log["requests"])
            await next_round()  # the following round runs the lane
            shapes = [shape for _, shape in log["requests"][before:]]
            assert shapes.count(FAST) == 4
            assert shapes[:3] == [(4, 0, 40), FAST, (4, 120, 12)]
            held_from = log["requests"][before][0]
            assert {index for index, _ in log["requests"][before:]} == {held_from}
            assert recorder._roles["gridboss"].fast_active is True

            generator["volts"] = 20
            await next_round()  # this round's own read sees it gone and stops the lane
            assert recorder._roles["gridboss"].fast_active is False
            before = len(log["requests"])
            closed_before = log["closed"]
            await next_round()
            assert FAST not in [shape for _, shape in log["requests"][before:]]
            assert recorder._roles["gridboss"].fast_active is False
            assert device.keep_open is False and device._held is None
            assert log["closed"] > closed_before
        finally:
            permits.put_nowait(None)
            await recorder.async_close()


@pytest.mark.asyncio
async def test_fast_read_failure_never_fails_the_round_and_capture_recovers():
    records = asyncio.Queue()
    permits = asyncio.Queue()
    silent = {"fast": True}

    async def respond(_index, shape):
        if shape == FAST and silent["fast"]:
            return None
        return _gridboss_words(shape)

    async def consume(record):
        records.put_nowait(record)
        await permits.get()

    async with _persistent_endpoint(respond) as (device, log):
        recorder = EG4LocalRecorder(
            devices={"gridboss": device}, configured_inverter_roles=set(), writer=None,
            on_update=consume, interval=0.005, gridboss_fast_mode="always",
            gridboss_fast_reads=2, gridboss_fast_timeout=0.05,
        )
        try:
            await recorder.async_start()
            first = await asyncio.wait_for(records.get(), 5)
            assert first["ok"] is True and first["errors"] == []
            assert len(first["transaction_ids"]) == 4 and first["failed_transaction_ids"] == []
            lane = _lane(recorder)
            # One failure rests the lane for the remainder of the round.
            assert lane["Samples"] == 0 and lane["Failures"] == 1
            assert "TimeoutError" in lane["LastError"]
            assert [shape for _, shape in log["requests"]].count(FAST) == 1
            recorder._health_cache = None
            role = recorder.snapshot().health["Roles"]["gridboss"]
            assert role["PrimaryAvailable"] is True and role["FailedProfileBlocks"] == []
            # The failed fast read dropped the connection; the next read reconnected.
            assert log["connections"] == 2

            silent["fast"] = False
            permits.put_nowait(None)
            second = await asyncio.wait_for(records.get(), 5)
            assert second["ok"] is True
            lane = _lane(recorder)
            assert lane["Samples"] == 7
        finally:
            permits.put_nowait(None)
            await recorder.async_close()


@pytest.mark.asyncio
async def test_fast_lane_runs_cleanly_with_journal_and_incident_recorder(tmp_path):
    """Fast transactions pass through the full persistence stack without errors."""
    from custom_components.eg4_monitor.local_dongle_trace import ControllerObservationJournal
    from custom_components.eg4_monitor.local_incidents import IncidentRecorder, RecorderLimits

    incidents = IncidentRecorder(
        tmp_path / "capture", entry_id="synthetic-fast",
        session_id="fast-session", build_id="fast-build", roles={"gridboss"},
        limits=RecorderLimits(min_free_bytes=0), evidence_class="synthetic",
    )
    raw = LocalDongleTraceWriter(
        incidents.raw_root, max_files=None, write_guard=incidents.write_guard("raw"),
    )
    journal_writer = LocalDongleTraceWriter(
        incidents.journal_root, max_files=None, write_guard=incidents.write_guard("journal"),
    )
    journal = ControllerObservationJournal(journal_writer)
    records = asyncio.Queue()
    permits = asyncio.Queue()

    async def respond(_index, shape):
        return _gridboss_words(shape)

    async def consume(record):
        records.put_nowait(record)
        await permits.get()

    async with _persistent_endpoint(respond) as (device, log):
        recorder = EG4LocalRecorder(
            devices={"gridboss": device}, configured_inverter_roles=set(),
            writer=raw, journal=journal, incidents=incidents, on_update=consume,
            interval=0.005, build_id="fast-build", gridboss_fast_mode="always",
        )
        try:
            await recorder.async_start()
            for _ in range(3):
                record = await asyncio.wait_for(records.get(), 3)
                assert record["ok"] is True and record["errors"] == []
                await incidents.async_tick()
                permits.put_nowait(None)
            state = recorder._roles["gridboss"]
            assert state.errors == 0 and state.fast_failures == 0
            assert state.fast_samples >= 14
            recorder._health_cache = None
            health = recorder.snapshot().health
            assert health["WriterError"] is None
            assert health["RawPersistence"]["State"] != "degraded"
            assert health["JournalPersistence"]["State"] != "degraded"
            assert log["connections"] == 1
        finally:
            permits.put_nowait(None)
            await recorder.async_close()
            await raw.async_close()
            await journal_writer.async_close()


# ---------------------------------------------------------------------------
# Idle cadence: slow rounds while the GridBOSS shows no generator.
# ---------------------------------------------------------------------------

def _inverter_words(shape):
    values = [0] * shape[2]
    if shape == (4, 0, 40):
        values[4], values[5], values[10] = 520, (100 << 8) | 70, 500
    elif shape == (4, 80, 40):
        values[16], values[17], values[18] = 5, 1400, 2500
        values[21], values[22] = 3297, 3278
        values[23], values[24], values[26] = 310, 290, 18
    return values


def test_age_limit_follows_the_cadence_a_sample_was_read_under():
    async def noop(_record):
        return None

    recorder = EG4LocalRecorder(
        devices={}, configured_inverter_roles=set(), writer=None, on_update=noop,
        interval=1.0, idle_interval=30.0,
    )
    assert recorder.idle_interval == 30.0
    assert recorder._age_limit(1.0) == 15.0
    assert recorder._age_limit(30.0) == 75.0
    # No GridBOSS role: nothing can say the generator is absent, so never idle.
    assert recorder._idle() is False and recorder._target_interval() == 1.0
    # An idle interval no longer than the normal one disables the feature.
    assert EG4LocalRecorder(
        devices={}, configured_inverter_roles=set(), writer=None, on_update=noop,
        interval=5.0, idle_interval=5.0,
    ).idle_interval is None


@pytest.mark.asyncio
async def test_idle_cadence_slows_both_roles_and_generator_restores_full_rate():
    generator = {"volts": 20}
    rounds = {"gridboss": [], "flexboss_master": []}

    async def grid(_index, shape):
        return _gridboss_words(shape, generator=generator["volts"])

    async def inverter(_index, shape):
        return _inverter_words(shape)

    async def consume(record):
        if record is not None:
            rounds[record["role"]].append(record)

    async with _persistent_endpoint(grid) as (grid_device, _), _persistent_endpoint(
        inverter, client_type=EG4LocalDongle,
    ) as (inverter_device, _):
        recorder = EG4LocalRecorder(
            devices={"gridboss": grid_device, "flexboss_master": inverter_device},
            configured_inverter_roles={"flexboss_master"}, writer=None, on_update=consume,
            interval=0.01, idle_interval=0.4, gridboss_fast_mode="generator",
            gridboss_fast_hold=0.0,
        )
        try:
            await recorder.async_start()
            await _until(lambda: len(rounds["gridboss"]) >= 3 and len(rounds["flexboss_master"]) >= 3, 5)
            for role in rounds:
                idle = [row for row in rounds[role] if row["cadence"] == "idle"]
                assert idle and all(row["target_interval_seconds"] == 0.4 for row in idle)
                # Rounds started under the idle cadence are spaced by it.
                assert all(row["sample_gap_seconds"] >= 0.35 for row in idle[1:])
                assert all(row["ok"] for row in rounds[role])
            assert recorder._idle() is True
            state = recorder._roles["flexboss_master"]
            assert state.sample_max_age == recorder._age_limit(0.4)
            snapshot = recorder.snapshot()
            assert snapshot.gridboss is not None and "flexboss_master" in snapshot.inverters
            assert snapshot.local_freshness["StaleRoles"] == []
            recorder._health_cache = None
            cadence = recorder.snapshot().health["Cadence"]
            assert cadence["Mode"] == "idle" and cadence["GeneratorPresent"] is False
            assert recorder._roles["gridboss"].fast_active is False

            generator["volts"] = 2400
            mark = {role: len(rows) for role, rows in rounds.items()}
            await _until(lambda: recorder._idle() is False, 3)
            await _until(
                lambda: all(len(rounds[role]) >= mark[role] + 6 for role in rounds), 3,
            )
            for role in rounds:
                recent = rounds[role][-3:]
                assert all(row["cadence"] == "active" for row in recent)
                assert all(row["target_interval_seconds"] == 0.01 for row in recent)
                assert all(row["sample_gap_seconds"] < 0.3 for row in recent)
            assert recorder._roles["gridboss"].fast_active is True
            assert recorder._roles["gridboss"].fast_samples > 0
            assert state.sample_max_age == recorder.max_age
            recorder._health_cache = None
            cadence = recorder.snapshot().health["Cadence"]
            assert cadence["Mode"] == "active" and cadence["GeneratorPresent"] is True

            generator["volts"] = 20
            await _until(lambda: recorder._idle() is True, 3)
            await _until(lambda: recorder._roles["gridboss"].fast_active is False, 3)
            assert grid_device.keep_open is False and grid_device._held is None
        finally:
            await recorder.async_close()


@pytest.mark.asyncio
async def test_idle_round_reads_several_due_maintenance_blocks():
    records = asyncio.Queue()
    permits = asyncio.Queue()

    async def respond(_index, shape):
        return _gridboss_words(shape, generator=20)

    async def consume(record):
        records.put_nowait(record)
        await permits.get()

    async with _persistent_endpoint(respond) as (device, log):
        recorder = EG4LocalRecorder(
            devices={"gridboss": device}, configured_inverter_roles=set(), writer=None,
            on_update=consume, interval=0.01, idle_interval=0.05,
        )
        try:
            await recorder.async_start()
            seen = []
            for _ in range(3):
                record = await asyncio.wait_for(records.get(), 3)
                assert record["ok"] is True
                attempted = record.get("maintenance_attempted_all", [])
                assert len(attempted) <= 4
                if attempted:
                    assert record["maintenance_attempted"] == attempted[0]
                assert len(record["transaction_ids"]) == 3 + len(attempted)
                seen.extend(attempted)
                permits.put_nowait(None)
            # All eight start-up blocks are read within three idle rounds, the
            # five-second relay-command block first.
            assert seen[0] == "3:4500:10" and len(set(seen)) == 8
        finally:
            permits.put_nowait(None)
            await recorder.async_close()


@pytest.mark.asyncio
async def test_unknown_gridboss_keeps_inverter_at_full_rate():
    rounds = []

    async def silent(_index, _shape):
        return None

    async def inverter(_index, shape):
        return _inverter_words(shape)

    async def consume(record):
        if record is not None and record["role"] == "flexboss_master":
            rounds.append(record)

    async with _persistent_endpoint(silent, timeout=0.05) as (grid_device, _), _persistent_endpoint(
        inverter, client_type=EG4LocalDongle,
    ) as (inverter_device, _):
        recorder = EG4LocalRecorder(
            devices={"gridboss": grid_device, "flexboss_master": inverter_device},
            configured_inverter_roles={"flexboss_master"}, writer=None, on_update=consume,
            interval=0.01, idle_interval=5.0,
        )
        try:
            await recorder.async_start()
            await _until(lambda: len(rounds) >= 5, 3)
            assert recorder._generator_state() is None and recorder._idle() is False
            assert all(row["cadence"] == "active" and row["ok"] for row in rounds)
        finally:
            await recorder.async_close()
