import gzip
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from conftest import CLIMATE, OUTSIDE, POWER, WATER, make_entry, set_climate, set_temps, setup_entry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.ready_by.archive import InputArchive, Row, events_from_rows
from custom_components.ready_by.const import CONF_IMPORT_HISTORY, CONF_POWER_THRESHOLD, DOMAIN
from custom_components.ready_by.model import CoolingSample, Session, detect
from custom_components.ready_by.store import _balanced

T0 = datetime(2026, 1, 31, 22, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
SOURCES = {"climate": CLIMATE, "water": WATER, "outside": OUTSIDE, "power": POWER}


def _run(start: datetime, *, power: float = 1500, minutes: int = 120) -> list[Row]:
    """An idle hour, a heating run of 2 °C/h at the given power, then an idle hour."""
    rows = []
    for i in range(60):
        rows.append(Row(start + i * MINUTE, 30.0, 4.0, 0.0, False, "boost"))
    for i in range(minutes):
        t = start + (60 + i) * MINUTE
        rows.append(Row(t, 30.0 + round(i / 30) * 1.0, 4.0, power, True, "boost"))
    end = start + (60 + minutes) * MINUTE
    for i in range(60):
        rows.append(Row(end + i * MINUTE, 34.0, 4.0, 0.0, False, "boost"))
    return rows


def test_append_read_roundtrip_across_months(tmp_path):
    archive = InputArchive(tmp_path, 10**9)
    rows = _run(T0)
    archive.append(SOURCES, rows[:100])
    archive.append(SOURCES, rows[100:])
    assert [p.name for p in archive.files()] == [
        "inputs-2026-01.jsonl.gz",
        "inputs-2026-02.jsonl.gz",
    ]
    assert archive.read(SOURCES) == rows


def test_rows_from_other_sources_are_not_read(tmp_path):
    archive = InputArchive(tmp_path, 10**9)
    archive.append({**SOURCES, "outside": "sensor.old_outside"}, _run(T0))
    archive.append(SOURCES, _run(T0 + timedelta(days=1)))
    rows = archive.read(SOURCES)
    assert rows[0].when == T0 + timedelta(days=1)


def test_damaged_tail_keeps_earlier_rows(tmp_path):
    archive = InputArchive(tmp_path, 10**9)
    rows = _run(datetime(2026, 3, 1, tzinfo=UTC))
    archive.append(SOURCES, rows)
    path = archive.files()[0]
    with open(path, "ab") as f:
        f.write(gzip.compress(b"[1,2,3")[:-6])
    assert archive.read(SOURCES) == rows


def test_prune_removes_oldest_months_over_limit(tmp_path):
    archive = InputArchive(tmp_path, 10**9)
    for month in (1, 2, 3):
        archive.append(SOURCES, _run(datetime(2026, month, 5, tzinfo=UTC)))
    one_month = archive.files()[0].stat().st_size
    archive.limit_bytes = int(one_month * 2.5)
    archive.prune()
    assert [p.name for p in archive.files()] == [
        "inputs-2026-02.jsonl.gz",
        "inputs-2026-03.jsonl.gz",
    ]


def test_events_from_rows_use_current_power_threshold():
    rows = _run(T0, power=400)
    sessions, cooling = detect(events_from_rows(rows, power_threshold=100, use_power=True))
    assert len(sessions) == 1
    assert sessions[0].mode == "boost"
    # The synthetic water first reaches its peak (34 °C) 105 minutes in; the session ends there.
    assert sessions[0].rate == pytest.approx(4 / 1.75)
    # Raising the threshold above the run's power means it no longer counts as heating.
    sessions, _ = detect(events_from_rows(rows, power_threshold=500, use_power=True))
    assert sessions == []


def test_events_from_rows_without_power_use_heating_flag():
    rows = [Row(r.when, r.water, r.outside, None, r.heating, r.mode) for r in _run(T0)]
    sessions, _ = detect(events_from_rows(rows, power_threshold=100, use_power=False))
    assert len(sessions) == 1


def test_balanced_keeps_cold_weather_sessions():
    cold = [
        Session(T0 + timedelta(days=i), T0 + timedelta(days=i, hours=3), 30, 34, -3.0)
        for i in range(5)
    ]
    mild = [
        Session(T0 + timedelta(days=100 + i), T0 + timedelta(days=100 + i, hours=2), 30, 34, 12.0)
        for i in range(50)
    ]
    kept = _balanced(cold + mild, 20)
    assert len(kept) == 20
    assert all(s in kept for s in cold)
    # The newest mild sessions survive.
    assert mild[-1] in kept and mild[0] not in kept


def test_balanced_under_cap_is_sorted_copy():
    a = CoolingSample(T0 + timedelta(days=1), T0 + timedelta(days=1, hours=5), 38, 36, 5)
    b = CoolingSample(T0, T0 + timedelta(hours=5), 38, 36, 5)
    assert _balanced([a, b], 10) == [b, a]


async def test_live_sampling_writes_archive_and_flushes_on_unload(hass: HomeAssistant, freezer):
    set_climate(hass)
    set_temps(hass, water=30, outside=5)
    hass.states.async_set(POWER, "1.2", {"unit_of_measurement": "kW"})
    entry = await setup_entry(hass, make_entry(power=POWER))
    controller = entry.runtime_data
    for _ in range(3):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)

    rows = await hass.async_add_executor_job(controller.archive.read, SOURCES)
    assert len(rows) >= 3
    assert rows[-1].power == 1200.0
    assert (rows[-1].water, rows[-1].outside, rows[-1].heating) == (30.0, 5.0, True)


