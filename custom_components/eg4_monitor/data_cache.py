"""Bounded last-good payload caching for EG4 polling."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class CachedPayload:
    """A payload returned from the cache with its age at read time."""

    value: dict[str, Any]
    fetched_at: datetime
    age_seconds: float


@dataclass(frozen=True)
class _CacheEntry:
    """Internal cached payload and fetch timestamp."""

    value: dict[str, Any]
    fetched_at: datetime


def _elapsed_seconds(now: datetime, then: datetime) -> float:
    """Return elapsed seconds while tolerating naive/aware timestamps."""
    if now.tzinfo is None and then.tzinfo is not None:
        now = now.replace(tzinfo=then.tzinfo)
    elif now.tzinfo is not None and then.tzinfo is None:
        then = then.replace(tzinfo=now.tzinfo)
    return max((now - then).total_seconds(), 0.0)


class BoundedPayloadCache:
    """Keep last-good dictionaries for a finite amount of time."""

    def __init__(self, max_age: timedelta) -> None:
        """Initialize the cache."""
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        self._max_age_seconds = max_age.total_seconds()
        self._entries: dict[str, _CacheEntry] = {}

    def put(self, key: str, value: dict[str, Any], fetched_at: datetime) -> None:
        """Store a defensive copy of a successful payload."""
        self._entries[key] = _CacheEntry(deepcopy(value), fetched_at)

    def get(self, key: str, now: datetime) -> CachedPayload | None:
        """Return a fresh-enough cached payload, or expire it."""
        entry = self._entries.get(key)
        if entry is None:
            return None

        age_seconds = _elapsed_seconds(now, entry.fetched_at)
        if age_seconds > self._max_age_seconds:
            self._entries.pop(key, None)
            return None

        return CachedPayload(
            value=deepcopy(entry.value),
            fetched_at=entry.fetched_at,
            age_seconds=age_seconds,
        )

    def clear(self) -> None:
        """Drop all cached payloads."""
        self._entries.clear()
