"""Persist protection evidence without promoting rejected core data to telemetry."""
from __future__ import annotations

import json

import pytest

from custom_components.eg4_monitor.local_dongle_trace import (
    ControllerObservationTracker,
    LocalDongleTraceWriter,
)


def _protection_record(word: int, identity: str) -> dict:
    """Represent a structurally valid reply with an invalid inverter status."""
    values = [0] * 40
    values[0], values[6] = 33280, word
    return {
        "kind": "acquisition", "session_id": "synthetic-protection-session",
        "role": "flexboss_master", "acquisition_id": identity, "ok": False,
        "sample_started_at": "2026-11-09T12:00:00+00:00",
        "sample_completed_at": "2026-11-09T12:00:01+00:00",
        "primary_blocks": [{
            "function": 4, "register": 0, "count": 40, "ok": True,
            "semantic": "rejected", "values": values,
            "transaction_id": identity + ":core", "acquisition_id": identity,
            "started_at": "2026-11-09T12:00:00+00:00",
            "completed_at": "2026-11-09T12:00:01+00:00",
        }],
        "diagnostics": [],
    }


@pytest.mark.asyncio
async def test_protection_low_bit_change_survives_semantic_rejection_to_disk(tmp_path):
    tracker = ControllerObservationTracker()
    writer = LocalDongleTraceWriter(tmp_path)
    try:
        baseline = tracker.observe(_protection_record(0x4000, "synthetic-core-a"))
        assert baseline is not None
        assert await writer.async_write(baseline) == writer.active_path
        changed = tracker.observe(_protection_record(0x4001, "synthetic-core-b"))
        assert changed is not None, "input6 bit0-only protection evidence was suppressed"
        assert changed["changed_fields"] == ["input.6.bits_0_1"]
        assert await writer.async_write(changed) == writer.active_path
    finally:
        await writer.async_close()
    saved = [json.loads(line) for line in writer.active_path.read_text().splitlines()]
    assert [row["controller"]["input"]["6"] for row in saved] == [0x4000, 0x4001]
    assert all(row["availability"]["primary"] is False for row in saved)
    assert saved[1]["primary_blocks"][0]["semantic"] == "rejected"
