import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ready_by.model import (
    DEFAULT_COOLING_K,
    MIN_RATE,
    CoolingSample,
    HeatingModel,
    Session,
    detect,
    fit_cooling,
    fit_heating,
    outside_temp_function,
    plan_heating,
    simulate_cooling,
    simulate_heating,
)

T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)
TRUE_C0, TRUE_C1, TRUE_C2 = 2.0, 0.03, -0.035


def _true_rate(outside: float, water: float) -> float:
    return TRUE_C0 + TRUE_C1 * outside + TRUE_C2 * water


def _session(start_offset_h: float, water_start: float, outside: float, noise: float = 0.0):
    rise = 4.0
    rate = _true_rate(outside, water_start + rise / 2) + noise
    start = T0 + timedelta(hours=start_offset_h)
    return Session(
        start=start,
        end=start + timedelta(hours=rise / rate),
        water_start=water_start,
        water_end=water_start + rise,
        outside_avg=outside,
    )


def _synthetic_sessions(n: int, seed: int = 1, noise: float = 0.02) -> list[Session]:
    rng = random.Random(seed)
    return [
        _session(i * 24, rng.uniform(20, 34), rng.uniform(-3, 18), rng.gauss(0, noise))
        for i in range(n)
    ]


def near(a, b, tolerance=timedelta(minutes=1)) -> bool:
    return abs(a - b) <= tolerance


def constant(value: float):
    return lambda _when: value


def test_session_roundtrip_and_properties():
    s = _session(0, 30, 10)
    assert Session.from_dict(s.as_dict()) == s
    assert s.rate == pytest.approx(_true_rate(10, 32))
    assert s.is_valid()


def test_short_or_flat_sessions_are_invalid():
    short = Session(T0, T0 + timedelta(minutes=20), 30, 32, 10)
    flat = Session(T0, T0 + timedelta(hours=2), 30, 30.5, 10)
    assert not short.is_valid()
    assert not flat.is_valid()


def test_seed_model_until_enough_sessions():
    model = fit_heating(_synthetic_sessions(2), seed_rate=1.3)
    assert model.kind == "seed"
    assert model.rate(5, 30) == pytest.approx(1.3)


def test_simple_fit_with_few_sessions():
    model = fit_heating(_synthetic_sessions(5), seed_rate=1.0)
    assert model.kind == "simple"
    assert model.c1 == pytest.approx(-model.c2)
    assert model.c1 >= 0


def test_full_fit_recovers_coefficients():
    model = fit_heating(_synthetic_sessions(40, noise=0.005), seed_rate=1.0)
    assert model.kind == "full"
    assert model.c1 == pytest.approx(TRUE_C1, abs=0.005)
    assert model.c2 == pytest.approx(TRUE_C2, abs=0.005)
    assert model.r2 is not None and model.r2 > 0.95
    assert model.rate(5, 30) == pytest.approx(_true_rate(5, 30), abs=0.03)


def test_outliers_are_rejected():
    sessions = _synthetic_sessions(20, noise=0.005)
    # Lid left off: heating at a third of the normal speed.
    lid_off = _session(999, 25, 10)
    sessions.append(
        Session(
            lid_off.start,
            lid_off.start + (lid_off.end - lid_off.start) * 3,
            lid_off.water_start,
            lid_off.water_end,
            lid_off.outside_avg,
        )
    )
    model = fit_heating(sessions, seed_rate=1.0)
    assert model.n == 20
    assert model.rate(10, 27) == pytest.approx(_true_rate(10, 27), abs=0.03)


def test_implausible_full_fit_falls_back_to_simple():
    # Hotter water heating faster is unphysical; the full fit must be refused.
    sessions = [
        Session(
            T0 + timedelta(days=i),
            T0 + timedelta(days=i, hours=4 / (0.5 + 0.05 * w)),
            w,
            w + 4,
            10.0 + (i % 3),
        )
        for i, w in enumerate(range(20, 36, 2))
    ]
    model = fit_heating(sessions, seed_rate=1.0)
    assert model.kind == "simple"


def test_rate_is_clamped():
    model = HeatingModel(kind="full", c0=-5, c1=0, c2=0, n=10, max_rate=2.0)
    assert model.rate(0, 30) == MIN_RATE
    model = HeatingModel(kind="full", c0=50, c1=0, c2=0, n=10, max_rate=2.0)
    assert model.rate(0, 30) == pytest.approx(2.4)


def test_cooling_k_and_fit():
    k = 0.02
    hours = 8
    sample = CoolingSample(
        T0,
        T0 + timedelta(hours=hours),
        38,
        5 + 33 * math.exp(-k * hours),
        5,
    )
    assert sample.k == pytest.approx(k)
    assert fit_cooling([sample, sample]) == (pytest.approx(k), 2)
    assert fit_cooling([]) == (DEFAULT_COOLING_K, 0)


def test_cooling_sample_rejects_short_or_rising():
    assert not CoolingSample(T0, T0 + timedelta(hours=1), 38, 37, 5).is_valid()
    assert not CoolingSample(T0, T0 + timedelta(hours=5), 30, 31, 5).is_valid()


