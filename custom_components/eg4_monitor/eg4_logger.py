"""Stateful EG4 generator and battery-charge event logging.

The local dongle trace is raw evidence.  This module turns fresh samples into
bounded, restart-safe state transitions without pretending that GridBOSS
terminal measurements prove the mechanical state of a generator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
from typing import Any
from uuid import uuid4

LOGGER_SCHEMA_VERSION = 1

EVENT_GENERATOR_STATE_CHANGED = "eg4_monitor_generator_state_changed"
EVENT_GENERATOR_TRANSFER_CHANGED = "eg4_monitor_generator_transfer_observation_changed"
EVENT_CHARGE_SESSION_STARTED = "eg4_monitor_charging_session_started"
EVENT_CHARGE_SESSION_ENDED = "eg4_monitor_charging_session_ended"

GENERATOR_ON_VOLTAGE = 80.0
GENERATOR_OFF_VOLTAGE = 72.0
GENERATOR_ON_FREQUENCY = 45.0
GENERATOR_OFF_FREQUENCY = 42.0
GENERATOR_ON_POWER = 100.0
GENERATOR_OFF_POWER = 50.0
GENERATOR_DEBOUNCE_SAMPLES = 2
UNKNOWN_DEBOUNCE_SAMPLES = 2
CHARGE_START_POWER = 10.0
CHARGE_STOP_POWER = 5.0
CHARGE_START_DEBOUNCE_SAMPLES = 2
CHARGE_STOP_DEBOUNCE_SAMPLES = 2
CHARGE_STOP_MIN_DWELL_SECONDS = 10.0
MAX_SAMPLE_GAP_SECONDS = 300.0
MAX_RECENT_EVENTS = 100
MAX_COMPLETED_SESSIONS = 100
SESSION_RETENTION = timedelta(days=90)


def _finite_float(value: Any) -> float | None:
    """Return a finite float or None."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _iso(value: datetime | None) -> str | None:
    """Serialize an aware timestamp in UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def _datetime(value: Any) -> datetime | None:
    """Parse a timestamp stored by the logger."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _seconds_between(later: datetime | None, earlier: datetime | None) -> float | None:
    """Return a non-negative wall-clock duration."""
    if later is None or earlier is None:
        return None
    return max(0.0, (later - earlier).total_seconds())


