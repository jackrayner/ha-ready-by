"""Persistent storage for learned sessions, settings and schedule state."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    BALANCE_BAND_DEGREES,
    DEFAULT_TARGET,
    DOMAIN,
    MAX_COOLING_SAMPLES,
    MAX_SESSIONS,
    PHASE_IDLE,
    STORAGE_VERSION,
)
from .model import CoolingSample, Session

SAVE_DELAY_SECONDS = 10


@dataclass
class ReadyByData:
    sessions: list[Session] = field(default_factory=list)
    cooling: list[CoolingSample] = field(default_factory=list)
    enabled: bool = False
    ready_by: datetime | None = None
    target: float = DEFAULT_TARGET
    phase: str = PHASE_IDLE
    started_at: datetime | None = None
    history_imported: bool = False
    sources_key: str | None = None
    daily_enabled: bool = False
    daily_time: time | None = None
    daily_off_at_ready_by: bool = False
    daily_target: float | None = None
    session_hours: float | None = None
    daily_done_until: datetime | None = None
    active: str | None = None
    active_ready_by: datetime | None = None
    predicted_ready: datetime | None = None
    reached_at: datetime | None = None
    last_run: dict[str, Any] | None = None

    def add_sessions(self, sessions: list[Session]) -> int:
        """Add sessions not already known (by start time); return how many were added."""
        known = {s.start for s in self.sessions}
        new = [s for s in sessions if s.start not in known]
        self.sessions = _balanced(self.sessions + new, MAX_SESSIONS)
        return len(new)

    def add_cooling(self, samples: list[CoolingSample]) -> int:
        known = {s.start for s in self.cooling}
        new = [s for s in samples if s.start not in known]
        self.cooling = _balanced(self.cooling + new, MAX_COOLING_SAMPLES)
        return len(new)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessions": [s.as_dict() for s in self.sessions],
            "cooling": [s.as_dict() for s in self.cooling],
            "enabled": self.enabled,
            "ready_by": self.ready_by.isoformat() if self.ready_by else None,
            "target": self.target,
            "phase": self.phase,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "history_imported": self.history_imported,
            "sources_key": self.sources_key,
            "daily_enabled": self.daily_enabled,
            "daily_time": self.daily_time.isoformat() if self.daily_time else None,
            "daily_off_at_ready_by": self.daily_off_at_ready_by,
            "daily_target": self.daily_target,
            "session_hours": self.session_hours,
            "daily_done_until": self.daily_done_until.isoformat()
            if self.daily_done_until
            else None,
            "active": self.active,
            "active_ready_by": self.active_ready_by.isoformat() if self.active_ready_by else None,
            "predicted_ready": self.predicted_ready.isoformat() if self.predicted_ready else None,
            "reached_at": self.reached_at.isoformat() if self.reached_at else None,
            "last_run": self.last_run,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ReadyByData:
        return cls(
            sessions=[Session.from_dict(s) for s in raw.get("sessions", [])],
            cooling=[CoolingSample.from_dict(s) for s in raw.get("cooling", [])],
            enabled=bool(raw.get("enabled", False)),
            ready_by=dt_util.parse_datetime(raw["ready_by"]) if raw.get("ready_by") else None,
            target=float(raw.get("target", DEFAULT_TARGET)),
            phase=raw.get("phase", PHASE_IDLE),
            started_at=(
                dt_util.parse_datetime(raw["started_at"]) if raw.get("started_at") else None
            ),
            history_imported=bool(raw.get("history_imported", False)),
            sources_key=raw.get("sources_key"),
            daily_enabled=bool(raw.get("daily_enabled", False)),
            daily_time=dt_util.parse_time(raw["daily_time"]) if raw.get("daily_time") else None,
            daily_off_at_ready_by=bool(raw.get("daily_off_at_ready_by", False)),
            daily_target=(
                float(raw["daily_target"]) if raw.get("daily_target") is not None else None
            ),
            daily_done_until=(
                dt_util.parse_datetime(raw["daily_done_until"])
                if raw.get("daily_done_until")
                else None
            ),
            session_hours=(
                float(raw["session_hours"]) if raw.get("session_hours") is not None else None
            ),
            active=raw.get("active"),
            active_ready_by=(
                dt_util.parse_datetime(raw["active_ready_by"])
                if raw.get("active_ready_by")
                else None
            ),
            predicted_ready=(
                dt_util.parse_datetime(raw["predicted_ready"])
                if raw.get("predicted_ready")
                else None
            ),
            reached_at=dt_util.parse_datetime(raw["reached_at"]) if raw.get("reached_at") else None,
            last_run=raw.get("last_run"),
        )


def _balanced[T: (Session, CoolingSample)](samples: list[T], cap: int) -> list[T]:
    """Sort by start; over the cap, drop the oldest from the busiest outside-temperature band.

    Plain oldest-first trimming would let a long mild spell push out every
    cold-weather sample; this keeps the whole range of conditions represented.
    """
    samples = sorted(samples, key=lambda s: s.start)
    excess = len(samples) - cap
    if excess <= 0:
        return samples
    bands: dict[int, list[T]] = {}
    for sample in samples:
        bands.setdefault(math.floor(sample.outside_avg / BALANCE_BAND_DEGREES), []).append(sample)
    dropped: set[int] = set()
    for _ in range(excess):
        busiest = max(bands.values(), key=len)
        dropped.add(id(busiest.pop(0)))
    return [s for s in samples if id(s) not in dropped]


class ReadyByStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self.data = ReadyByData()

    async def async_load(self) -> ReadyByData:
        raw = await self._store.async_load()
        self.data = ReadyByData.from_dict(raw) if raw else ReadyByData()
        return self.data

    def async_schedule_save(self) -> None:
        self._store.async_delay_save(self.data.as_dict, SAVE_DELAY_SECONDS)

    async def async_save(self) -> None:
        await self._store.async_save(self.data.as_dict())

    async def async_remove(self) -> None:
        await self._store.async_remove()
