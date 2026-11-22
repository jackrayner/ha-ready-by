from datetime import timedelta

import pytest
from conftest import ClimateCalls, make_entry, set_climate, set_temps, setup_entry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.ready_by.const import DOMAIN


def _sample(start, hours, water_start, water_end, outside=5.0):
    return {
        "start": start.isoformat(),
        "end": (start + timedelta(hours=hours)).isoformat(),
        "water_start": water_start,
        "water_end": water_end,
        "outside_avg": outside,
    }


async def test_set_ready_by_service(hass: HomeAssistant, climate_calls: ClimateCalls):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    when = dt_util.utcnow() + timedelta(hours=8)
    await hass.services.async_call(
        DOMAIN,
        "set_ready_by",
        {"config_entry_id": entry.entry_id, "ready_by": when.isoformat(), "target": 40},
        blocking=True,
    )
    assert entry.runtime_data.data.target == 40
    assert entry.runtime_data.data.enabled
    assert entry.runtime_data.plan is not None


async def test_import_and_reset(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    entry = await setup_entry(hass, make_entry())
    base = dt_util.utcnow() - timedelta(days=10)
    sessions = [_sample(base + timedelta(days=i), 2, 30, 33 + i * 0.1) for i in range(4)]
    sessions.append(_sample(base, 0.2, 30, 31))  # too short, dropped
    cooling = [_sample(base + timedelta(days=5), 10, 38, 34)]
    response = await hass.services.async_call(
        DOMAIN,
        "import_sessions",
        {"config_entry_id": entry.entry_id, "sessions": sessions, "cooling": cooling},
        blocking=True,
        return_response=True,
    )
    assert response == {"sessions_added": 4, "cooling_added": 1}
    controller = entry.runtime_data
    assert controller.model.kind == "simple"
    assert controller.cooling_n == 1

    # Importing the same data again adds nothing.
    response = await hass.services.async_call(
        DOMAIN,
        "import_sessions",
        {"config_entry_id": entry.entry_id, "sessions": sessions},
        blocking=True,
        return_response=True,
    )
    assert response["sessions_added"] == 0

    await hass.services.async_call(
        DOMAIN, "reset_model", {"config_entry_id": entry.entry_id}, blocking=True
    )
    assert controller.data.sessions == []
    assert controller.model.kind == "seed"


async def test_unknown_entry_is_rejected(hass: HomeAssistant):
    set_climate(hass)
    set_temps(hass)
    await setup_entry(hass, make_entry())
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "reset_model", {"config_entry_id": "nope"}, blocking=True
        )