def _nonnegative_int(value: Any, default: int = 0) -> int:
    """Parse a bounded state counter without allowing Store corruption to raise."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, 0)


@dataclass(frozen=True)
class LoggerSample:
    """One correlated local sample used by both state machines."""

    observed_at: datetime
    generator_acquisition_id: str | None
    generator_acquired_at: datetime | None
    charge_acquisition_id: str | None
    charge_acquired_at: datetime | None
    charge_role_context: dict[str, dict[str, Any]]
    sequence: int | None = None
    source_serial: str | None = None
    charge_source_serial: str | None = None
    generator_source_available: bool = False
    generator_input_registers_valid: bool = False
    generator_voltage: float | None = None
    generator_frequency: float | None = None
    generator_power: float | None = None
    transfer_state: str | None = None
    charge_source_available: bool = False
    charge_power_w: float | None = None
    charge_source: str | None = None
    battery_voltage: float | None = None
    battery_soc: float | None = None
    process_generator: bool = True
    process_charge: bool = True


class GeneratorStateMachine:
    """Debounced GridBOSS terminal-observation state machine."""

    def __init__(self) -> None:
        self.run_state = "unknown"
        self.presence_state = "unknown"
        self.supply_state = "unknown"
        self.last_known_run_state = "unknown"
        self.quality = "unknown"
        self.reason = "no_fresh_gridboss_sample"
        self.candidate_state: str | None = None
        self.candidate_count = 0
        self.unknown_count = 0
        self.last_transition_at: datetime | None = None
        self.last_on_at: datetime | None = None
        self.last_off_at: datetime | None = None
        self.last_transition_event_id: str | None = None
        self.transition_count = 0
        self.last_observed_at: datetime | None = None
        self.last_acquisition_id: str | None = None
        self.last_acquired_at: datetime | None = None
        self.last_voltage: float | None = None
        self.last_frequency: float | None = None
        self.last_power: float | None = None
        self.last_transfer_state: str | None = None
        self.last_transfer_acquisition_id: str | None = None
        self.last_transfer_at: datetime | None = None
        self.transfer_intervening_unknown = False

    @staticmethod
    def _classify(sample: LoggerSample) -> tuple[str, str, str, str]:
        """Classify one fresh sample, preserving unknown/conflict states."""
        if (
            not sample.generator_source_available
            or sample.generator_acquisition_id is None
            or sample.generator_acquired_at is None
        ):
            return "unknown", "unknown", "unknown", "gridboss_unavailable"
        if not sample.generator_input_registers_valid:
            return "unknown", "unknown", "unknown", "gridboss_registers_invalid"

        voltage = _finite_float(sample.generator_voltage)
        frequency = _finite_float(sample.generator_frequency)
        power = _finite_float(sample.generator_power)
        if voltage is None:
            presence = "unknown"
        elif voltage >= GENERATOR_ON_VOLTAGE:
            presence = "present"
        elif voltage <= GENERATOR_OFF_VOLTAGE:
            presence = "off"
        else:
            presence = "unknown"

        if power is None:
            supply = "unknown"
        elif abs(power) >= GENERATOR_ON_POWER:
            supply = "supplying"
        elif abs(power) <= GENERATOR_OFF_POWER:
            supply = "not_supplying"
        else:
            supply = "unknown"

        if presence == "present" and (
            frequency is None or frequency < GENERATOR_ON_FREQUENCY
        ):
            return (
                "unknown", presence, supply,
                "generator_qualified_frequency_invalid_or_unavailable",
            )
        if voltage is None or frequency is None:
            return "unknown", presence, supply, "generator_voltage_or_frequency_missing"
        if presence == "unknown" or supply == "unknown":
            return "unknown", presence, supply, "generator_signal_between_hysteresis_bands"
        if presence == "off" and frequency > GENERATOR_OFF_FREQUENCY:
            return "unknown", presence, supply, "generator_voltage_frequency_conflict"
        if presence == "off" and supply == "not_supplying":
            return "off", presence, supply, "generator_voltage_absent"
        if presence == "present" and supply == "supplying":
            return "running_supplying", presence, supply, "generator_voltage_and_power_present"
        if presence == "present" and supply == "not_supplying":
            return "on_not_supplying", presence, supply, "generator_voltage_present_power_low"
        return "unknown", presence, supply, "generator_presence_supply_conflict"

    def update(self, sample: LoggerSample) -> dict[str, Any] | None:
        """Apply a sample and return a transition candidate when accepted."""
        now = sample.observed_at
        self.last_observed_at = now
        self.last_voltage = _finite_float(sample.generator_voltage)
        self.last_frequency = _finite_float(sample.generator_frequency)
        self.last_power = _finite_float(sample.generator_power)
        distinct = (
            sample.generator_source_available
            and sample.generator_acquisition_id is not None
            and sample.generator_acquired_at is not None
            and sample.generator_acquisition_id != self.last_acquisition_id
        )
        gap = _seconds_between(sample.generator_acquired_at, self.last_acquired_at)
        if distinct:
            self.last_acquisition_id = sample.generator_acquisition_id
            self.last_acquired_at = sample.generator_acquired_at
        if gap is not None and gap > MAX_SAMPLE_GAP_SECONDS:
            self.candidate_state = None
            self.candidate_count = 0
            self.unknown_count = 0
        target, presence, supply, reason = self._classify(sample)
        self.presence_state = presence
        self.supply_state = supply
        self.reason = reason
        if target == "unknown":
            self.quality = "interrupted"
        elif distinct:
            self.quality = "good"
        if target == "unknown":
            # An interruption invalidates normal debounce immediately. Unknown
            # itself retains its existing debounce rather than implying a stop.
            self.candidate_state = None
            self.candidate_count = 0
            if distinct or not sample.generator_source_available:
                self.unknown_count += 1
            if self.unknown_count < UNKNOWN_DEBOUNCE_SAMPLES:
                return None
        else:
            self.unknown_count = 0
            if not distinct:
                return None
            if target != self.candidate_state:
                self.candidate_state = target
                self.candidate_count = 1
            else:
                self.candidate_count += 1
            if self.candidate_count < GENERATOR_DEBOUNCE_SAMPLES:
                return None
        if target == self.run_state:
            return None

        previous = self.run_state
        self.run_state = target
        self.last_transition_at = now
        self.transition_count += 1
        if target in {"on_not_supplying", "running_supplying"}:
            self.last_known_run_state = target
            if previous not in {"on_not_supplying", "running_supplying"}:
                self.last_on_at = now
        elif target == "off":
            self.last_known_run_state = target
            self.last_off_at = now
        transition = {
            "event_type": EVENT_GENERATOR_STATE_CHANGED,
            "from": previous,
            "to": target,
            "reason": reason,
            "presence_state": presence,
            "supply_state": supply,
        }
        return transition

    def update_transfer(self, sample: LoggerSample) -> dict[str, Any] | None:
        """Observe raw controller state, not debounced state or contact feedback."""
        if (
            not sample.generator_source_available
            or sample.generator_acquisition_id is None
            or sample.generator_acquired_at is None
            or sample.transfer_state is None
        ):
            self.transfer_intervening_unknown = True
            return None
        if sample.generator_acquisition_id == self.last_transfer_acquisition_id:
            return None
        gap = _seconds_between(sample.generator_acquired_at, self.last_transfer_at)
        if gap is not None and gap > MAX_SAMPLE_GAP_SECONDS:
            self.transfer_intervening_unknown = True
        previous = self.last_transfer_state
        self.last_transfer_state = sample.transfer_state
        self.last_transfer_acquisition_id = sample.generator_acquisition_id
        self.last_transfer_at = sample.generator_acquired_at
        intervening_unknown = self.transfer_intervening_unknown
        self.transfer_intervening_unknown = False
        if previous is None or previous == sample.transfer_state:
            return None
        return {
            "event_type": EVENT_GENERATOR_TRANSFER_CHANGED,
            "from": previous,
            "to": sample.transfer_state,
            "reason": "gridboss_transfer_observation_changed",
            "intervening_unknown": intervening_unknown,
        }

    def snapshot(self, now: datetime) -> dict[str, Any]:
        """Return an HA-friendly view of generator state."""
        duration = None
        if self.run_state in {"on_not_supplying", "running_supplying"}:
            duration = _seconds_between(now, self.last_on_at)
        age = _seconds_between(now, self.last_acquired_at)
        return {
            "RunState": self.run_state,
            "PresenceState": self.presence_state,
            "SupplyState": self.supply_state,
            "LastKnownRunState": self.last_known_run_state,
            "Quality": self.quality,
            "Provenance": "inferred",
            "Confidence": "high" if self.quality == "good" else "unknown",
            "Source": "gridboss_direct",
            "Reason": self.reason,
            "GeneratorVoltage": self.last_voltage,
            "GeneratorFrequency": self.last_frequency,
            "GeneratorPower": self.last_power,
            "TransferObservation": self.last_transfer_state,
            "AcquisitionId": self.last_acquisition_id,
            "AcquiredAt": _iso(self.last_acquired_at),
            "SampleAgeSeconds": round(age, 3) if age is not None else None,
            "RunDurationSeconds": round(duration, 3) if duration is not None else None,
            "LastTransition": _iso(self.last_transition_at),
            "LastOn": _iso(self.last_on_at),
            "LastOff": _iso(self.last_off_at),
            "LastTransitionEventId": self.last_transition_event_id,
            "TransitionCount": self.transition_count,
            "Thresholds": {
                "OnVoltage": GENERATOR_ON_VOLTAGE,
                "OffVoltage": GENERATOR_OFF_VOLTAGE,
                "OnFrequency": GENERATOR_ON_FREQUENCY,
                "OffFrequency": GENERATOR_OFF_FREQUENCY,
                "OnPower": GENERATOR_ON_POWER,
                "OffPower": GENERATOR_OFF_POWER,
                "DebounceSamples": GENERATOR_DEBOUNCE_SAMPLES,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize restart-safe state."""
        return {
            "run_state": self.run_state,
            "presence_state": self.presence_state,
            "supply_state": self.supply_state,
            "last_known_run_state": self.last_known_run_state,
            "quality": self.quality,
            "reason": self.reason,
            "last_transition_at": _iso(self.last_transition_at),
            "last_on_at": _iso(self.last_on_at),
            "last_off_at": _iso(self.last_off_at),
            "last_transition_event_id": self.last_transition_event_id,
            "transition_count": self.transition_count,
            "last_observed_at": _iso(self.last_observed_at),
            "last_acquisition_id": self.last_acquisition_id,
            "last_acquired_at": _iso(self.last_acquired_at),
            "last_voltage": self.last_voltage,
            "last_frequency": self.last_frequency,
            "last_power": self.last_power,
            "last_transfer_state": self.last_transfer_state,
            "last_transfer_acquisition_id": self.last_transfer_acquisition_id,
            "last_transfer_at": _iso(self.last_transfer_at),
        }

    def restore(self, raw: Any) -> None:
        """Restore validated state without emitting a transition."""
        if not isinstance(raw, dict):
            return
        self.run_state = str(raw.get("run_state") or "unknown")
        self.presence_state = str(raw.get("presence_state") or "unknown")
        self.supply_state = str(raw.get("supply_state") or "unknown")
        self.last_known_run_state = str(
            raw.get("last_known_run_state") or self.run_state
        )
        self.quality = "interrupted"
        self.reason = "restored_across_unknown_gap"
        self.candidate_state = None
        self.candidate_count = 0
        self.unknown_count = 0
        self.last_transition_at = _datetime(raw.get("last_transition_at"))
        self.last_on_at = _datetime(raw.get("last_on_at"))
        self.last_off_at = _datetime(raw.get("last_off_at"))
        self.last_transition_event_id = raw.get("last_transition_event_id")
        self.transition_count = _nonnegative_int(raw.get("transition_count"))
        self.last_observed_at = _datetime(raw.get("last_observed_at"))
        self.last_acquisition_id = raw.get("last_acquisition_id")
        self.last_acquired_at = _datetime(raw.get("last_acquired_at"))
        self.last_voltage = _finite_float(raw.get("last_voltage"))
        self.last_frequency = _finite_float(raw.get("last_frequency"))
        self.last_power = _finite_float(raw.get("last_power"))
        self.last_transfer_state = raw.get("last_transfer_state")
        self.last_transfer_acquisition_id = raw.get("last_transfer_acquisition_id")
        self.last_transfer_at = _datetime(raw.get("last_transfer_at"))
        self.transfer_intervening_unknown = True


