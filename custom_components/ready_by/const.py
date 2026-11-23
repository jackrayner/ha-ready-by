"""Constants for the Ready By integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "ready_by"

CONF_CLIMATE: Final = "climate_entity"
CONF_WATER: Final = "water_sensor"
CONF_OUTSIDE: Final = "outside_sensor"
CONF_WEATHER: Final = "weather_entity"

CONF_BUFFER_MINUTES: Final = "buffer_minutes"
CONF_SEED_RATE: Final = "seed_rate"
CONF_HOLD_HOURS: Final = "hold_hours"
CONF_IMPORT_HISTORY: Final = "import_history"
CONF_POWER: Final = "power_sensor"
CONF_POWER_THRESHOLD: Final = "power_threshold"
CONF_ARCHIVE_MB: Final = "archive_mb"

DEFAULT_NAME: Final = "Hot tub"
DEFAULT_TARGET: Final = 38.0
DEFAULT_BUFFER_MINUTES: Final = 15
DEFAULT_SEED_RATE: Final = 1.0
DEFAULT_HOLD_HOURS: Final = 2.0
DEFAULT_IMPORT_HISTORY: Final = True
DEFAULT_POWER_THRESHOLD: Final = 100.0
DEFAULT_ARCHIVE_MB: Final = 200

MIN_TARGET: Final = 10.0
MAX_TARGET: Final = 45.0

EVENT_HEATING_STARTED: Final = "ready_by_heating_started"
EVENT_READY: Final = "ready_by_ready"
EVENT_LATE: Final = "ready_by_late"
EVENT_SESSION_ENDED: Final = "ready_by_session_ended"

SERVICE_SET_READY_BY: Final = "set_ready_by"
SERVICE_IMPORT_SESSIONS: Final = "import_sessions"
SERVICE_RESET_MODEL: Final = "reset_model"
SERVICE_SET_DAILY: Final = "set_daily"
SERVICE_END_SESSION: Final = "end_session"
SERVICE_EXPORT_SESSIONS: Final = "export_sessions"

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_READY_BY: Final = "ready_by"
ATTR_TARGET: Final = "target"
ATTR_SESSIONS: Final = "sessions"
ATTR_COOLING: Final = "cooling"
ATTR_TIME: Final = "time"
ATTR_ENABLED: Final = "enabled"
ATTR_OFF_AT_READY_BY: Final = "off_at_ready_by"

PHASE_IDLE: Final = "idle"
PHASE_SCHEDULED: Final = "scheduled"
PHASE_HEATING: Final = "heating"
PHASE_HOLDING: Final = "holding"
PHASES: Final = [PHASE_IDLE, PHASE_SCHEDULED, PHASE_HEATING, PHASE_HOLDING]

SCHEDULE_ONE_OFF: Final = "one_off"
SCHEDULE_DAILY: Final = "daily"

STORAGE_VERSION: Final = 1
MAX_SESSIONS: Final = 5000
MAX_COOLING_SAMPLES: Final = 5000
BALANCE_BAND_DEGREES: Final = 2.0
HISTORY_DAYS: Final = 400
ARCHIVE_SAMPLE_INTERVAL: Final = timedelta(minutes=1)
ARCHIVE_FLUSH_INTERVAL: Final = timedelta(minutes=15)
FORECAST_INTERVAL: Final = timedelta(minutes=30)
REPLAN_COOLDOWN_SECONDS: Final = 30
# Water within this much of the target counts as reaching it (sensors often
# report whole or half degrees, and heaters may stop just short of the setpoint).
REACHED_TOLERANCE: Final = 0.5
RESULT_REACHED: Final = "reached"
RESULT_MISSED: Final = "missed"
RESULTS: Final = [RESULT_REACHED, RESULT_MISSED]