async def test_relearn_from_archive_after_threshold_change(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry(power=POWER))
    controller = entry.runtime_data
    # The archive stores whole seconds.
    start = (dt_util.utcnow() - timedelta(days=60)).replace(microsecond=0)
    await hass.async_add_executor_job(controller.archive.append, SOURCES, _run(start, power=400))
    assert controller.data.sessions == []

    # Lowering the threshold changes the sources, so it relearns from the archive.
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_POWER_THRESHOLD: 200}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    sessions = entry.runtime_data.data.sessions
    assert len(sessions) == 1
    assert sessions[0].start == start + 60 * MINUTE


async def test_reset_uses_archive_and_recorder_only_before_it(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    hass.config.components.add("recorder")
    entry = make_entry(power=POWER)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_IMPORT_HISTORY: True}
    )
    archive_start = (dt_util.utcnow() - timedelta(days=20)).replace(microsecond=0)
    before = archive_start - timedelta(days=5)
    overlapping = archive_start + timedelta(days=1)
    recorded = (
        [
            Session(before, before + timedelta(hours=2), 30, 34, 3, "boost"),
            Session(overlapping, overlapping + timedelta(hours=2), 30, 34, 3, "boost"),
        ],
        [],
    )
    with patch(
        "custom_components.ready_by.planner.async_load_history", return_value=recorded
    ) as load:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        controller = entry.runtime_data
        await hass.async_add_executor_job(controller.archive.append, SOURCES, _run(archive_start))
        await controller.async_reset_model()

    assert load.call_args.kwargs["end"] == archive_start
    starts = [s.start for s in controller.data.sessions]
    assert starts == [before, archive_start + 60 * MINUTE]


async def test_export_and_import_roundtrip(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    controller = entry.runtime_data
    start = dt_util.utcnow() - timedelta(days=3)
    await controller.async_import(
        [Session(start, start + timedelta(hours=2), 30, 34, 5, "sleep")],
        [CoolingSample(start - timedelta(hours=8), start, 36, 30, 5)],
    )
    exported = await hass.services.async_call(
        DOMAIN,
        "export_sessions",
        {"config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )
    assert exported["sessions"][0]["mode"] == "sleep"

    await controller.async_reset_model()
    assert controller.data.sessions == []
    response = await hass.services.async_call(
        DOMAIN,
        "import_sessions",
        {"config_entry_id": entry.entry_id, **exported},
        blocking=True,
        return_response=True,
    )
    assert response == {"sessions_added": 1, "cooling_added": 1}
    assert controller.data.sessions[0].mode == "sleep"


async def test_archive_removed_with_entry(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    archive = entry.runtime_data.archive
    await hass.async_add_executor_job(archive.append, SOURCES, _run(T0))
    assert archive.directory.is_dir()
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not archive.directory.exists()


async def test_diagnostic_sensors_have_statistics(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    from conftest import entity_id

    for key in ("cooling_k", "fit_r2"):
        state = hass.states.get(entity_id(hass, "sensor", entry, key))
        assert state.attributes.get("state_class") == "measurement"
