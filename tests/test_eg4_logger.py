"""Tests for the EG4 stateful generator and charging logger."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.eg4_monitor.eg4_logger import (
    EVENT_CHARGE_SESSION_ENDED,
    EVENT_CHARGE_SESSION_STARTED,
    EVENT_GENERATOR_STATE_CHANGED,
    EVENT_GENERATOR_TRANSFER_CHANGED,
    EG4TelemetryLogger,
    LoggerSample,
)


BASE = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _sample(
    offset: int,
    acquisition_id: str,
    *,
    sequence: int = 1,
    generator_voltage: float | None = 0.0,
    generator_frequency: float | None = 0.0,
    generator_power: float | None = 0.0,
    generator_valid: bool = True,
    charge_power: float | None = 0.0,
    charge_source_available: bool = True,
) -> LoggerSample:
    return LoggerSample(
        generator_acquisition_id=f"gridboss:{acquisition_id}",
        generator_acquired_at=BASE + timedelta(seconds=offset),
        charge_acquisition_id=f"master:{acquisition_id}",
        charge_acquired_at=BASE + timedelta(seconds=offset),
        charge_role_context={
            "master": {
                "available": charge_source_available,
                "acquisition_id": acquisition_id,
                "acquired_at": (BASE + timedelta(seconds=offset)).isoformat(timespec="milliseconds"),
                "age_seconds": 0.0,
                "charge_power_w": charge_power,
            },
        },
        observed_at=BASE + timedelta(seconds=offset),
        sequence=sequence,
        source_serial="GRID123",
        generator_source_available=True,
        generator_input_registers_valid=generator_valid,
        generator_voltage=generator_voltage,
        generator_frequency=generator_frequency,
        generator_power=generator_power,
        transfer_state="no_ac_source",
        charge_source_available=charge_source_available,
        charge_power_w=charge_power,
        charge_source="local_flexboss",
    )


def test_generator_requires_debounce_and_uses_voltage_power_states() -> None:
    logger = EG4TelemetryLogger("entry")

    assert logger.process(
        _sample(0, "read-0", generator_voltage=239, generator_frequency=60, generator_power=0)
    ) == []
    events = logger.process(
        _sample(1, "read-1", sequence=2, generator_voltage=239, generator_frequency=60, generator_power=0)
    )
    assert len(events) == 1
    assert events[0]["event_type"] == EVENT_GENERATOR_STATE_CHANGED
    assert events[0]["to"] == "on_not_supplying"
    assert events[0]["generator_power_w"] == 0.0
    assert events[0]["confidence"] == "high"

    logger.process(
        _sample(2, "read-2", sequence=3, generator_voltage=239, generator_frequency=60, generator_power=2500)
    )
    events = logger.process(
        _sample(3, "read-3", sequence=4, generator_voltage=239, generator_frequency=60, generator_power=2500)
    )
    assert events[0]["to"] == "running_supplying"
    assert logger.snapshot(BASE + timedelta(seconds=10))["Generator"]["RunDurationSeconds"] == 9.0

    logger.process(_sample(4, "read-4", sequence=5, generator_voltage=0, generator_frequency=0, generator_power=0))
    events = logger.process(
        _sample(5, "read-5", sequence=6, generator_voltage=0, generator_frequency=0, generator_power=0)
    )
    assert events[0]["to"] == "off"


def test_generator_invalid_samples_become_unknown_not_off() -> None:
    logger = EG4TelemetryLogger("entry")
    logger.process(_sample(0, "read-0", generator_voltage=239, generator_frequency=60, generator_power=2500))
    logger.process(_sample(1, "read-1", sequence=2, generator_voltage=239, generator_frequency=60, generator_power=2500))

    logger.process(_sample(2, "read-2", sequence=3, generator_valid=False, generator_voltage=None, generator_frequency=None, generator_power=None))
    events = logger.process(
        _sample(3, "read-3", sequence=4, generator_valid=False, generator_voltage=None, generator_frequency=None, generator_power=None)
    )
    assert events[0]["event_type"] == EVENT_GENERATOR_STATE_CHANGED
    assert events[0]["to"] == "unknown"
    assert events[0]["confidence"] == "unknown"
    assert logger.snapshot(BASE + timedelta(seconds=4))["Generator"]["LastKnownRunState"] == "running_supplying"


def test_charge_session_tracks_start_end_duration_and_energy() -> None:
    logger = EG4TelemetryLogger("entry")
    assert logger.process(_sample(0, "read-0", charge_power=500)) == []
    events = logger.process(_sample(1, "read-1", sequence=2, charge_power=500))
    start_event = next(
        event for event in events if event["event_type"] == EVENT_CHARGE_SESSION_STARTED
    )
    session_id = start_event["session"]["session_id"]

    logger.process(_sample(2, "read-2", sequence=3, charge_power=500))
    logger.process(_sample(3, "read-3", sequence=4, charge_power=500))
    for offset in range(4, 14):
        assert logger.process(
            _sample(offset, f"read-{offset}", sequence=offset + 1, charge_power=0)
        ) == []
    events = logger.process(_sample(14, "read-14", sequence=15, charge_power=0))
    end_event = next(
        event for event in events if event["event_type"] == EVENT_CHARGE_SESSION_ENDED
    )
    assert end_event["session"]["session_id"] == session_id
    assert end_event["session"]["duration_seconds"] == 13.0
    assert end_event["session"]["energy_estimated_kwh"] > 0
    assert end_event["confidence"] == "high"

    snapshot = logger.snapshot(BASE + timedelta(seconds=15))["Charging"]
    assert snapshot["LastSession"]["ended_at"] == (BASE + timedelta(seconds=14)).isoformat(timespec="milliseconds")
    assert snapshot["SessionCount"] == 1
    assert snapshot["RetainedSessionCount"] == 1
    assert "CompletedSessions" not in snapshot


def test_unknown_charge_gap_keeps_active_session_interrupted() -> None:
    logger = EG4TelemetryLogger("entry")
    logger.process(_sample(0, "read-0", charge_power=500))
    logger.process(_sample(1, "read-1", sequence=2, charge_power=500))
    logger.process(
        _sample(400, "read-400", sequence=3, charge_power=None, charge_source_available=False)
    )
    charging = logger.snapshot(BASE + timedelta(seconds=400))["Charging"]
    assert charging["Active"] is True
    assert charging["Interrupted"] is True
    assert charging["ActiveSession"]["quality"] == "interrupted"


def test_restore_does_not_reemit_existing_active_session() -> None:
    first = EG4TelemetryLogger("entry")
    first.process(_sample(0, "read-0", charge_power=500))
    first.process(_sample(1, "read-1", sequence=2, charge_power=500))

    second = EG4TelemetryLogger("entry")
    assert second.restore(first.to_dict()) is True
    assert second.process(_sample(2, "read-2", sequence=3, charge_power=500)) == []
    restored = second.snapshot(BASE + timedelta(seconds=2))["Charging"]
    assert restored["Active"] is True
    assert restored["Quality"] == "interrupted"
    assert restored["ActiveSession"]["interrupted"] is True


def test_corrupt_store_counters_are_ignored() -> None:
    logger = EG4TelemetryLogger("entry")
    assert logger.restore(
        {
            "schema_version": 1,
            "entry_id": "entry",
            "generator": {
                "candidate_count": "bad",
                "unknown_count": None,
                "transition_count": "bad",
            },
            "charging": {
                "candidate_count": "bad",
                "session_count": None,
            },
        }
    ) is True
    snapshot = logger.snapshot(BASE)
    assert snapshot["Generator"]["TransitionCount"] == 0
    assert snapshot["Charging"]["SessionCount"] == 0


def test_live_generator_voltage_with_zero_qualified_frequency_is_unknown() -> None:
    logger = EG4TelemetryLogger("entry")
    logger.process(_sample(
        0, "read-0", sequence=1, generator_voltage=239,
        generator_frequency=0, generator_power=0,
    ))
    logger.process(_sample(
        1, "read-1", sequence=2, generator_voltage=239,
        generator_frequency=0, generator_power=0,
    ))
    generator = logger.snapshot(BASE + timedelta(seconds=1))["Generator"]
    assert generator["PresenceState"] == "present"
    assert generator["SupplyState"] == "not_supplying"
    assert generator["RunState"] == "unknown"
    assert generator["Quality"] == "interrupted"
    assert generator["Confidence"] == "unknown"
    assert "frequency" in generator["Reason"]
    assert generator["Reason"] != "generator_voltage_absent"


def test_cached_acquisitions_cannot_debounce_or_refresh_source_age() -> None:
    logger = EG4TelemetryLogger("entry")
    source = _sample(
        0, "original", generator_voltage=239, generator_frequency=60,
        generator_power=500, charge_power=3600,
    )
    assert logger.process(source) == []
    cached = replace(source, observed_at=BASE + timedelta(seconds=20), sequence=99)
    assert logger.process(cached) == []
    snapshot = logger.snapshot(cached.observed_at)
    assert snapshot["Generator"]["RunState"] == "unknown"
    assert snapshot["Charging"]["ActiveSession"] is None
    assert snapshot["Generator"]["SampleAgeSeconds"] == 20.0
    assert snapshot["Charging"]["SampleAgeSeconds"] == 20.0

    fresh = replace(
        _sample(
            1, "next", generator_voltage=239, generator_frequency=60,
            generator_power=500, charge_power=3600,
        ),
        observed_at=BASE + timedelta(seconds=21),
    )
    events = logger.process(fresh)
    assert {event["event_type"] for event in events} == {
        EVENT_GENERATOR_STATE_CHANGED, EVENT_CHARGE_SESSION_STARTED,
    }
    for event in events:
        assert event["generator_acquisition_id"] == "gridboss:next"
        assert event["charge_acquisition_id"] == "master:next"
        assert event["generator_acquired_at"] == (BASE + timedelta(seconds=1)).isoformat(timespec="milliseconds")
        assert event["observed_at"] == fresh.observed_at.isoformat(timespec="milliseconds")
        assert event["charge_role_context"]["master"]["acquisition_id"] == "next"
    assert logger.process(replace(
        fresh, observed_at=BASE + timedelta(seconds=120), sequence=100,
    )) == []
    charging = logger.snapshot(BASE + timedelta(seconds=120))["Charging"]
    assert charging["SampleAgeSeconds"] == 119.0
    assert charging["ActiveSession"]["energy_estimated_kwh"] == 0.0


def test_partial_zero_slave_keeps_session_interrupted_with_all_role_context() -> None:
    logger = EG4TelemetryLogger("entry")
    logger.process(_sample(0, "start-0", charge_power=500))
    logger.process(_sample(1, "start-1", charge_power=500))
    session_id = logger.snapshot(BASE + timedelta(seconds=1))["Charging"]["ActiveSession"]["session_id"]
    partial = replace(
        _sample(2, "partial", charge_power=None, charge_source_available=False),
        charge_acquisition_id=None,
        charge_acquired_at=None,
        charge_role_context={
            "master": {
                "available": False, "acquisition_id": None, "acquired_at": None,
                "age_seconds": None, "charge_power_w": None,
            },
            "slave": {
                "available": True, "acquisition_id": "slave-zero",
                "acquired_at": (BASE + timedelta(seconds=2)).isoformat(timespec="milliseconds"),
                "age_seconds": 0.0, "charge_power_w": 0.0,
            },
        },
    )
    events = logger.process(partial)
    assert not any(event["event_type"] == EVENT_CHARGE_SESSION_ENDED for event in events)
    charging = logger.snapshot(partial.observed_at)["Charging"]
    assert charging["ChargePowerW"] is None
    assert charging["Quality"] == "interrupted"
    assert charging["ActiveSession"]["session_id"] == session_id
    assert charging["ActiveSession"]["interrupted"] is True
    assert charging["Active"] is True
    assert charging["RoleContext"] == partial.charge_role_context
    assert charging["LastSession"] is None


@pytest.mark.parametrize(
    ("power", "available"),
    [(3600.0, False), (None, True), (7.0, True)],
)
def test_unknown_charge_endpoints_exclude_both_adjacent_energy_intervals(
    power: float | None, available: bool,
) -> None:
    logger = EG4TelemetryLogger("entry")
    logger.process(_sample(0, "start-0", charge_power=3600))
    logger.process(_sample(1, "start-1", charge_power=3600))
    logger.process(_sample(2, "known-2", charge_power=3600))
    logger.process(_sample(
        3, "unknown-3", charge_power=power, charge_source_available=available,
    ))
    logger.process(_sample(4, "resumed-4", charge_power=3600))
    charging = logger.snapshot(BASE + timedelta(seconds=4))["Charging"]
    assert charging["ActiveSession"]["energy_estimated_kwh"] == 0.001
    assert charging["ActiveSession"]["interrupted"] is True
    logger.process(_sample(5, "known-5", charge_power=3600))
    assert logger.snapshot(BASE + timedelta(seconds=5))["Charging"]["ActiveSession"]["energy_estimated_kwh"] == 0.002


def test_stop_dwell_uses_new_acquisition_times_not_cached_consumption() -> None:
    logger = EG4TelemetryLogger("entry")
    logger.process(_sample(0, "start-0", charge_power=500))
    logger.process(_sample(1, "start-1", charge_power=500))
    zero = _sample(2, "zero-2")
    logger.process(zero)
    assert logger.process(replace(
        zero, observed_at=BASE + timedelta(seconds=12), sequence=100,
    )) == []
    assert logger.process(replace(
        _sample(3, "zero-3"), observed_at=BASE + timedelta(seconds=13),
    )) == []
    events = logger.process(_sample(12, "zero-12"))
    assert [event["event_type"] for event in events] == [EVENT_CHARGE_SESSION_ENDED]


@pytest.mark.parametrize("gap", ["unknown", "long_gap", "restart"])
def test_stop_dwell_restarts_after_interruption(gap: str) -> None:
    logger = EG4TelemetryLogger("entry")
    logger.process(_sample(0, "start-0", charge_power=500))
    logger.process(_sample(1, "start-1", charge_power=500))
    logger.process(_sample(2, "zero-before-gap"))
    if gap == "unknown":
        logger.process(_sample(
            3, "unknown", charge_power=None, charge_source_available=False,
        ))
    elif gap == "restart":
        restored = EG4TelemetryLogger("entry")
        assert restored.restore(logger.to_dict()) is True
        logger = restored
    resumed_at = 400 if gap == "long_gap" else 20
    assert logger.process(_sample(resumed_at, "resumed-zero")) == []
    assert logger.process(_sample(resumed_at + 1, "next-zero")) == []
    charging = logger.snapshot(BASE + timedelta(seconds=resumed_at + 1))["Charging"]
    assert charging["ActiveSession"]["interrupted"] is True
    assert charging["LastSession"] is None
    events = logger.process(_sample(resumed_at + 10, "confirmed-zero"))
    assert [event["event_type"] for event in events] == [EVENT_CHARGE_SESSION_ENDED]
    assert events[0]["session"]["quality"] == "interrupted"


@pytest.mark.parametrize("gap", ["unknown", "long_gap", "restart"])
def test_pending_normal_debounce_does_not_survive_interruption(gap: str) -> None:
    logger = EG4TelemetryLogger("entry")
    source = _sample(
        0, "candidate", generator_voltage=239, generator_frequency=60,
        generator_power=500, charge_power=500,
    )
    logger.process(source)
    if gap == "unknown":
        logger.process(replace(
            _sample(1, "unknown", charge_power=None, charge_source_available=False),
            generator_source_available=False,
        ))
    elif gap == "restart":
        restored = EG4TelemetryLogger("entry")
        assert restored.restore(logger.to_dict()) is True
        logger = restored
    resumed_at = 400 if gap == "long_gap" else 2
    resumed = replace(
        source,
        observed_at=BASE + timedelta(seconds=resumed_at),
        generator_acquisition_id="gridboss:resumed",
        generator_acquired_at=BASE + timedelta(seconds=resumed_at),
        charge_acquisition_id="master:resumed",
        charge_acquired_at=BASE + timedelta(seconds=resumed_at),
    )
    assert logger.process(resumed) == []
    events = logger.process(replace(
        resumed,
        observed_at=resumed.observed_at + timedelta(seconds=1),
        generator_acquisition_id="gridboss:confirmed",
        generator_acquired_at=BASE + timedelta(seconds=resumed_at + 1),
        charge_acquisition_id="master:confirmed",
        charge_acquired_at=BASE + timedelta(seconds=resumed_at + 1),
    ))
    assert {event["event_type"] for event in events} == {
        EVENT_GENERATOR_STATE_CHANGED, EVENT_CHARGE_SESSION_STARTED,
    }


@pytest.mark.parametrize("gap", ["long_gap", "restart"])
def test_resumed_charge_starts_new_energy_endpoint_without_new_session(gap: str) -> None:
    logger = EG4TelemetryLogger("entry")
    logger.process(_sample(0, "start-0", charge_power=3600))
    logger.process(_sample(1, "start-1", charge_power=3600))
    logger.process(_sample(2, "known-2", charge_power=3600))
    session_id = logger.snapshot(BASE + timedelta(seconds=2))["Charging"]["ActiveSession"]["session_id"]
    if gap == "restart":
        restored = EG4TelemetryLogger("entry")
        assert restored.restore(logger.to_dict()) is True
        logger = restored
    resumed_at = 400 if gap == "long_gap" else 10
    assert logger.process(_sample(resumed_at, "resumed", charge_power=3600)) == []
    charging = logger.snapshot(BASE + timedelta(seconds=resumed_at))["Charging"]
    assert charging["ActiveSession"]["session_id"] == session_id
    assert charging["ActiveSession"]["energy_estimated_kwh"] == 0.001
    assert charging["ActiveSession"]["interrupted"] is True
    assert logger.process(_sample(resumed_at + 1, "next", charge_power=3600)) == []
    assert logger.snapshot(BASE + timedelta(seconds=resumed_at + 1))["Charging"]["ActiveSession"]["energy_estimated_kwh"] == 0.002


@pytest.mark.parametrize("gap", ["missing", "long_gap", "restart"])
def test_transfer_resumption_preserves_last_known_and_marks_uncertainty(gap: str) -> None:
    logger = EG4TelemetryLogger("entry")
    source = replace(_sample(0, "transfer-before"), transfer_state="generator")
    logger.process(source)
    if gap == "missing":
        logger.process(replace(
            _sample(1, "missing"), generator_source_available=False,
            transfer_state=None,
        ))
        assert logger.snapshot(BASE + timedelta(seconds=1))["Generator"]["TransferObservation"] == "generator"
    elif gap == "restart":
        restored = EG4TelemetryLogger("entry")
        assert restored.restore(logger.to_dict()) is True
        logger = restored
    resumed_at = 400 if gap == "long_gap" else 2
    resumed = replace(
        _sample(resumed_at, "transfer-after"), transfer_state="grid",
    )
    events = logger.process(resumed)
    transfers = [
        event for event in events
        if event["event_type"] == EVENT_GENERATOR_TRANSFER_CHANGED
    ]
    assert len(transfers) == 1
    assert transfers[0]["from"] == "generator"
    assert transfers[0]["to"] == "grid"
    assert transfers[0]["intervening_unknown"] is True
    assert transfers[0]["observed_at"] == resumed.observed_at.isoformat(timespec="milliseconds")
    assert logger.process(replace(
        resumed, observed_at=resumed.observed_at + timedelta(seconds=1), sequence=100,
    )) == []