def test_outside_temp_interpolation():
    f = outside_temp_function([(T0, 0.0), (T0 + timedelta(hours=2), 10.0)], fallback=99)
    assert f(T0 - timedelta(hours=1)) == 0.0
    assert f(T0 + timedelta(hours=1)) == pytest.approx(5.0)
    assert f(T0 + timedelta(hours=5)) == 10.0
    assert outside_temp_function([], fallback=7)(T0) == 7


def test_simulate_heating_constant_rate():
    model = HeatingModel(kind="seed", c0=2.0)
    done = simulate_heating(T0, 30, 38, constant(5), model)
    assert done == T0 + timedelta(hours=4)
    assert simulate_heating(T0, 39, 38, constant(5), model) == T0


def test_simulate_heating_gives_up():
    model = HeatingModel(kind="seed", c0=MIN_RATE)
    assert simulate_heating(T0, 0, 38, constant(5), model, limit=timedelta(hours=1)) is None


def test_simulate_cooling_matches_exponential():
    water = simulate_cooling(T0, T0 + timedelta(hours=10), 38, constant(5), 0.02)
    assert water == pytest.approx(5 + 33 * math.exp(-0.2))


def test_plan_without_cooling_is_deadline_minus_heat_time():
    model = HeatingModel(kind="seed", c0=2.0)
    deadline = T0 + timedelta(hours=10)
    plan = plan_heating(T0, deadline, 30, 38, constant(5), model, cooling_k=0.0)
    assert plan.on_time
    assert near(plan.start, deadline - timedelta(hours=4))
    assert plan.expected_ready <= deadline
    assert near(plan.duration, timedelta(hours=4))


def test_plan_accounts_for_cooling_before_start():
    model = HeatingModel(kind="seed", c0=2.0)
    deadline = T0 + timedelta(hours=10)
    no_cooling = plan_heating(T0, deadline, 30, 38, constant(5), model, cooling_k=0.0)
    cooling = plan_heating(T0, deadline, 30, 38, constant(5), model, cooling_k=0.03)
    assert cooling.start < no_cooling.start
    assert cooling.water_at_start < 30


def test_plan_uses_forecast():
    model = fit_heating(_synthetic_sessions(40, noise=0.005), seed_rate=1.0)
    deadline = T0 + timedelta(hours=12)
    warm = plan_heating(T0, deadline, 28, 38, constant(15), model, cooling_k=0.0)
    cold_later = outside_temp_function([(T0, 15.0), (T0 + timedelta(hours=4), -5.0)], fallback=15)
    cold = plan_heating(T0, deadline, 28, 38, cold_later, model, cooling_k=0.0)
    assert cold.start < warm.start


def test_plan_late_starts_now():
    model = HeatingModel(kind="seed", c0=1.0)
    deadline = T0 + timedelta(hours=2)
    plan = plan_heating(T0, deadline, 30, 38, constant(5), model, cooling_k=0.0)
    assert not plan.on_time
    assert plan.start == T0
    assert plan.expected_ready == T0 + timedelta(hours=8)


def test_plan_already_hot_starts_at_deadline():
    model = HeatingModel(kind="seed", c0=1.0)
    deadline = T0 + timedelta(hours=2)
    plan = plan_heating(T0, deadline, 39, 38, constant(5), model, cooling_k=0.0)
    assert plan.on_time
    assert near(plan.start, deadline)


def test_detect_sessions_and_cooling():
    h = timedelta(hours=1)
    events = [
        (T0, "outside", 6.0),
        (T0, "water", 36.0),
        (T0, "heating", False),
        (T0 + 4 * h, "outside", 4.0),
        (T0 + 6 * h, "water", 33.0),
        (T0 + 6 * h, "heating", True),
        (T0 + 7 * h, "water", 35.0),
        (T0 + 8 * h, "water", 37.0),
        (T0 + 8 * h, "heating", False),
    ]
    sessions, cooling = detect(events)
    assert len(sessions) == 1
    s = sessions[0]
    assert (s.start, s.end, s.water_start, s.water_end) == (T0 + 6 * h, T0 + 8 * h, 33.0, 37.0)
    assert s.outside_avg == 4.0
    assert len(cooling) == 1
    c = cooling[0]
    assert (c.water_start, c.water_end) == (36.0, 33.0)
    assert c.outside_avg == pytest.approx((6 * 4 + 4 * 2) / 6)


def test_detect_ignores_phase_without_water_or_outside():
    h = timedelta(hours=1)
    sessions, cooling = detect(
        [
            (T0, "heating", True),
            (T0 + 2 * h, "heating", False),
        ]
    )
    assert sessions == [] and cooling == []


def test_detect_starts_phase_at_first_water_reading():
    h = timedelta(hours=1)
    sessions, _ = detect(
        [
            (T0, "outside", 5.0),
            (T0, "heating", True),
            (T0 + h, "water", 30.0),
            (T0 + 3 * h, "water", 34.0),
            (T0 + 3 * h, "heating", False),
        ]
    )
    assert sessions[0].start == T0 + h


