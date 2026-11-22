#!/usr/bin/env python3
"""Build Ready By heating sessions from Home Assistant's InfluxDB (1.x) history.

Prints a JSON payload for the ready_by.import_sessions action. Paste it into
Developer Tools > Actions (YAML mode accepts JSON) and fill in config_entry_id.

Credentials come from the INFLUX_USERNAME and INFLUX_PASSWORD environment
variables. Entity IDs are given in full (climate.hot_tub); Home Assistant's
InfluxDB integration stores them without the domain, which this script handles.

Example:
    INFLUX_USERNAME=homeassistant INFLUX_PASSWORD=... \\
        python3 scripts/influx_backfill.py --url http://ha.local:8086 \\
        --climate climate.hot_tub --water sensor.hot_tub_water \\
        --outside sensor.outside_temperature --days 365 > sessions.json

Add --power sensor.heat_pump_power (and --power-threshold, default 100 W) to
detect heating from the heater's power use, as the integration does when its
power sensor option is set.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODEL_PATH = Path(__file__).resolve().parent.parent / "custom_components" / "ready_by" / "model.py"
HEATING_ACTIONS = {"heating", "preheating"}
HEATING_MODES = {"heat", "heat_cool", "auto"}
WATTS_PER_UNIT = {"mW": 0.001, "W": 1.0, "kW": 1000.0, "MW": 1_000_000.0}


def load_model():
    spec = importlib.util.spec_from_file_location("ready_by_model", MODEL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def query(url: str, db: str, q: str) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"db": db, "q": q, "epoch": "s"})
    request = urllib.request.Request(f"{url.rstrip('/')}/query?{params}")
    user, password = os.environ.get("INFLUX_USERNAME"), os.environ.get("INFLUX_PASSWORD")
    if user:
        token = base64.b64encode(f"{user}:{password or ''}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.load(response)
    rows: list[dict[str, Any]] = []
    for result in payload.get("results", []):
        if "error" in result:
            raise RuntimeError(result["error"])
        for series in result.get("series", []):
            columns = series["columns"]
            for values in series["values"]:
                row = dict(zip(columns, values, strict=True))
                row["_measurement"] = series.get("name")
                rows.append(row)
    return rows


def _object_id(entity_id: str) -> str:
    return entity_id.split(".", 1)[1] if "." in entity_id else entity_id


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def fetch_entity(url: str, db: str, entity_id: str, days: int) -> list[dict[str, Any]]:
    q = (
        f"SELECT * FROM /.*/ WHERE \"entity_id\" = '{_object_id(entity_id)}' "
        f"AND time > now() - {int(days)}d"
    )
    return sorted(query(url, db, q), key=lambda r: r["time"])


def climate_heating(row: dict[str, Any]) -> bool | None:
    """Mirror of tracker.climate_is_heating for InfluxDB rows."""
    mode = row.get("state")
    if mode in (None, "unknown", "unavailable"):
        return None
    action = row.get("hvac_action_str")
    if action is not None:
        return action in HEATING_ACTIONS
    current = _float(row.get("current_temperature"))
    setpoint = _float(row.get("temperature"))
    if mode not in HEATING_MODES or current is None or setpoint is None:
        return False
    return current < setpoint


def build_events(
    climate_rows: list[dict[str, Any]],
    water_rows: list[dict[str, Any]] | None,
    outside_rows: list[dict[str, Any]],
    power_rows: list[dict[str, Any]] | None = None,
    power_threshold: float = 0.0,
) -> list[tuple[datetime, str, Any]]:
    def when(row: dict[str, Any]) -> datetime:
        return datetime.fromtimestamp(row["time"], tz=UTC)

    events: list[tuple[datetime, str, Any]] = []
    for row in climate_rows:
        if row.get("state") not in (None, "unknown", "unavailable"):
            events.append((when(row), "mode", row.get("preset_mode_str")))
        if water_rows is None:
            events.append((when(row), "water", _float(row.get("current_temperature"))))
        if power_rows is None:
            heating = climate_heating(row)
            if heating is not None:
                events.append((when(row), "heating", heating))
    for row in power_rows or []:
        power = _float(row.get("value"))
        if power is not None:
            # Home Assistant's InfluxDB integration names the measurement after the unit.
            power *= WATTS_PER_UNIT.get(row.get("_measurement"), 1.0)
            events.append((when(row), "heating", power > power_threshold))
    for row in water_rows or []:
        events.append((when(row), "water", _float(row.get("value"))))
    for row in outside_rows:
        events.append((when(row), "outside", _float(row.get("value"))))
    return sorted(events, key=lambda e: (e[0], e[1] == "heating"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8086")
    parser.add_argument("--db", default="homeassistant")
    parser.add_argument("--climate", required=True)
    parser.add_argument("--water", help="omit to use the climate entity's current_temperature")
    parser.add_argument("--outside", required=True)
    parser.add_argument("--power", help="heater power sensor; decides heating instead of climate")
    parser.add_argument("--power-threshold", type=float, default=100.0, help="watts (default 100)")
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args(argv)

    model = load_model()
    climate_rows = fetch_entity(args.url, args.db, args.climate, args.days)
    water_rows = fetch_entity(args.url, args.db, args.water, args.days) if args.water else None
    outside_rows = fetch_entity(args.url, args.db, args.outside, args.days)
    power_rows = fetch_entity(args.url, args.db, args.power, args.days) if args.power else None
    sessions, cooling = model.detect(
        build_events(climate_rows, water_rows, outside_rows, power_rows, args.power_threshold)
    )

    print(
        f"Found {len(sessions)} heating sessions and {len(cooling)} cooling samples "
        f"from {len(climate_rows)} climate, {len(water_rows or [])} water and "
        f"{len(outside_rows)} outside rows.",
        file=sys.stderr,
    )
    json.dump(
        {
            "config_entry_id": "REPLACE_WITH_ENTRY_ID",
            "sessions": [s.as_dict() for s in sessions],
            "cooling": [c.as_dict() for c in cooling],
        },
        sys.stdout,
        indent=2,
    )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
