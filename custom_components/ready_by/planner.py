"""The Ready By controller: learns, plans, and switches the climate entity."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.components.climate import (
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, CONF_NAME
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .archive import InputArchive, Row, events_from_rows
from .const import (
    ARCHIVE_FLUSH_INTERVAL,
    ARCHIVE_SAMPLE_INTERVAL,
    CONF_ARCHIVE_MB,
    CONF_BUFFER_MINUTES,
    CONF_CLIMATE,
    CONF_HOLD_HOURS,
    CONF_IMPORT_HISTORY,
    CONF_OUTSIDE,
    CONF_POWER,
    CONF_POWER_THRESHOLD,
    CONF_SEED_RATE,
    CONF_WATER,
    CONF_WEATHER,
    DEFAULT_ARCHIVE_MB,
    DEFAULT_BUFFER_MINUTES,
    DEFAULT_HOLD_HOURS,
    DEFAULT_IMPORT_HISTORY,
    DEFAULT_NAME,
    DEFAULT_POWER_THRESHOLD,
    DEFAULT_SEED_RATE,
    DOMAIN,
    EVENT_HEATING_STARTED,
    EVENT_LATE,
    EVENT_READY,
    EVENT_SESSION_ENDED,
    FORECAST_INTERVAL,
    PHASE_HEATING,
    PHASE_HOLDING,
    PHASE_IDLE,
    PHASE_SCHEDULED,
    REACHED_TOLERANCE,
    REPLAN_COOLDOWN_SECONDS,
    RESULT_MISSED,
    RESULT_REACHED,
    SCHEDULE_DAILY,
    SCHEDULE_ONE_OFF,
)
from .history import async_load_history
from .model import (
    CoolingSample,
    HeatingModel,
    OutsideTemp,
    Plan,
    Session,
    detect,
    fit_cooling,
    fit_heating,
    outside_temp_function,
    plan_heating,
    simulate_heating,
)
from .store import ReadyByData, ReadyByStore
from .tracker import SessionTracker, Sources, power_watts

_LOGGER = logging.getLogger(__name__)


class ReadyByController:
    """Owns the learned model, the user's settings, and the heating schedule."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._store = ReadyByStore(hass, entry.entry_id)
        self.data = ReadyByData()
        self.model = HeatingModel(kind="seed", c0=self.seed_rate)
        self.cooling_k = 0.0
        self.cooling_n = 0
        self.plan: Plan | None = None
        self.forecast: list[tuple[datetime, float]] = []
        self._listeners: list[CALLBACK_TYPE] = []
        self._start_unsub: CALLBACK_TYPE | None = None
        self._ready_unsub: CALLBACK_TYPE | None = None
        self._end_unsub: CALLBACK_TYPE | None = None
        self._forecast_unsub: CALLBACK_TYPE | None = None
        self._archive_unsubs: list[CALLBACK_TYPE] = []
        self._archive_buffer: list[Row] = []
        self.archive = InputArchive(
            Path(hass.config.path(".storage", DOMAIN, entry.entry_id)),
            int(entry.options.get(CONF_ARCHIVE_MB, DEFAULT_ARCHIVE_MB)) * 1024 * 1024,
        )
        self.tracker = SessionTracker(
            hass,
            self.sources,
            on_sample=self._on_sample,
            on_change=self._on_inputs_changed,
        )
        self._debouncer: Debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=REPLAN_COOLDOWN_SECONDS,
            immediate=True,
            function=self.async_replan,
        )

    @property
    def name(self) -> str:
        return self.entry.data.get(CONF_NAME, DEFAULT_NAME)

    @property
    def climate_id(self) -> str:
        return self.entry.data[CONF_CLIMATE]

    @property
    def water_id(self) -> str | None:
        return self.entry.data.get(CONF_WATER) or None

    @property
    def outside_id(self) -> str:
        return self.entry.data[CONF_OUTSIDE]

    @property
    def sources(self) -> Sources:
        return Sources(
            climate_id=self.climate_id,
            outside_id=self.outside_id,
            water_id=self.water_id,
            power_id=self.entry.data.get(CONF_POWER) or None,
            power_threshold=float(
                self.entry.options.get(CONF_POWER_THRESHOLD, DEFAULT_POWER_THRESHOLD)
            ),
        )

    @property
    def weather_id(self) -> str | None:
        return self.entry.data.get(CONF_WEATHER) or None

    @property
    def buffer(self) -> timedelta:
        return timedelta(
            minutes=self.entry.options.get(CONF_BUFFER_MINUTES, DEFAULT_BUFFER_MINUTES)
        )

    @property
    def seed_rate(self) -> float:
        return float(self.entry.options.get(CONF_SEED_RATE, DEFAULT_SEED_RATE))

    @property
    def hold(self) -> timedelta:
        """Session length: how long to keep heating after the ready-by time."""
        return timedelta(hours=self.session_hours)

    @property
    def session_hours(self) -> float:
        if self.data.session_hours is not None:
            return self.data.session_hours
        # Before the session length entity, this was the hold_hours option.
        return float(self.entry.options.get(CONF_HOLD_HOURS, DEFAULT_HOLD_HOURS))

    @property
    def heating_rate(self) -> float | None:
        water, outside = self.tracker.water, self.tracker.outside
        if water is None or outside is None:
            return None
        return self.model.rate(outside, water, self.tracker.mode)

    async def async_setup(self) -> None:
        self.data = await self._store.async_load()
        if self.data.daily_target is None:
            # The daily schedule used the shared target before it had its own.
            self.data.daily_target = self.data.target
        if self.data.session_hours is None:
            # Carry the old hold_hours option over before the options drop it.
            self.data.session_hours = self.session_hours
        self._check_sources()
        self._refit()
        self.tracker.async_start()
        self._archive_unsubs = [
            async_track_time_interval(
                self.hass, self._async_archive_sample, ARCHIVE_SAMPLE_INTERVAL
            ),
            async_track_time_interval(self.hass, self._async_archive_flush, ARCHIVE_FLUSH_INTERVAL),
        ]
        if self.weather_id:
            self._forecast_unsub = async_track_time_interval(
                self.hass, self._async_forecast_interval, FORECAST_INTERVAL
            )
            await self.async_refresh_forecast(replan=False)
        self._resume()
        await self.async_replan()
        if not self.data.history_imported:
            self.entry.async_create_background_task(
                self.hass, self._async_relearn(), "ready_by relearn"
            )

    async def async_shutdown(self) -> None:
        self._cancel_all_timers()
        if self._forecast_unsub:
            self._forecast_unsub()
            self._forecast_unsub = None
        self.tracker.async_stop()
        for unsub in self._archive_unsubs:
            unsub()
        self._archive_unsubs = []
        await self._async_archive_flush()
        self._debouncer.async_shutdown()
        await self._store.async_save()

    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(update)

        @callback
        def remove() -> None:
            self._listeners.remove(update)

        return remove

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    async def async_set_enabled(self, enabled: bool) -> None:
        """Turn the one-off ready-by on or off."""
        self.data.enabled = enabled
        if not enabled:
            self._stop_managing(SCHEDULE_ONE_OFF)
        self._store.async_schedule_save()
        await self.async_replan()

    async def async_set_ready_by(self, ready_by: datetime, target: float | None = None) -> None:
        """Set the one-off ready-by time and turn it on."""
        self.data.ready_by = dt_util.as_utc(ready_by)
        if target is not None:
            await self.async_set_target(target, replan=False)
        self.data.enabled = True
        if self.data.active == SCHEDULE_ONE_OFF and self._running:
            self.data.active_ready_by = self.data.ready_by
            self.data.phase = PHASE_HEATING
            self._schedule_ready_and_end()
        self._store.async_schedule_save()
        await self.async_replan()

    async def async_set_daily_enabled(self, enabled: bool) -> None:
        self.data.daily_enabled = enabled
        if not enabled:
            self._stop_managing(SCHEDULE_DAILY)
        self._store.async_schedule_save()
        await self.async_replan()

    async def async_set_daily_time(self, value: time, *, enable: bool = True) -> None:
        """Set the daily ready-by time; by default this also turns the daily schedule on."""
        self.data.daily_time = value
        if enable:
            self.data.daily_enabled = True
        if self.data.active == SCHEDULE_DAILY and self.data.phase == PHASE_HEATING:
            # Move today's running occurrence to the new time.
            self.data.active_ready_by = self._daily_at(dt_util.utcnow(), value, after=False)
            self._schedule_ready_and_end()
        self._store.async_schedule_save()
        await self.async_replan()

    async def async_set_session_length(self, hours: float) -> None:
        """Set how long to keep heating after the ready-by time; 0 never turns it off."""
        self.data.session_hours = hours
        self._store.async_schedule_save()
        if self.data.phase == PHASE_HOLDING and self.data.active_ready_by is not None:
            if hours <= 0:
                self._cancel(end=True)
                await self._async_finish(turn_off=False)
                return
            if self.data.active_ready_by + self.hold <= dt_util.utcnow():
                self._cancel(end=True)
                await self._async_finish(turn_off=True)
                return
            self._schedule_ready_and_end()
        self._notify()

    async def async_end_session(self) -> None:
        """Turn the heater off now, ending any run in progress before its scheduled end."""
        self._cancel(ready=True, end=True)
        if self._running:
            await self._async_finish(turn_off=True, force=True)
        else:
            await self._async_call_climate(SERVICE_SET_HVAC_MODE, {"hvac_mode": HVACMode.OFF})
            self.hass.bus.async_fire(
                EVENT_SESSION_ENDED,
                {**self._event_data(None), "reason": "manual", "heater_off": True},
            )
            self._notify()

    async def async_set_daily_off_at_ready_by(self, enabled: bool) -> None:
        self.data.daily_off_at_ready_by = enabled
        if enabled and self.data.active == SCHEDULE_DAILY and self.data.phase == PHASE_HOLDING:
            # Already past today's ready-by: end the hold now.
            self._cancel(end=True)
            await self._async_finish(turn_off=True)
            return
        self._store.async_schedule_save()
        self._notify()

    def _stop_managing(self, schedule: str) -> None:
        """Stop a running or planned run of this schedule. The heater is left as it is."""
        if self.data.active != schedule:
            return
        self._cancel_all_timers()
        self.data.phase = PHASE_IDLE
        self.data.started_at = None
        self.data.active = None
        self.data.active_ready_by = None

    @property
    def daily_target(self) -> float:
        return self.data.target if self.data.daily_target is None else self.data.daily_target

    def target_for(self, schedule: str | None) -> float:
        """The target temperature a run of this schedule heats to."""
        return self.daily_target if schedule == SCHEDULE_DAILY else self.data.target

    @property
    def active_target(self) -> float:
        return self.target_for(self.data.active)

    async def async_set_target(self, target: float, *, replan: bool = True) -> None:
        """Set the one-off target temperature."""
        self.data.target = target
        await self._async_target_changed(SCHEDULE_ONE_OFF, replan=replan)

    async def async_set_daily_target(self, target: float, *, replan: bool = True) -> None:
        self.data.daily_target = target
        await self._async_target_changed(SCHEDULE_DAILY, replan=replan)

    async def _async_target_changed(self, schedule: str, *, replan: bool) -> None:
        self._store.async_schedule_save()
        if self._running and self.data.active == schedule:
            self._reset_outcome()
            await self._async_call_climate(
                SERVICE_SET_TEMPERATURE, {ATTR_TEMPERATURE: self.active_target}
            )
        if replan:
            await self.async_replan()
        else:
            self._notify()

    async def async_import(
        self, sessions: list[Session], cooling: list[CoolingSample]
    ) -> tuple[int, int]:
        added = (
            self.data.add_sessions([s for s in sessions if s.is_valid()]),
            self.data.add_cooling([c for c in cooling if c.is_valid()]),
        )
        self._refit()
        self._store.async_schedule_save()
        await self.async_replan()
        return added

    async def async_reset_model(self) -> None:
        """Forget everything learned, then relearn from the input archive and recorder."""
        self.data.sessions = []
        self.data.cooling = []
        self.data.history_imported = False
        self._refit()
        self._store.async_schedule_save()
        await self.async_replan()
        await self._async_relearn()

    @property
    def _archive_sources(self) -> dict[str, str | None]:
        """The entities archived rows are recorded from; rows from others aren't reused."""
        sources = self.sources
        return {
            "climate": sources.climate_id,
            "water": sources.water_id,
            "outside": sources.outside_id,
            "power": sources.power_id,
        }

    async def _async_relearn(self) -> None:
        """Learn from the input archive, and from the recorder for the time before it."""
        await self._async_archive_flush()
        sources = self.sources
        try:
            rows = await self.hass.async_add_executor_job(self.archive.read, self._archive_sources)
            events = events_from_rows(
                rows,
                power_threshold=sources.power_threshold,
                use_power=sources.power_id is not None,
            )
            sessions, cooling = await self.hass.async_add_executor_job(detect, events)
        except Exception:  # noqa: BLE001 - a damaged archive must not break the integration
            _LOGGER.warning("Could not relearn %s from its input archive", self.name, exc_info=True)
            rows, sessions, cooling = [], [], []
        archive_start = rows[0].when if rows else None

        recorder_failed = False
        if self._should_import_history():
            try:
                old_sessions, old_cooling = await async_load_history(
                    self.hass, sources, end=archive_start
                )
            except Exception:  # noqa: BLE001 - a failed import must not break the integration
                recorder_failed = True
                _LOGGER.warning(
                    "Could not import recorder history for %s", self.name, exc_info=True
                )
            else:
                if archive_start is not None:
                    old_sessions = [s for s in old_sessions if s.end <= archive_start]
                    old_cooling = [c for c in old_cooling if c.end <= archive_start]
                sessions = old_sessions + sessions
                cooling = old_cooling + cooling

        # Try the recorder again at the next setup if it failed this time.
        self.data.history_imported = not recorder_failed
        added = await self.async_import(sessions, cooling)
        _LOGGER.info(
            "Relearned %d heating sessions and %d cooling samples for %s (archive from %s)",
            *added,
            self.name,
            archive_start,
        )

    @callback
    def _async_archive_sample(self, now: datetime) -> None:
        power_id = self.sources.power_id
        self._archive_buffer.append(
            Row(
                when=now,
                water=self.tracker.water,
                outside=self.tracker.outside,
                power=power_watts(self.hass.states.get(power_id)) if power_id else None,
                heating=self.tracker.heating,
                mode=self.tracker.mode,
            )
        )

    async def _async_archive_flush(self, _now: datetime | None = None) -> None:
        rows, self._archive_buffer = self._archive_buffer, []
        if not rows:
            return
        try:
            await self.hass.async_add_executor_job(self.archive.append, self._archive_sources, rows)
        except OSError:
            _LOGGER.warning("Could not write the input archive for %s", self.name, exc_info=True)

    def export(self) -> dict[str, Any]:
        """Learned sessions and cooling samples, in the format import_sessions takes."""
        return {
            "sessions": [s.as_dict() for s in self.data.sessions],
            "cooling": [c.as_dict() for c in self.data.cooling],
        }

    @callback
    def _on_sample(self, sample: Session | CoolingSample) -> None:
        if isinstance(sample, Session):
            self.data.add_sessions([sample])
        else:
            self.data.add_cooling([sample])
        self._refit()
        self._store.async_schedule_save()
        self._notify()

    @callback
    def _on_inputs_changed(self) -> None:
        self._check_reached()
        if self.plan is None and self._inputs_ready() and self.upcoming(dt_util.utcnow()):
            # The plan was only missing inputs (e.g. sensors still loading after a
            # restart); plan as soon as they arrive rather than after the cooldown.
            self.entry.async_create_task(self.hass, self.async_replan(), "ready_by replan")
            return
        self._debouncer.async_schedule_call()

    def _reset_outcome(self) -> None:
        if self.data.phase == PHASE_HEATING:
            self.data.predicted_ready = None
            self.data.reached_at = None

    def _check_reached(self) -> None:
        """Note when the water first reaches the target during a run."""
        water = self.tracker.water
        if (
            self.data.phase == PHASE_HEATING
            and self.data.reached_at is None
            and water is not None
            and water >= self.active_target - REACHED_TOLERANCE
        ):
            self.data.reached_at = dt_util.utcnow()
            self._store.async_schedule_save()

    def _inputs_ready(self) -> bool:
        return self.tracker.water is not None and self.outside_at(dt_util.utcnow()) is not None

    def _should_import_history(self) -> bool:
        return bool(
            self.entry.options.get(CONF_IMPORT_HISTORY, DEFAULT_IMPORT_HISTORY)
            and "recorder" in self.hass.config.components
        )

    def _check_sources(self) -> None:
        """Forget data learned from different entities, so it can be relearned."""
        key = repr(self.sources)
        if self.data.sources_key is not None and self.data.sources_key != key:
            _LOGGER.info(
                "Sources for %s changed; discarding %d learned sessions to relearn them",
                self.name,
                len(self.data.sessions),
            )
            self.data.sessions = []
            self.data.cooling = []
            self.data.history_imported = False
        self.data.sources_key = key
        self._store.async_schedule_save()

    def _refit(self) -> None:
        self.model = fit_heating(self.data.sessions, self.seed_rate)
        self.cooling_k, self.cooling_n = fit_cooling(self.data.cooling)

    async def _async_forecast_interval(self, _now: datetime) -> None:
        await self.async_refresh_forecast()

    async def async_refresh_forecast(self, *, replan: bool = True) -> None:
        if not self.weather_id:
            return
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {ATTR_ENTITY_ID: self.weather_id, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("Hourly forecast unavailable for %s: %s", self.weather_id, err)
            return
        points: list[tuple[datetime, float]] = []
        for item in (response or {}).get(self.weather_id, {}).get("forecast", []):
            when = dt_util.parse_datetime(str(item.get("datetime")))
            try:
                temperature = float(item["temperature"])
            except (KeyError, TypeError, ValueError):
                continue
            if when is not None:
                points.append((dt_util.as_utc(when), temperature))
        self.forecast = points
        if replan:
            self._debouncer.async_schedule_call()

    def outside_at(self, now: datetime) -> OutsideTemp | None:
        """Outside temperature over time: the sensor now, then the forecast."""
        current = self.tracker.outside
        points = [p for p in self.forecast if p[0] > now]
        if current is not None:
            points.insert(0, (now, current))
        if not points:
            return None
        return outside_temp_function(points, fallback=points[0][1])

    @property
    def _running(self) -> bool:
        return self.data.phase in (PHASE_HEATING, PHASE_HOLDING)

    def _daily_at(self, now: datetime, value: time, *, after: bool = True) -> datetime:
        """Today's occurrence of a daily time in local time, or tomorrow's if it has passed."""
        local = dt_util.as_local(now)
        occurrence = datetime.combine(local.date(), value, tzinfo=local.tzinfo)
        occurrence = dt_util.as_local(occurrence)
        if after and occurrence <= local:
            occurrence = datetime.combine(
                local.date() + timedelta(days=1), value, tzinfo=local.tzinfo
            )
        return dt_util.as_utc(occurrence)

    def upcoming(self, now: datetime) -> list[tuple[str, datetime]]:
        """Enabled schedules' next ready-by times, earliest first."""
        found: list[tuple[str, datetime]] = []
        data = self.data
        if data.enabled and data.ready_by is not None and data.ready_by > now:
            found.append((SCHEDULE_ONE_OFF, data.ready_by))
        if data.daily_enabled and data.daily_time is not None:
            occurrence = self._daily_at(now, data.daily_time)
            if data.daily_done_until is not None and occurrence <= data.daily_done_until:
                # That day's run was already ended early; move on to the next day.
                occurrence = self._daily_at(data.daily_done_until, data.daily_time)
            found.append((SCHEDULE_DAILY, occurrence))
        return sorted(found, key=lambda item: item[1])

    @property
    def next_ready_by(self) -> tuple[str, datetime] | None:
        if self.data.active and self.data.active_ready_by and self.data.phase != PHASE_IDLE:
            return self.data.active, self.data.active_ready_by
        upcoming = self.upcoming(dt_util.utcnow())
        return upcoming[0] if upcoming else None

    def _make_plan(self, now: datetime, ready_by: datetime) -> Plan | None:
        water = self.tracker.water
        outside_at = self.outside_at(now)
        if water is None or outside_at is None:
            return None
        return plan_heating(
            now,
            ready_by - self.buffer,
            water,
            self.active_target,
            outside_at,
            self.model,
            self.cooling_k,
            mode=self.tracker.mode,
        )

    async def async_replan(self) -> None:
        now = dt_util.utcnow()
        self._cancel(start=True)
        data = self.data

        if data.enabled and data.ready_by is not None and data.ready_by <= now:
            if not (self._running and data.active == SCHEDULE_ONE_OFF):
                # The one-off time passed without a run (e.g. HA was down).
                data.enabled = False
                self._store.async_schedule_save()

        upcoming = self.upcoming(now)

        if self._running:
            if (
                data.phase == PHASE_HEATING
                and upcoming
                and data.active_ready_by is not None
                and upcoming[0][1] < data.active_ready_by
            ):
                # An earlier ready-by was set while heating: this run now serves it.
                previous_target = self.active_target
                data.active, data.active_ready_by = upcoming[0]
                self._schedule_ready_and_end()
                self._store.async_schedule_save()
                if self.active_target != previous_target:
                    self._reset_outcome()
                    await self._async_call_climate(
                        SERVICE_SET_TEMPERATURE, {ATTR_TEMPERATURE: self.active_target}
                    )
            self.plan = self._progress_plan(now)
            if data.phase == PHASE_HEATING and data.predicted_ready is None and self.plan:
                # No prediction for this run yet (its target changed, or none was possible
                # at the start): judge it against the current one.
                data.predicted_ready = self.plan.expected_ready
            self._check_reached()
            self._notify()
            return

        if not upcoming:
            data.phase = PHASE_IDLE
            data.active = None
            data.active_ready_by = None
            self.plan = None
            self._notify()
            return

        data.active, data.active_ready_by = upcoming[0]
        self.plan = self._make_plan(now, data.active_ready_by)
        if self.plan is None:
            data.phase = PHASE_IDLE
            self._notify()
            return

        data.phase = PHASE_SCHEDULED
        if self.plan.start <= now:
            await self._async_start_heating()
            return
        self._start_unsub = async_track_point_in_utc_time(
            self.hass, self._async_start_fired, self.plan.start
        )
        self._notify()

    def _progress_plan(self, now: datetime) -> Plan | None:
        water = self.tracker.water
        outside_at = self.outside_at(now)
        ready_by = self.data.active_ready_by
        if water is None or outside_at is None or ready_by is None:
            return None
        ready = simulate_heating(
            now, water, self.active_target, outside_at, self.model, mode=self.tracker.mode
        )
        return Plan(
            start=self.data.started_at or now,
            expected_ready=ready,
            water_at_start=water,
            on_time=ready is not None and ready <= ready_by,
        )

    async def _async_start_fired(self, _now: datetime) -> None:
        self._start_unsub = None
        await self._async_start_heating()

    async def _async_start_heating(self) -> None:
        self._cancel(start=True)
        plan = self.plan
        self.data.phase = PHASE_HEATING
        self.data.started_at = dt_util.utcnow()
        self.data.predicted_ready = plan.expected_ready if plan else None
        self.data.reached_at = None
        await self._async_call_climate(SERVICE_SET_HVAC_MODE, {"hvac_mode": HVACMode.HEAT})
        await self._async_call_climate(
            SERVICE_SET_TEMPERATURE, {ATTR_TEMPERATURE: self.active_target}
        )
        event_data = self._event_data(plan)
        self.hass.bus.async_fire(EVENT_HEATING_STARTED, event_data)
        if plan is not None and not plan.on_time:
            self.hass.bus.async_fire(EVENT_LATE, event_data)
        self._schedule_ready_and_end()
        self._check_reached()
        self._store.async_schedule_save()
        await self.async_replan()

    def _schedule_ready_and_end(self) -> None:
        self._cancel(ready=True, end=True)
        ready_by = self.data.active_ready_by
        if ready_by is None:
            return
        if self.data.phase == PHASE_HEATING:
            self._ready_unsub = async_track_point_in_utc_time(
                self.hass, self._async_ready_fired, max(ready_by, dt_util.utcnow())
            )
        elif self.data.phase == PHASE_HOLDING:
            self._end_unsub = async_track_point_in_utc_time(
                self.hass, self._async_end_fired, max(ready_by + self.hold, dt_util.utcnow())
            )

    def _resume(self) -> None:
        """Pick a run back up after a restart."""
        data = self.data
        if data.phase == PHASE_SCHEDULED:
            data.phase = PHASE_IDLE
        if not self._running:
            return
        if data.active is None:
            # Stored by a version without schedules: the run was the one-off.
            data.active, data.active_ready_by = SCHEDULE_ONE_OFF, data.ready_by
        still_on = data.enabled if data.active == SCHEDULE_ONE_OFF else data.daily_enabled
        if not still_on or data.active_ready_by is None:
            data.phase = PHASE_IDLE
            data.active = None
            return
        self._schedule_ready_and_end()

    async def _async_ready_fired(self, _now: datetime) -> None:
        self._ready_unsub = None
        self._check_reached()
        result = self._record_result()
        event_data = {
            **self._event_data(self.plan),
            "water_temperature": self.tracker.water,
            "reached": result["result"] == RESULT_REACHED,
        }
        self.hass.bus.async_fire(EVENT_READY, event_data)
        if result["result"] == RESULT_MISSED:
            self.hass.bus.async_fire(EVENT_LATE, event_data)
        if self.data.active == SCHEDULE_DAILY and self.data.daily_off_at_ready_by:
            await self._async_finish(turn_off=True)
        elif self.hold > timedelta(0):
            self.data.phase = PHASE_HOLDING
            self._schedule_ready_and_end()
            self._store.async_schedule_save()
            await self.async_replan()
        else:
            await self._async_finish(turn_off=False)

    def _record_result(self) -> dict[str, Any]:
        """Compare the run with its prediction at the ready-by time, and keep it."""
        data = self.data
        predicted, reached_at = data.predicted_ready, data.reached_at
        error = (
            round((reached_at - predicted).total_seconds() / 60)
            if predicted is not None and reached_at is not None
            else None
        )
        result = {
            "result": RESULT_REACHED if reached_at is not None else RESULT_MISSED,
            "schedule": data.active,
            "ready_by": data.active_ready_by.isoformat() if data.active_ready_by else None,
            "target": self.active_target,
            "water_temperature": self.tracker.water,
            "started_at": data.started_at.isoformat() if data.started_at else None,
            "predicted_ready": predicted.isoformat() if predicted else None,
            "reached_at": reached_at.isoformat() if reached_at else None,
            "prediction_error_minutes": error,
        }
        data.last_run = result
        self._store.async_schedule_save()
        self._notify()
        return result

    async def _async_end_fired(self, _now: datetime) -> None:
        self._end_unsub = None
        await self._async_finish(turn_off=True)

    async def _async_finish(self, *, turn_off: bool, force: bool = False) -> None:
        """End the active run. force is a manual end: always turn off, and say so."""
        data = self.data
        event_data = self._event_data(self.plan)
        if data.active == SCHEDULE_ONE_OFF:
            data.enabled = False
        elif data.active == SCHEDULE_DAILY:
            data.daily_done_until = data.active_ready_by
        data.phase = PHASE_IDLE
        data.started_at = None
        data.predicted_ready = None
        data.reached_at = None
        data.active = None
        data.active_ready_by = None
        if turn_off:
            now = dt_util.utcnow()
            upcoming = self.upcoming(now)
            follow_on = self._make_plan(now, upcoming[0][1]) if upcoming else None
            # Don't switch off only to switch straight back on for the next run,
            # unless the session was ended by hand.
            if force or follow_on is None or follow_on.start > now + timedelta(minutes=5):
                await self._async_call_climate(SERVICE_SET_HVAC_MODE, {"hvac_mode": HVACMode.OFF})
            else:
                turn_off = False
        self.hass.bus.async_fire(
            EVENT_SESSION_ENDED,
            {**event_data, "reason": "manual" if force else "scheduled", "heater_off": turn_off},
        )
        self._store.async_schedule_save()
        await self.async_replan()

    async def _async_call_climate(self, service: str, data: dict[str, Any]) -> None:
        try:
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                service,
                {ATTR_ENTITY_ID: self.climate_id, **data},
                blocking=True,
            )
        except HomeAssistantError:
            _LOGGER.exception("Calling climate.%s on %s failed", service, self.climate_id)

    def _event_data(self, plan: Plan | None) -> dict[str, Any]:
        return {
            "config_entry_id": self.entry.entry_id,
            "name": self.name,
            "schedule": self.data.active,
            "ready_by": (
                self.data.active_ready_by.isoformat() if self.data.active_ready_by else None
            ),
            "target": self.active_target,
            "expected_ready": (
                plan.expected_ready.isoformat() if plan and plan.expected_ready else None
            ),
            "on_time": plan.on_time if plan else None,
        }

    def _cancel(self, *, start: bool = False, ready: bool = False, end: bool = False) -> None:
        if start and self._start_unsub:
            self._start_unsub()
            self._start_unsub = None
        if ready and self._ready_unsub:
            self._ready_unsub()
            self._ready_unsub = None
        if end and self._end_unsub:
            self._end_unsub()
            self._end_unsub = None

    def _cancel_all_timers(self) -> None:
        self._cancel(start=True, ready=True, end=True)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "data": self.data.as_dict(),
            "model": self.model.as_dict(),
            "cooling_k": self.cooling_k,
            "cooling_samples": self.cooling_n,
            "plan": self.plan.as_dict() if self.plan else None,
            "forecast_points": len(self.forecast),
            "water": self.tracker.water,
            "outside": self.tracker.outside,
            "heating": self.tracker.heating,
            "mode": self.tracker.mode,
        }


type ReadyByConfigEntry = ConfigEntry[ReadyByController]
