# Ready By

<img src="custom_components/ready_by/brand/icon.png" alt="Ready By icon" width="120" align="right" />

[![GitHub release](https://img.shields.io/github/v/release/jackrayner/ha-ready-by)](https://github.com/jackrayner/ha-ready-by/releases)
[![Test](https://github.com/jackrayner/ha-ready-by/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/jackrayner/ha-ready-by/actions/workflows/test.yml)
[![Lint](https://github.com/jackrayner/ha-ready-by/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/jackrayner/ha-ready-by/actions/workflows/lint.yml)
[![Validate](https://github.com/jackrayner/ha-ready-by/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/jackrayner/ha-ready-by/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![License: MIT](https://img.shields.io/github/license/jackrayner/ha-ready-by)](./LICENSE)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jackrayner&repository=ha-ready-by&category=integration)
[![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ready_by)

A Home Assistant custom integration that turns a heater on at the right time so the water (or room) is up to temperature by a time you choose. It was built for a hot tub with an air-source heat pump, but it works with any `climate` entity.

It learns from every heating run how fast the heater warms the water at different outside and water temperatures, and how fast the water cools while the heater is off. From that, the current water temperature and the hourly weather forecast, it works out the latest moment it can switch on and still be ready in time.

## How it works

- **Learning.** Each time the climate entity heats for at least 30 minutes and raises the water by at least 1 °C, the run is stored as a session. The heating rate is fitted as `rate = c0 + c1 × outside + c2 × water` (least squares, with outliers such as a lid left off thrown out). With fewer than 8 sessions it uses a simpler one-variable fit on `outside − water`, and with fewer than 3 it uses a starting rate you set. Idle periods of 2 hours or more give a Newton's-law cooling coefficient.
- **Planning.** It simulates the water in 5-minute steps: cooling from now until a candidate start time, then heating, using the outside sensor now and the hourly forecast after that. A binary search finds the latest start that still reaches the target by the ready-by time (minus an optional safety buffer). It plans again when the temperatures, forecast, target or ready-by time change.
- **Control.** At the start time it sets the climate entity to `heat` at the target temperature. After the ready-by time it keeps heating for a hold period you choose, then turns the climate entity off. If there isn't enough time, it starts immediately and marks the plan as late.

Heating is detected from the climate entity's `hvac_action`. If the entity doesn't report one, it's treated as heating whenever it's in a heating mode and below its setpoint. That guess is rough, so if the heater has a power sensor, set it in the options: the heater then counts as heating while its power is above a threshold. Stops shorter than 10 minutes, such as a heat pump's defrost cycle, don't split a session. A session ends when the water first reaches its highest temperature, so time spent holding the setpoint doesn't drag the learned rate down. Power readings in kW (or mW/MW) are converted to watts.

Temperatures are assumed to be in °C.

## Installation

### Via HACS

1. Click **Open in HACS** above, or in HACS add `https://github.com/jackrayner/ha-ready-by` as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/) (category: Integration).
2. Download **Ready By** and restart Home Assistant.

### Manual

Copy `custom_components/ready_by/` into your Home Assistant `config/custom_components/` directory and restart.

## Setup

Click **Add integration** above, or go to **Settings → Devices & services → Add integration → Ready By**. Then choose:

- the heater's climate entity
- a water temperature sensor (optional; leave it empty to use the climate entity's current temperature)
- an outside temperature sensor
- a weather entity (optional, for the hourly forecast)

On first setup it learns from whatever the recorder still holds (10 days by default). To use longer history from InfluxDB, see [Importing history](#importing-history).

## Entities

| Entity | What it does |
| --- | --- |
| `datetime` Ready by | When the water should be at the target. Setting it turns the schedule on. |
| `number` Target temperature | The temperature to reach. |
| `switch` Ready-by schedule | Turns scheduling on or off. Turning it off leaves the heater as it is. |
| `sensor` Status | `idle`, `scheduled`, `heating` or `holding`. |
| `sensor` Start time | When heating will start (or started). |
| `sensor` Expected ready | When the target is expected to be reached. |
| `sensor` Heating time needed | Predicted minutes of heating. |
| `sensor` Heating rate | Predicted °C/h at the current temperatures. The model's coefficients are in its attributes. |
| `binary_sensor` On time | Whether the target will be reached by the ready-by time. |
| Diagnostic sensors | Sessions learned, model fit (R²) and cooling coefficient. |

### Options

| Option | Default | Meaning |
| --- | --- | --- |
| Heater power sensor | none | When set, decides when the heater is heating. Changing it discards learned sessions and relearns them from history. |
| Heating power threshold | 100 W | Power above which the heater counts as heating. |
| Safety buffer | 15 min | Aim to be ready this long before the ready-by time. |
| Starting heating rate | 1.0 °C/h | Used until 3 sessions have been learned. |
| Keep heating after the ready-by time | 2 h | Then the heater is turned off. 0 leaves it on. |
| Learn from recorder history on first setup | On | Imports past sessions once. |

## Actions and events

- `ready_by.set_ready_by`: `config_entry_id`, `ready_by`, optional `target`. Sets the time and turns the schedule on.
- `ready_by.import_sessions`: `config_entry_id`, `sessions`, `cooling`. Adds past sessions; returns how many were new.
- `ready_by.reset_model`: forgets everything learned.

Events, for your own notifications:

- `ready_by_heating_started`
- `ready_by_late`: heating started too late to be ready on time
- `ready_by_ready`: the ready-by time was reached; includes `water_temperature`

Each event carries `config_entry_id`, `name`, `ready_by`, `target`, `expected_ready` and `on_time`.

```yaml
automation:
  - alias: Hot tub heating late
    triggers:
      - trigger: event
        event_type: ready_by_late
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >
            {{ trigger.event.data.name }} won't be ready until
            {{ as_timestamp(trigger.event.data.expected_ready) | timestamp_custom('%H:%M') }}.
```

## Importing history

[`scripts/influx_backfill.py`](scripts/influx_backfill.py) reads history from InfluxDB 1.x (as written by Home Assistant's InfluxDB integration) and prints a payload for `ready_by.import_sessions`. It needs only the Python standard library.

```bash
INFLUX_USERNAME=homeassistant INFLUX_PASSWORD=... \
  python3 scripts/influx_backfill.py --url http://homeassistant.local:8086 \
  --climate climate.hot_tub --water sensor.hot_tub_water \
  --outside sensor.outside_temperature --days 365 > sessions.json
```

If you use a power sensor in the integration, pass the same one with `--power` (and `--power-threshold`) so history is read the same way.

Replace `REPLACE_WITH_ENTRY_ID` in the output with the entry's ID, then paste it into **Developer tools → Actions** for `ready_by.import_sessions` in YAML mode. Sessions already known are skipped.

## Limitations

- Temperatures are assumed to be in °C.
- A restart during a heating run starts a new session from the restart, so that run is learned as a shorter session.
- Things the integration can't see, such as the lid being off or people in the tub, make individual sessions noisy. Outlier rejection limits the damage, and the fit improves as sessions build up.

## Development

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the dev setup and commit conventions.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt ruff

python3 -m pytest
ruff check . && ruff format --check .
```

`requirements.txt` is empty: the integration has no runtime dependencies. `requirements-dev.txt` adds `pytest` and `pytest-homeassistant-custom-component`, a full Home Assistant core install used only for testing.

### Releases

Versions are cut automatically by [release-please](https://github.com/googleapis/release-please) from [Conventional Commits](https://www.conventionalcommits.org/) on `main` (`feat:` → minor, `fix:` → patch, `feat!:`/`BREAKING CHANGE:` → major). It keeps a running release PR up to date with the changelog and version bump. Merging that PR tags the release and updates `manifest.json`.

## Project structure

```text
custom_components/ready_by/
├── manifest.json        # no runtime requirements; recorder is an after_dependency
├── const.py
├── model.py             # pure Python: session detection, heating/cooling fits, planner (no HA imports)
├── tracker.py           # climate/sensor states -> detector events (live and from history)
├── history.py           # one-off recorder import on first setup
├── planner.py           # ReadyByController: settings, timers, forecast, climate calls
├── store.py             # persisted sessions, settings and schedule state
├── config_flow.py       # entity selection, and options (buffer, seed rate, hold, history import)
├── entity.py            # shared device_info base entity
├── datetime.py          # ready-by time
├── number.py            # target temperature
├── switch.py            # schedule on/off
├── sensor.py            # status, start/ready times, duration, rate, diagnostics
├── binary_sensor.py     # on time
├── diagnostics.py
├── services.yaml        # set_ready_by, import_sessions, reset_model
├── brand/               # icon.png / icon@2x.png / logo.png
└── translations/en.json # no strings.json (Core-only build file)
scripts/
└── influx_backfill.py   # InfluxDB 1.x history -> import_sessions payload (stdlib only)
tests/
├── conftest.py          # fake climate/sensor states, mocked climate and forecast services
├── test_model.py        # pure unit tests of the maths
├── test_planner.py      # scheduling, start/ready/hold timers, events, forecast
├── test_tracker.py      # heating detection and session learning
├── test_setup.py        # setup/unload, reload persistence, entities
├── test_config_flow.py
├── test_services.py
├── test_history.py
├── test_influx_backfill.py
└── test_manifest.py
```

## License

MIT. See [LICENSE](./LICENSE).
