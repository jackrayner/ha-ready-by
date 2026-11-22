"""Heating model, session detection and planning.

Pure Python with no Home Assistant imports, so it can be unit-tested directly and
loaded by scripts/influx_backfill.py outside Home Assistant.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

MIN_SESSION_DURATION = timedelta(minutes=30)
MIN_SESSION_RISE = 1.0
MIN_IDLE_FOR_COOLING = timedelta(hours=2)
STOP_MERGE_GAP = timedelta(minutes=10)
MIN_COOLING_DROP = 0.2
MIN_RATE = 0.1
MAX_RATE_HEADROOM = 1.2
DEFAULT_COOLING_K = 0.015
SIMPLE_FIT_MIN_SESSIONS = 3
FULL_FIT_MIN_SESSIONS = 8
RIDGE_LAMBDA = 0.1
OUTLIER_MADS = 3.0
SIM_STEP = timedelta(minutes=5)
MAX_HEAT_DURATION = timedelta(hours=72)
PLAN_RESOLUTION = timedelta(minutes=1)

OutsideTemp = Callable[[datetime], float]


@dataclass(frozen=True)
class Session:
    """One completed heating run."""

    start: datetime
    end: datetime
    water_start: float
    water_end: float
    outside_avg: float

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600

    @property
    def rate(self) -> float:
        return (self.water_end - self.water_start) / self.hours

    @property
    def water_mid(self) -> float:
        return (self.water_start + self.water_end) / 2

    def is_valid(self) -> bool:
        return (
            self.end - self.start >= MIN_SESSION_DURATION
            and self.water_end - self.water_start >= MIN_SESSION_RISE
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "water_start": self.water_start,
            "water_end": self.water_end,
            "outside_avg": self.outside_avg,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            start=_parse_dt(data["start"]),
            end=_parse_dt(data["end"]),
            water_start=float(data["water_start"]),
            water_end=float(data["water_end"]),
            outside_avg=float(data["outside_avg"]),
        )


@dataclass(frozen=True)
class CoolingSample:
    """One idle period, used to learn how fast the water loses heat."""

    start: datetime
    end: datetime
    water_start: float
    water_end: float
    outside_avg: float

    @property
    def k(self) -> float | None:
        """Newton's-law coefficient per hour, or None if the sample can't give one."""
        hours = (self.end - self.start).total_seconds() / 3600
        above_start = self.water_start - self.outside_avg
        above_end = self.water_end - self.outside_avg
        if hours <= 0 or above_end <= 0 or above_start <= above_end:
            return None
        return math.log(above_start / above_end) / hours

    def is_valid(self) -> bool:
        return (
            self.end - self.start >= MIN_IDLE_FOR_COOLING
            and self.water_start - self.water_end >= MIN_COOLING_DROP
            and self.k is not None
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "water_start": self.water_start,
            "water_end": self.water_end,
            "outside_avg": self.outside_avg,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoolingSample:
        return cls(
            start=_parse_dt(data["start"]),
            end=_parse_dt(data["end"]),
            water_start=float(data["water_start"]),
            water_end=float(data["water_end"]),
            outside_avg=float(data["outside_avg"]),
        )


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class _TimeAverage:
    """Time-weighted average of a stepwise signal (each value holds until the next)."""

    def __init__(self, start: datetime, value: float | None) -> None:
        self._last_time = start
        self._last_value = value
        self._weighted = 0.0
        self._seconds = 0.0

    def add(self, when: datetime, value: float | None) -> None:
        self._accumulate(when)
        self._last_value = value

    def average(self, end: datetime) -> float | None:
        self._accumulate(end)
        if self._seconds > 0:
            return self._weighted / self._seconds
        return self._last_value

    def _accumulate(self, when: datetime) -> None:
        seconds = (when - self._last_time).total_seconds()
        if seconds > 0 and self._last_value is not None:
            self._weighted += self._last_value * seconds
            self._seconds += seconds
        self._last_time = max(self._last_time, when)


Sample = Session | CoolingSample


class SessionDetector:
    """State machine turning heating/water/outside updates into sessions.

    Feed it time-ordered updates; each update returns any samples it completed.
    A heating stop is held for STOP_MERGE_GAP before it ends the session, so a
    brief pause (a defrost cycle, a flickering power reading) doesn't split one
    heating run into fragments too short to learn from. Call flush() as time
    passes, or at the end of a history, to release a held stop.

    A session ends when the water first reached its highest temperature, not
    when the heater stopped: a heater holding its setpoint isn't warming the
    water, and counting that time would understate the heating rate.
    """

    def __init__(self) -> None:
        self.heating: bool | None = None
        self.water: float | None = None
        self.outside: float | None = None
        self._phase_start: datetime | None = None
        self._phase_water: float | None = None
        self._outside_avg: _TimeAverage | None = None
        self._pending_stop: tuple[datetime, float | None] | None = None
        self._peak: tuple[datetime, float] | None = None

    def update_water(self, when: datetime, value: float | None) -> list[Sample]:
        done = self.flush(when)
        self.water = value
        if self.heating is not None and self._phase_start is not None:
            if self._phase_water is None and value is not None:
                # The phase began before the first water reading; start it now.
                self._start_phase(when)
            elif self.heating and value is not None and self._pending_stop is None:
                if self._peak is None or value > self._peak[1]:
                    self._peak = (when, value)
        return done

    def update_outside(self, when: datetime, value: float | None) -> list[Sample]:
        done = self.flush(when)
        self.outside = value
        if self._outside_avg is not None:
            self._outside_avg.add(when, value)
        return done

    def update_heating(self, when: datetime, heating: bool) -> list[Sample]:
        done = self.flush(when)
        if heating == self.heating:
            return done
        if self.heating and not heating:
            self._pending_stop = (when, self.water)
            self.heating = False
            return done
        if heating and self._pending_stop is not None:
            self._pending_stop = None
            self.heating = True
            return done
        if self.heating is not None:
            done.extend(self._finish_phase(when, was_heating=self.heating))
        self.heating = heating
        self._start_phase(when)
        return done

    def flush(self, now: datetime) -> list[Sample]:
        """End a held heating stop once it has lasted longer than STOP_MERGE_GAP."""
        if self._pending_stop is None or now - self._pending_stop[0] <= STOP_MERGE_GAP:
            return []
        stopped_at, water = self._pending_stop
        self._pending_stop = None
        done = self._finish_phase(stopped_at, was_heating=True, water_end=water)
        self._start_phase(stopped_at, water=water)
        return done

    def _start_phase(self, when: datetime, water: float | None = None) -> None:
        self._phase_start = when
        self._phase_water = self.water if water is None else water
        self._outside_avg = _TimeAverage(when, self.outside)
        self._peak = None

    def _finish_phase(
        self, when: datetime, *, was_heating: bool, water_end: float | None = None
    ) -> list[Sample]:
        water_end = self.water if water_end is None else water_end
        if self._phase_start is None or self._phase_water is None or water_end is None:
            return []
        if was_heating and self._peak is not None and self._peak[1] >= water_end:
            when, water_end = self._peak
        outside_avg = self._outside_avg.average(when) if self._outside_avg else None
        if outside_avg is None:
            return []
        cls = Session if was_heating else CoolingSample
        sample = cls(
            start=self._phase_start,
            end=when,
            water_start=self._phase_water,
            water_end=water_end,
            outside_avg=outside_avg,
        )
        return [sample] if sample.is_valid() else []


def detect(
    events: Iterable[tuple[datetime, str, Any]],
) -> tuple[list[Session], list[CoolingSample]]:
    """Run time-ordered (when, kind, value) events through a SessionDetector.

    kind is "heating" (bool), "water" (float or None) or "outside" (float or None).
    A heating stop still held at the end of the events is treated as final.
    """
    detector = SessionDetector()
    done: list[Sample] = []
    last: datetime | None = None
    for when, kind, value in sorted(events, key=lambda e: e[0]):
        last = when
        if kind == "water":
            done.extend(detector.update_water(when, value))
        elif kind == "outside":
            done.extend(detector.update_outside(when, value))
        elif kind == "heating":
            done.extend(detector.update_heating(when, bool(value)))
    if last is not None:
        done.extend(detector.flush(last + STOP_MERGE_GAP * 2))
    sessions = [s for s in done if isinstance(s, Session)]
    cooling = [s for s in done if isinstance(s, CoolingSample)]
    return sessions, cooling


@dataclass(frozen=True)
class HeatingModel:
    """Predicted heating rate (°C/h): c0 + c1 * outside + c2 * water, clamped."""

    kind: str
    c0: float
    c1: float = 0.0
    c2: float = 0.0
    n: int = 0
    r2: float | None = None
    max_rate: float | None = None

    def rate(self, outside: float, water: float) -> float:
        raw = self.c0 + self.c1 * outside + self.c2 * water
        upper = self.max_rate * MAX_RATE_HEADROOM if self.max_rate else None
        if upper is not None:
            raw = min(raw, upper)
        return max(raw, MIN_RATE)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "c0": self.c0,
            "c1": self.c1,
            "c2": self.c2,
            "n": self.n,
            "r2": self.r2,
            "max_rate": self.max_rate,
        }


def fit_heating(sessions: Iterable[Session], seed_rate: float) -> HeatingModel:
    """Fit the heating model, falling back to simpler forms when data is thin.

    Fewer than SIMPLE_FIT_MIN_SESSIONS: the seed rate. Up to FULL_FIT_MIN_SESSIONS,
    or when the full fit gives physically implausible signs: a one-variable fit on
    (outside - water). Otherwise the two-variable fit.
    """
    valid = [s for s in sessions if s.is_valid()]
    if len(valid) < SIMPLE_FIT_MIN_SESSIONS:
        return HeatingModel(kind="seed", c0=seed_rate, n=len(valid))

    kept = _reject_outliers(valid)
    if len(kept) >= FULL_FIT_MIN_SESSIONS:
        full = _fit_full(kept)
        if full is not None:
            return full
    return _fit_simple(kept)


def _fit_simple(sessions: list[Session]) -> HeatingModel:
    xs = [s.outside_avg - s.water_mid for s in sessions]
    ys = [s.rate for s in sessions]
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    sxx = sum((x - x_mean) ** 2 for x in xs)
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
    slope = max(sxy / (sxx + RIDGE_LAMBDA), 0.0)
    intercept = y_mean - slope * x_mean
    predicted = [intercept + slope * x for x in xs]
    return HeatingModel(
        kind="simple",
        c0=intercept,
        c1=slope,
        c2=-slope,
        n=len(sessions),
        r2=_r2(ys, predicted),
        max_rate=max(ys),
    )


def _fit_full(sessions: list[Session]) -> HeatingModel | None:
    x1 = [s.outside_avg for s in sessions]
    x2 = [s.water_mid for s in sessions]
    ys = [s.rate for s in sessions]
    m1, m2, my = statistics.fmean(x1), statistics.fmean(x2), statistics.fmean(ys)
    d1 = [v - m1 for v in x1]
    d2 = [v - m2 for v in x2]
    dy = [v - my for v in ys]
    a11 = sum(v * v for v in d1) + RIDGE_LAMBDA
    a22 = sum(v * v for v in d2) + RIDGE_LAMBDA
    a12 = sum(a * b for a, b in zip(d1, d2, strict=True))
    b1 = sum(a * b for a, b in zip(d1, dy, strict=True))
    b2 = sum(a * b for a, b in zip(d2, dy, strict=True))
    det = a11 * a22 - a12 * a12
    if abs(det) < 1e-9:
        return None
    c1 = (b1 * a22 - b2 * a12) / det
    c2 = (a11 * b2 - a12 * b1) / det
    # Warmer air should speed heating and hotter water should slow it.
    if c1 < 0 or c2 > 0:
        return None
    c0 = my - c1 * m1 - c2 * m2
    predicted = [c0 + c1 * a + c2 * b for a, b in zip(x1, x2, strict=True)]
    return HeatingModel(
        kind="full",
        c0=c0,
        c1=c1,
        c2=c2,
        n=len(sessions),
        r2=_r2(ys, predicted),
        max_rate=max(ys),
    )


def _reject_outliers(sessions: list[Session]) -> list[Session]:
    first = _fit_simple(sessions)
    residuals = [s.rate - first.rate(s.outside_avg, s.water_mid) for s in sessions]
    median = statistics.median(residuals)
    mad = statistics.median(abs(r - median) for r in residuals)
    if mad == 0:
        return sessions
    limit = OUTLIER_MADS * 1.4826 * mad
    kept = [s for s, r in zip(sessions, residuals, strict=True) if abs(r - median) <= limit]
    return kept if len(kept) >= SIMPLE_FIT_MIN_SESSIONS else sessions


def _r2(actual: list[float], predicted: list[float]) -> float | None:
    mean = statistics.fmean(actual)
    total = sum((a - mean) ** 2 for a in actual)
    if total == 0:
        return None
    residual = sum((a - p) ** 2 for a, p in zip(actual, predicted, strict=True))
    return 1 - residual / total


def fit_cooling(samples: Iterable[CoolingSample]) -> tuple[float, int]:
    """Median cooling coefficient per hour and the number of samples behind it."""
    ks = [k for s in samples if s.is_valid() and (k := s.k) is not None]
    if not ks:
        return DEFAULT_COOLING_K, 0
    return statistics.median(ks), len(ks)


def outside_temp_function(points: Iterable[tuple[datetime, float]], fallback: float) -> OutsideTemp:
    """Linear interpolation through (time, temperature) points, flat beyond the ends."""
    ordered = sorted(points, key=lambda p: p[0])
    if not ordered:
        return lambda _when: fallback
    times = [p[0] for p in ordered]
    temps = [p[1] for p in ordered]

    def at(when: datetime) -> float:
        i = bisect.bisect_left(times, when)
        if i == 0:
            return temps[0]
        if i == len(times):
            return temps[-1]
        t0, t1 = times[i - 1], times[i]
        frac = (when - t0) / (t1 - t0)
        return temps[i - 1] + frac * (temps[i] - temps[i - 1])

    return at


def simulate_cooling(
    start: datetime, end: datetime, water: float, outside_at: OutsideTemp, k: float
) -> float:
    """Water temperature at end, cooling from start with Newton's law."""
    when = start
    while when < end:
        step = min(SIM_STEP, end - when)
        hours = step.total_seconds() / 3600
        outside = outside_at(when + step / 2)
        if water > outside:
            water = outside + (water - outside) * math.exp(-k * hours)
        when += step
    return water