def test_detect_merges_short_stop():
    m = timedelta(minutes=1)
    sessions, _ = detect(
        [
            (T0, "outside", 5.0),
            (T0, "water", 30.0),
            (T0, "heating", True),
            (T0 + 50 * m, "heating", False),
            (T0 + 55 * m, "heating", True),
            (T0 + 120 * m, "water", 33.0),
            (T0 + 120 * m, "heating", False),
        ]
    )
    assert len(sessions) == 1
    assert (sessions[0].start, sessions[0].end) == (T0, T0 + 120 * m)


def test_detect_long_stop_ends_session_at_stop_time():
    m = timedelta(minutes=1)
    sessions, cooling = detect(
        [
            (T0, "outside", 5.0),
            (T0, "water", 30.0),
            (T0, "heating", True),
            (T0 + 90 * m, "water", 33.0),
            (T0 + 90 * m, "heating", False),
            (T0 + 300 * m, "water", 32.0),
            (T0 + 300 * m, "heating", True),
        ]
    )
    assert sessions[0].end == T0 + 90 * m
    assert sessions[0].water_end == 33.0
    assert (cooling[0].start, cooling[0].end) == (T0 + 90 * m, T0 + 300 * m)
    assert (cooling[0].water_start, cooling[0].water_end) == (33.0, 32.0)


def test_session_ends_when_water_peaks():
    """Holding the setpoint after the water is hot isn't heating time."""
    h = timedelta(hours=1)
    sessions, _ = detect(
        [
            (T0, "outside", 5.0),
            (T0, "water", 34.0),
            (T0, "heating", True),
            (T0 + h, "water", 36.0),
            (T0 + 2 * h, "water", 38.0),
            (T0 + 3 * h, "water", 38.0),
            (T0 + 5 * h, "water", 37.5),
            (T0 + 5 * h, "heating", False),
        ]
    )
    assert (sessions[0].end, sessions[0].water_end) == (T0 + 2 * h, 38.0)
    assert sessions[0].rate == 2.0


def _mode_sessions(n: int, mode: str | None, factor: float, seed: int) -> list[Session]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        water, outside = rng.uniform(20, 34), rng.uniform(-3, 18)
        rate = _true_rate(outside, water + 2) * factor
        start = T0 + timedelta(days=i, hours=seed)
        out.append(
            Session(start, start + timedelta(hours=4 / rate), water, water + 4, outside, mode)
        )
    return out


def test_mode_factor_learned_and_applied():
    sessions = _mode_sessions(20, "boost", 1.0, 1) + _mode_sessions(5, "sleep", 0.6, 2)
    model = fit_heating(sessions, seed_rate=1.0)
    assert model.mode_factors["sleep"] / model.mode_factors["boost"] == pytest.approx(0.6, abs=0.03)
    assert model.rate(5, 30, "boost") == pytest.approx(_true_rate(5, 30), abs=0.05)
    assert model.rate(5, 30, "sleep") == pytest.approx(_true_rate(5, 30) * 0.6, abs=0.05)
    # The base fit isn't dragged down by the slower mode.
    assert model.c1 == pytest.approx(TRUE_C1, abs=0.01)


def test_mode_needs_enough_sessions():
    sessions = _mode_sessions(20, "boost", 1.0, 1) + _mode_sessions(2, "sleep", 0.5, 2)
    model = fit_heating(sessions, seed_rate=1.0)
    assert "sleep" not in model.mode_factors
    assert model.factor("sleep") == 1.0
    assert model.factor(None) == 1.0


def test_plan_uses_mode():
    model = HeatingModel(kind="seed", c0=2.0, mode_factors={"sleep": 0.5})
    deadline = T0 + timedelta(hours=12)
    fast = plan_heating(T0, deadline, 30, 38, constant(5), model, 0.0, mode="boost")
    slow = plan_heating(T0, deadline, 30, 38, constant(5), model, 0.0, mode="sleep")
    assert near(fast.duration, timedelta(hours=4))
    assert near(slow.duration, timedelta(hours=8))


def test_session_mode_roundtrip_and_old_data():
    s = Session(T0, T0 + timedelta(hours=2), 30, 34, 5, "boost")
    assert Session.from_dict(s.as_dict()) == s
    old = {k: v for k, v in s.as_dict().items() if k != "mode"}
    assert Session.from_dict(old).mode is None


def test_detect_records_mode_and_mixed_runs():
    h = timedelta(hours=1)
    sessions, _ = detect(
        [
            (T0, "outside", 5.0),
            (T0, "water", 30.0),
            (T0, "mode", "boost"),
            (T0, "heating", True),
            (T0 + 2 * h, "water", 34.0),
            (T0 + 2 * h, "heating", False),
            (T0 + 5 * h, "heating", True),
            (T0 + 6 * h, "mode", "sleep"),
            (T0 + 7 * h, "water", 36.0),
            (T0 + 7 * h, "heating", False),
        ]
    )
    assert [s.mode for s in sessions] == ["boost", None]
