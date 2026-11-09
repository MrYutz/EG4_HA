"""Tests for the EG4 stateful generator and charging logger."""

from datetime import UTC, datetime, timedelta

from custom_components.eg4_monitor.eg4_logger import (
    EVENT_CHARGE_SESSION_ENDED,
    EVENT_CHARGE_SESSION_STARTED,
    EVENT_GENERATOR_STATE_CHANGED,
    EG4TelemetryLogger,
    LoggerSample,
)


BASE = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _sample(
    offset: int,
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
        _sample(0, generator_voltage=239, generator_frequency=60, generator_power=0)
    ) == []
    events = logger.process(
        _sample(1, sequence=2, generator_voltage=239, generator_frequency=60, generator_power=0)
    )
    assert len(events) == 1
    assert events[0]["event_type"] == EVENT_GENERATOR_STATE_CHANGED
    assert events[0]["to"] == "on_not_supplying"
    assert events[0]["generator_power_w"] == 0.0
    assert events[0]["confidence"] == "high"

    logger.process(
        _sample(2, sequence=3, generator_voltage=239, generator_frequency=60, generator_power=2500)
    )
    events = logger.process(
        _sample(3, sequence=4, generator_voltage=239, generator_frequency=60, generator_power=2500)
    )
    assert events[0]["to"] == "running_supplying"
    assert logger.snapshot(BASE + timedelta(seconds=10))["Generator"]["RunDurationSeconds"] == 9.0

    logger.process(_sample(4, sequence=5, generator_voltage=0, generator_frequency=0, generator_power=0))
    events = logger.process(
        _sample(5, sequence=6, generator_voltage=0, generator_frequency=0, generator_power=0)
    )
    assert events[0]["to"] == "off"


def test_generator_invalid_samples_become_unknown_not_off() -> None:
    logger = EG4TelemetryLogger("entry")
    logger.process(_sample(0, generator_voltage=239, generator_frequency=60, generator_power=2500))
    logger.process(_sample(1, sequence=2, generator_voltage=239, generator_frequency=60, generator_power=2500))

    logger.process(_sample(2, sequence=3, generator_valid=False, generator_voltage=None, generator_frequency=None, generator_power=None))
    events = logger.process(
        _sample(3, sequence=4, generator_valid=False, generator_voltage=None, generator_frequency=None, generator_power=None)
    )
    assert events[0]["event_type"] == EVENT_GENERATOR_STATE_CHANGED
    assert events[0]["to"] == "unknown"
    assert events[0]["confidence"] == "unknown"
    assert logger.snapshot(BASE + timedelta(seconds=4))["Generator"]["LastKnownRunState"] == "running_supplying"


def test_charge_session_tracks_start_end_duration_and_energy() -> None:
    logger = EG4TelemetryLogger("entry")
    assert logger.process(_sample(0, charge_power=500)) == []
    events = logger.process(_sample(1, sequence=2, charge_power=500))
    start_event = next(
        event for event in events if event["event_type"] == EVENT_CHARGE_SESSION_STARTED
    )
    session_id = start_event["session"]["session_id"]

    logger.process(_sample(2, sequence=3, charge_power=500))
    logger.process(_sample(3, sequence=4, charge_power=500))
    for offset in range(4, 14):
        assert logger.process(
            _sample(offset, sequence=offset + 1, charge_power=0)
        ) == []
    events = logger.process(_sample(14, sequence=15, charge_power=0))
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
    logger.process(_sample(0, charge_power=500))
    logger.process(_sample(1, sequence=2, charge_power=500))
    logger.process(
        _sample(400, sequence=3, charge_power=None, charge_source_available=False)
    )
    charging = logger.snapshot(BASE + timedelta(seconds=400))["Charging"]
    assert charging["Active"] is True
    assert charging["Interrupted"] is True
    assert charging["ActiveSession"]["quality"] == "interrupted"


def test_restore_does_not_reemit_existing_active_session() -> None:
    first = EG4TelemetryLogger("entry")
    first.process(_sample(0, charge_power=500))
    first.process(_sample(1, sequence=2, charge_power=500))

    second = EG4TelemetryLogger("entry")
    assert second.restore(first.to_dict()) is True
    assert second.process(_sample(2, sequence=3, charge_power=500)) == []
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
