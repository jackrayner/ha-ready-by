"""Learn from the recorder's history, for the period the input archive doesn't cover."""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import chain

from homeassistant.components.recorder import get_instance, history
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .const import HISTORY_DAYS
from .model import CoolingSample, Session, detect
from .tracker import Sources, events_from_history


async def async_load_history(
    hass: HomeAssistant, sources: Sources, end: datetime | None = None
) -> tuple[list[Session], list[CoolingSample]]:
    """Sessions from whatever the recorder still holds, optionally only up to end."""
    entity_ids = sources.entity_ids
    start = dt_util.utcnow() - timedelta(days=HISTORY_DAYS)
    if end is not None and end <= start:
        return [], []
    # significant_changes_only=False keeps attribute-only updates such as hvac_action.
    by_entity = await get_instance(hass).async_add_executor_job(
        lambda: history.get_significant_states(
            hass,
            start,
            end,
            entity_ids=entity_ids,
            significant_changes_only=False,
            include_start_time_state=True,
        )
    )
    states = [s for s in chain.from_iterable(by_entity.values()) if isinstance(s, State)]
    return detect(events_from_history(states, sources))
