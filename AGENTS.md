# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this is

A [HACS](https://hacs.xyz/) custom integration for Home Assistant (domain `ready_by`). It learns how fast a `climate` entity heats water from past sessions, plans the latest start time that reaches a target by a ready-by time, and switches the climate entity on and off. There are no runtime dependencies.

## Commands

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt ruff

python3 -m pytest
ruff check . && ruff format --check .
npx markdownlint-cli2 --config .markdownlint.jsonc "**/*.md" "!CHANGELOG.md" "!.venv/**"
```

`pytest.ini` sets `pythonpath = .` and `asyncio_mode = auto`. `tests/` has no `__init__.py`, so import shared helpers as `from conftest import ...`.

## Structure

- `custom_components/ready_by/model.py` holds the maths: session detection, heating and cooling fits, forecast interpolation and the planner. It must stay free of Home Assistant imports, because `scripts/influx_backfill.py` loads it by file path outside Home Assistant.
- `tracker.py` turns climate and sensor states (live or from the recorder) into detector events. `archive.py` records the inputs once a minute to monthly gzip files and replays them as detector events; relearning uses it first and `history.py` (the recorder) only for the time before it.
- `store.py` keeps every learned session and cooling sample (up to 5,000 each), trimming the busiest 2 °C outside-temperature band first so all seasons stay represented. Main storage is a single JSON file rewritten on save, so anything that grows unbounded belongs in the archive, not there.
- `planner.py` holds `ReadyByController`, the entry's `runtime_data`. It owns the settings, the learned data (persisted with `store.py`), the plan, the timers and the climate calls. Entities subscribe with `async_add_listener` and read from it.
- The entity platforms are `datetime.py`, `number.py`, `switch.py`, `sensor.py` and `binary_sensor.py`, all on one service device.
- `translations/en.json` is the only string source. Don't add `strings.json`, which is a Core-only build file.

## Future work

[`docs/roadmap.md`](./docs/roadmap.md) lists the planned follow-ups, with the reasoning behind each and how to validate them. Read it before starting on the model or scheduling.

## Conventions

- Settings (ready-by time, target, enabled) live in the controller's store, not in entity restore state, so there is one source of truth across restarts.
- `recorder` is an `after_dependency`. The history import only runs when it's loaded, so the integration still works without it.
- No comments except where a constraint isn't obvious.
- Commit messages use Conventional Commits. release-please derives versions from them and bumps `manifest.json`. Use `feat:`/`fix:` only for user-visible changes.
- Run markdownlint locally after editing any `.md` file.
- Keep the maintainer's own equipment, entity IDs, readings and home setup out of code, docs, commits and PRs. Use generic examples instead.
