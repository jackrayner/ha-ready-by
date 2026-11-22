"""Seed the model from the recorder's history on first setup."""

from __future__ import annotations

from datetime import timedelta
from itertools import chain

from homeassistant.components.recorder import get_instance, history
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .const import HISTORY_DAYS
from .model import CoolingSample, Session, detect
from .tracker import events_from_history


async def async_load_history(
    hass: HomeAssistant, climate_id: str, water_id: str | None, outside_id: str
) -> tuple[list[Session], list[CoolingSample]]:
    entity_ids = [e for e in (climate_id, water_id, outside_id) if e]
    start = dt_util.utcnow() - timedelta(days=HISTORY_DAYS)
    # significant_changes_only=False keeps attribute-only updates such as hvac_action.
    by_entity = await get_instance(hass).async_add_executor_job(
        lambda: history.get_significant_states(
            hass,
            start,
            entity_ids=entity_ids,
            significant_changes_only=False,
            include_start_time_state=True,
        )
    )
    states = [s for s in chain.from_iterable(by_entity.values()) if isinstance(s, State)]
    return detect(events_from_history(states, climate_id, water_id, outside_id))