def simulate_heating(
    start: datetime,
    water: float,
    target: float,
    outside_at: OutsideTemp,
    model: HeatingModel,
    limit: timedelta = MAX_HEAT_DURATION,
) -> datetime | None:
    """When the water reaches target heating from start, or None if not within limit."""
    if water >= target:
        return start
    when = start
    end = start + limit
    while when < end:
        hours = SIM_STEP.total_seconds() / 3600
        rate = model.rate(outside_at(when + SIM_STEP / 2), water)
        gain = rate * hours
        if water + gain >= target:
            return when + SIM_STEP * ((target - water) / gain)
        water += gain
        when += SIM_STEP
    return None


@dataclass(frozen=True)
class Plan:
    """When to start heating so the water is at target by the deadline."""

    start: datetime
    expected_ready: datetime | None
    water_at_start: float
    on_time: bool

    @property
    def duration(self) -> timedelta | None:
        if self.expected_ready is None:
            return None
        return self.expected_ready - self.start

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "expected_ready": self.expected_ready.isoformat() if self.expected_ready else None,
            "water_at_start": self.water_at_start,
            "on_time": self.on_time,
        }


def plan_heating(
    now: datetime,
    deadline: datetime,
    water: float,
    target: float,
    outside_at: OutsideTemp,
    model: HeatingModel,
    cooling_k: float,
) -> Plan:
    """Find the latest start that still reaches target by deadline.

    Finish time only moves later as the start moves later, so a binary search
    over the start time between now and the deadline finds it.
    """

    def finish(start: datetime) -> tuple[datetime | None, float]:
        water_at_start = simulate_cooling(now, start, water, outside_at, cooling_k)
        return simulate_heating(start, water_at_start, target, outside_at, model), water_at_start

    ready_now, water_now = finish(now)
    if deadline <= now or ready_now is None or ready_now > deadline:
        return Plan(start=now, expected_ready=ready_now, water_at_start=water_now, on_time=False)

    low, high = now, deadline
    best = (now, ready_now, water_now)
    while high - low > PLAN_RESOLUTION:
        mid = low + (high - low) / 2
        ready, water_at_mid = finish(mid)
        if ready is not None and ready <= deadline:
            low = mid
            best = (mid, ready, water_at_mid)
        else:
            high = mid
    start, ready, water_at_start = best
    return Plan(start=start, expected_ready=ready, water_at_start=water_at_start, on_time=True)
