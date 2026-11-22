from datetime import datetime
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.ready_by.const import (
    CONF_BUFFER_MINUTES,
    CONF_CLIMATE,
    CONF_HOLD_HOURS,
    CONF_IMPORT_HISTORY,
    CONF_OUTSIDE,
    CONF_SEED_RATE,
    CONF_WATER,
    CONF_WEATHER,
    DOMAIN,
)

CLIMATE = "climate.hot_tub"
WATER = "sensor.hot_tub_water"
OUTSIDE = "sensor.outside"
WEATHER = "weather.home"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def set_climate(
    hass: HomeAssistant,
    mode: str = "off",
    action: str | None = "idle",
    current: float | None = 30.0,
    setpoint: float = 38.0,
) -> None:
    attributes: dict[str, Any] = {"current_temperature": current, "temperature": setpoint}
    if action is not None:
        attributes["hvac_action"] = action
    hass.states.async_set(CLIMATE, mode, attributes)


def set_temps(hass: HomeAssistant, water: float | None = 30.0, outside: float | None = 5.0):
    hass.states.async_set(WATER, "unavailable" if water is None else str(water))
    hass.states.async_set(OUTSIDE, "unavailable" if outside is None else str(outside))


class ClimateCalls:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hvac_mode: list[ServiceCall] = async_mock_service(hass, "climate", "set_hvac_mode")
        self.temperature: list[ServiceCall] = async_mock_service(hass, "climate", "set_temperature")

    @property
    def modes(self) -> list[str]:
        return [c.data["hvac_mode"] for c in self.hvac_mode]


@pytest.fixture
def climate_calls(hass: HomeAssistant) -> ClimateCalls:
    return ClimateCalls(hass)


def mock_forecast(hass: HomeAssistant, points: list[tuple[datetime, float]]) -> list[ServiceCall]:
    return async_mock_service(
        hass,
        "weather",
        "get_forecasts",
        response={
            WEATHER: {
                "forecast": [
                    {"datetime": when.isoformat(), "temperature": temp} for when, temp in points
                ]
            }
        },
        supports_response=SupportsResponse.ONLY,
    )


def make_entry(
    *,
    water: str | None = WATER,
    weather: str | None = None,
    hold_hours: float = 2.0,
    buffer_minutes: int = 0,
    seed_rate: float = 2.0,
) -> MockConfigEntry:
    data = {"name": "Hot tub", CONF_CLIMATE: CLIMATE, CONF_OUTSIDE: OUTSIDE}
    if water:
        data[CONF_WATER] = water
    if weather:
        data[CONF_WEATHER] = weather
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hot tub",
        unique_id=CLIMATE,
        data=data,
        options={
            CONF_BUFFER_MINUTES: buffer_minutes,
            CONF_SEED_RATE: seed_rate,
            CONF_HOLD_HOURS: hold_hours,
            CONF_IMPORT_HISTORY: False,
        },
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> MockConfigEntry:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def entity_id(hass: HomeAssistant, platform: str, entry: MockConfigEntry, key: str) -> str:
    found = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
    assert found is not None, f"{platform} {key} not registered"
    return found