class ChargingSessionMachine:
    """Debounced, restart-safe battery charging session machine."""

    def __init__(self) -> None:
        self.state = "unknown"
        self.quality = "unknown"
        self.reason = "no_fresh_charge_sample"
        self.source = "unknown"
        self.candidate_state: str | None = None
        self.candidate_count = 0
        self.candidate_started_at: datetime | None = None
        self.unknown_count = 0
        self.active_session: dict[str, Any] | None = None
        self.last_session: dict[str, Any] | None = None
        self.completed_sessions: list[dict[str, Any]] = []
        self.session_count = 0
        self.last_observed_at: datetime | None = None
        self.last_sample_at: datetime | None = None
        self.last_charge_power_w: float | None = None
        self.last_acquisition_id: str | None = None
        self.last_acquired_at: datetime | None = None
        self.role_context: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _classify(sample: LoggerSample) -> tuple[str, str]:
        if (
            not sample.charge_source_available
            or sample.charge_acquisition_id is None
            or sample.charge_acquired_at is None
        ):
            return "unknown", "charge_source_unavailable"
        power = _finite_float(sample.charge_power_w)
        if power is None:
            return "unknown", "charge_power_missing"
        if power >= CHARGE_START_POWER:
            return "charging", "charge_power_above_start_threshold"
        if power <= CHARGE_STOP_POWER:
            return "not_charging", "charge_power_below_stop_threshold"
        return "unknown", "charge_power_between_hysteresis_bands"

    def _integrate(self, sample: LoggerSample) -> None:
        """Integrate only fresh, known charge power into the active session."""
        if (
            self.active_session is None
            or self.last_sample_at is None
            or sample.charge_acquired_at is None
        ):
            return
        delta = (sample.charge_acquired_at - self.last_sample_at).total_seconds()
        if delta <= 0 or delta > MAX_SAMPLE_GAP_SECONDS:
            return
        power = _finite_float(sample.charge_power_w)
        if power is None:
            return
        self.active_session["energy_estimated_kwh"] = round(
            float(self.active_session.get("energy_estimated_kwh", 0.0))
            + max(power, 0.0) * delta / 3_600_000.0,
            5,
        )

    def update(self, sample: LoggerSample) -> dict[str, Any] | None:
        """Apply one sample and return a start/end event candidate."""
        now = sample.observed_at
        self.last_observed_at = now
        self.last_charge_power_w = _finite_float(sample.charge_power_w)
        self.source = sample.charge_source or self.source
        self.role_context = {
            role: dict(context) for role, context in sample.charge_role_context.items()
        }
        distinct = (
            sample.charge_source_available
            and sample.charge_acquisition_id is not None
            and sample.charge_acquired_at is not None
            and sample.charge_acquisition_id != self.last_acquisition_id
        )
        gap = _seconds_between(sample.charge_acquired_at, self.last_acquired_at)
        if distinct:
            self.last_acquisition_id = sample.charge_acquisition_id
            self.last_acquired_at = sample.charge_acquired_at
        target, reason = self._classify(sample)
        self.reason = reason
        if target == "unknown" or (
            gap is not None and gap > MAX_SAMPLE_GAP_SECONDS
        ):
            self.candidate_state = None
            self.candidate_count = 0
            self.candidate_started_at = None
            self.last_sample_at = None
            if self.active_session is not None:
                self.active_session["interrupted"] = True
                self.active_session["quality"] = "interrupted"
        self.quality = (
            "interrupted"
            if target == "unknown"
            or (
                self.active_session is not None
                and self.active_session.get("interrupted") is True
            )
            else "good"
        )
        if target == "unknown":
            # Unknown supplies neither an energy endpoint nor normal debounce.
            if distinct or not sample.charge_source_available:
                self.unknown_count += 1
            if self.unknown_count >= UNKNOWN_DEBOUNCE_SAMPLES:
                self.state = "unknown"
            return None
        self.unknown_count = 0
        if not distinct:
            return None
        self._integrate(sample)
        self.last_sample_at = sample.charge_acquired_at
        if target != self.candidate_state:
            self.candidate_state = target
            self.candidate_count = 1
            self.candidate_started_at = sample.charge_acquired_at
        else:
            self.candidate_count += 1
        required = (
            CHARGE_START_DEBOUNCE_SAMPLES
            if target == "charging"
            else CHARGE_STOP_DEBOUNCE_SAMPLES
        )
        candidate_age = _seconds_between(
            sample.charge_acquired_at, self.candidate_started_at
        ) or 0.0
        stop_dwell_met = (
            target != "not_charging"
            or candidate_age >= CHARGE_STOP_MIN_DWELL_SECONDS
        )
        if self.candidate_count < required or not stop_dwell_met or target == self.state:
            return None

        previous = self.state
        self.state = target
        if target == "charging":
            self.quality = (
                "interrupted"
                if self.active_session and self.active_session.get("interrupted")
                else "good"
            )
            if self.active_session is None:
                self.session_count += 1
                self.active_session = {
                    "session_id": f"charge-{uuid4().hex}",
                    "started_at": _iso(now),
                    "start_observed_at": _iso(now),
                    "ended_at": None,
                    "end_observed_at": None,
                    "duration_seconds": None,
                    "initial_charge_power_w": self.last_charge_power_w,
                    "final_charge_power_w": None,
                    "energy_estimated_kwh": 0.0,
                    "source": self.source,
                    "quality": self.quality,
                    "interrupted": False,
                    "end_reason": None,
                }
                return {
                    "event_type": EVENT_CHARGE_SESSION_STARTED,
                    "from": previous,
                    "to": target,
                    "reason": reason,
                    "session": dict(self.active_session),
                }
        elif target == "not_charging" and self.active_session is not None:
            session = self.active_session
            session["ended_at"] = _iso(now)
            session["end_observed_at"] = _iso(now)
            session["duration_seconds"] = round(
                _seconds_between(now, _datetime(session.get("started_at"))) or 0.0,
                3,
            )
            session["final_charge_power_w"] = self.last_charge_power_w
            session["end_reason"] = reason
            session["quality"] = (
                "interrupted" if session.get("interrupted") else "complete"
            )
            self.last_session = dict(session)
            self.completed_sessions.append(dict(session))
            retention_start = now - SESSION_RETENTION
            retained: list[dict[str, Any]] = []
            for completed in self.completed_sessions:
                ended_at = _datetime(completed.get("ended_at"))
                if ended_at is not None and ended_at >= retention_start:
                    retained.append(completed)
            self.completed_sessions = retained[-MAX_COMPLETED_SESSIONS:]
            self.active_session = None
            self.quality = str(session["quality"])
            return {
                "event_type": EVENT_CHARGE_SESSION_ENDED,
                "from": previous,
                "to": target,
                "reason": reason,
                "session": dict(session),
            }
        return None

    def snapshot(self, now: datetime) -> dict[str, Any]:
        """Return an HA-friendly view of charging state."""
        active = dict(self.active_session) if self.active_session else None
        if active is not None:
            active["duration_seconds"] = round(
                _seconds_between(now, _datetime(active.get("started_at"))) or 0.0,
                3,
            )
            active["quality"] = (
                "interrupted" if active.get("interrupted") else self.quality
            )
        last = dict(self.last_session) if self.last_session else None
        age = _seconds_between(now, self.last_acquired_at)
        return {
            "State": self.state,
            "Active": self.state == "charging" and active is not None,
            "Quality": self.quality,
            "Source": self.source,
            "Provenance": "measured_power_inferred_session",
            "Confidence": "high" if self.quality in {"good", "complete"} else "interrupted",
            "Reason": self.reason,
            "ChargePowerW": self.last_charge_power_w,
            "SampleAgeSeconds": round(age, 3) if age is not None else None,
            "AcquisitionId": self.last_acquisition_id,
            "AcquiredAt": _iso(self.last_acquired_at),
            "RoleContext": {
                role: dict(context) for role, context in self.role_context.items()
            },
            "ActiveSession": active,
            "LastSession": last,
            "SessionCount": self.session_count,
            "RetainedSessionCount": len(self.completed_sessions),
            "Interrupted": bool(active and active.get("interrupted"))
            or self.quality == "interrupted",
            "Thresholds": {
                "StartPowerW": CHARGE_START_POWER,
                "StopPowerW": CHARGE_STOP_POWER,
                "StartDebounceSamples": CHARGE_START_DEBOUNCE_SAMPLES,
                "StopDebounceSamples": CHARGE_STOP_DEBOUNCE_SAMPLES,
                "StopMinDwellSeconds": CHARGE_STOP_MIN_DWELL_SECONDS,
                "MaxSampleGapSeconds": MAX_SAMPLE_GAP_SECONDS,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize restart-safe state."""
        return {
            "state": self.state,
            "quality": self.quality,
            "reason": self.reason,
            "source": self.source,
            "active_session": self.active_session,
            "last_session": self.last_session,
            "completed_sessions": self.completed_sessions[-MAX_COMPLETED_SESSIONS:],
            "session_count": self.session_count,
            "last_observed_at": _iso(self.last_observed_at),
            "last_sample_at": _iso(self.last_sample_at),
            "last_acquisition_id": self.last_acquisition_id,
            "last_acquired_at": _iso(self.last_acquired_at),
            "role_context": {
                role: dict(context) for role, context in self.role_context.items()
            },
            "last_charge_power_w": self.last_charge_power_w,
        }

    def restore(self, raw: Any) -> None:
        """Restore validated state without emitting a synthetic event."""
        if not isinstance(raw, dict):
            return
        self.state = str(raw.get("state") or "unknown")
        self.quality = str(raw.get("quality") or "unknown")
        self.reason = str(raw.get("reason") or "restored")
        self.source = str(raw.get("source") or "unknown")
        self.candidate_state = None
        self.candidate_count = 0
        self.candidate_started_at = None
        self.unknown_count = 0
        self.active_session = (
            dict(raw["active_session"])
            if isinstance(raw.get("active_session"), dict)
            else None
        )
        self.last_session = (
            dict(raw["last_session"])
            if isinstance(raw.get("last_session"), dict)
            else None
        )
        completed = raw.get("completed_sessions")
        restored_completed = (
            [dict(item) for item in completed if isinstance(item, dict)]
            if isinstance(completed, list)
            else []
        )
        retention_start = datetime.now(UTC) - SESSION_RETENTION
        self.completed_sessions = [
            item
            for item in restored_completed
            if (
                (ended_at := _datetime(item.get("ended_at"))) is not None
                and ended_at >= retention_start
            )
        ][-MAX_COMPLETED_SESSIONS:]
        self.session_count = _nonnegative_int(raw.get("session_count"))
        self.last_observed_at = _datetime(raw.get("last_observed_at"))
        self.last_sample_at = None
        self.last_acquisition_id = raw.get("last_acquisition_id")
        self.last_acquired_at = _datetime(raw.get("last_acquired_at"))
        context = raw.get("role_context")
        self.role_context = (
            {
                role: dict(value)
                for role, value in context.items()
                if isinstance(value, dict)
            }
            if isinstance(context, dict)
            else {}
        )
        self.last_charge_power_w = _finite_float(raw.get("last_charge_power_w"))
        if self.active_session is not None:
            # A restart is an unknown gap by definition. Keep the session open,
            # but preserve its interrupted quality until it ends.
            self.active_session["interrupted"] = True
            self.active_session["quality"] = "interrupted"
            self.quality = "interrupted"


class EG4TelemetryLogger:
    """Correlate generator and charge samples, events, and persistence state."""

    def __init__(self, entry_id: str, source_serial: str | None = None) -> None:
        self.entry_id = entry_id
        self.source_serial = source_serial
        self.generator = GeneratorStateMachine()
        self.charging = ChargingSessionMachine()
        self.recent_events: list[dict[str, Any]] = []
        self.last_sequence: int | None = None
        self.last_observed_at: datetime | None = None

    def process(self, sample: LoggerSample) -> list[dict[str, Any]]:
        """Process a correlated sample and return accepted HA events."""
        self.last_sequence = sample.sequence
        self.last_observed_at = sample.observed_at
        candidates: list[dict[str, Any] | None] = []
        if sample.process_generator:
            candidates.append(self.generator.update(sample))
            candidates.append(self.generator.update_transfer(sample))
        if sample.process_charge:
            candidates.append(self.charging.update(sample))
        events: list[dict[str, Any]] = []
        for candidate in candidates:
            if candidate is None:
                continue
            session_evidence = candidate.get("session")
            session_quality = (
                session_evidence.get("quality")
                if isinstance(session_evidence, dict)
                else None
            )
            event = {
                "event_id": f"eg4-{uuid4().hex}",
                "event_type": candidate["event_type"],
                "entry_id": self.entry_id,
                "source_serial": (
                    sample.source_serial
                    if candidate["event_type"]
                    in {
                        EVENT_GENERATOR_STATE_CHANGED,
                        EVENT_GENERATOR_TRANSFER_CHANGED,
                    }
                    else sample.charge_source_serial or sample.source_serial or self.source_serial
                ),
                "source": (
                    "gridboss_direct"
                    if candidate["event_type"]
                    in {
                        EVENT_GENERATOR_STATE_CHANGED,
                        EVENT_GENERATOR_TRANSFER_CHANGED,
                    }
                    else sample.charge_source or "unknown"
                ),
                "provenance": (
                    "inferred_gridboss_terminal_observation"
                    if candidate["event_type"]
                    in {
                        EVENT_GENERATOR_STATE_CHANGED,
                        EVENT_GENERATOR_TRANSFER_CHANGED,
                    }
                    else "inferred_from_battery_charge_power"
                ),
                "confidence": (
                    "medium"
                    if candidate["event_type"] == EVENT_GENERATOR_TRANSFER_CHANGED
                    else "unknown"
                    if candidate.get("to") == "unknown"
                    else "low"
                    if session_quality not in {None, "good", "complete"}
                    else "high"
                ),
                "observed_at": _iso(sample.observed_at),
                "received_at": _iso(datetime.now(UTC)),
                "sequence": sample.sequence,
                "generator_acquisition_id": sample.generator_acquisition_id,
                "generator_acquired_at": _iso(sample.generator_acquired_at),
                "charge_acquisition_id": sample.charge_acquisition_id,
                "charge_acquired_at": _iso(sample.charge_acquired_at),
                "charge_role_context": {
                    role: dict(context)
                    for role, context in sample.charge_role_context.items()
                },
                "generator_voltage": _finite_float(sample.generator_voltage),
                "generator_frequency": _finite_float(sample.generator_frequency),
                "generator_power": _finite_float(sample.generator_power),
                "generator_power_w": _finite_float(sample.generator_power),
                "transfer_state": sample.transfer_state,
                "charge_power_w": _finite_float(sample.charge_power_w),
                "battery_voltage": _finite_float(sample.battery_voltage),
                "battery_soc": _finite_float(sample.battery_soc),
                **candidate,
            }
            if candidate["event_type"] == EVENT_GENERATOR_STATE_CHANGED:
                self.generator.last_transition_event_id = event["event_id"]
            events.append(event)
            self.recent_events.append(event)
        self.recent_events = self.recent_events[-MAX_RECENT_EVENTS:]
        return events

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        """Return the current logger state."""
        now = now or datetime.now(UTC)
        return {
            "SchemaVersion": LOGGER_SCHEMA_VERSION,
            "UpdatedAt": _iso(now),
            "LastObservedAt": _iso(self.last_observed_at),
            "LastSequence": self.last_sequence,
            "Generator": self.generator.snapshot(now),
            "Charging": self.charging.snapshot(now),
            "RecentEvents": list(self.recent_events),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the versioned Store payload."""
        return {
            "schema_version": LOGGER_SCHEMA_VERSION,
            "entry_id": self.entry_id,
            "source_serial": self.source_serial,
            "last_sequence": self.last_sequence,
            "last_observed_at": _iso(self.last_observed_at),
            "generator": self.generator.to_dict(),
            "charging": self.charging.to_dict(),
            "recent_events": self.recent_events[-MAX_RECENT_EVENTS:],
        }

    def restore(self, raw: Any) -> bool:
        """Restore state if schema and entry identity are compatible."""
        if not isinstance(raw, dict):
            return False
        if raw.get("schema_version") != LOGGER_SCHEMA_VERSION:
            return False
        stored_entry = raw.get("entry_id")
        if stored_entry is not None and str(stored_entry) != self.entry_id:
            return False
        self.source_serial = raw.get("source_serial") or self.source_serial
        sequence = raw.get("last_sequence")
        self.last_sequence = int(sequence) if isinstance(sequence, int) else None
        self.last_observed_at = _datetime(raw.get("last_observed_at"))
        self.generator.restore(raw.get("generator"))
        self.charging.restore(raw.get("charging"))
        recent = raw.get("recent_events")
        self.recent_events = (
            [dict(item) for item in recent if isinstance(item, dict)][-MAX_RECENT_EVENTS:]
            if isinstance(recent, list)
            else []
        )
        return True


__all__ = [
    "CHARGE_START_DEBOUNCE_SAMPLES",
    "CHARGE_START_POWER",
    "CHARGE_STOP_DEBOUNCE_SAMPLES",
    "CHARGE_STOP_MIN_DWELL_SECONDS",
    "CHARGE_STOP_POWER",
    "EVENT_CHARGE_SESSION_ENDED",
    "EVENT_CHARGE_SESSION_STARTED",
    "EVENT_GENERATOR_STATE_CHANGED",
    "EVENT_GENERATOR_TRANSFER_CHANGED",
    "EG4TelemetryLogger",
    "GENERATOR_OFF_FREQUENCY",
    "GENERATOR_OFF_POWER",
    "GENERATOR_OFF_VOLTAGE",
    "GENERATOR_ON_FREQUENCY",
    "GENERATOR_ON_POWER",
    "GENERATOR_ON_VOLTAGE",
    "LoggerSample",
]
