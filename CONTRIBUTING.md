# Contributing

This is a small, solo-maintained project. Clone it, make your change, and open a PR.

## Setup and checks

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt ruff
python3 -m pytest
ruff check . && ruff format --check .
```

`pytest-homeassistant-custom-component` installs a full Home Assistant core, so the first install is slow. On Windows, use WSL2 or the Dev Container in `.devcontainer/`.

The maths in `custom_components/ready_by/model.py` is pure Python and is tested directly in `tests/test_model.py`. Everything else is tested against the `hass` fixture, with climate services mocked by `async_mock_service`. See `tests/conftest.py`.

## Commit messages

[release-please](https://github.com/googleapis/release-please) cuts releases from [Conventional Commits](https://www.conventionalcommits.org/) on `main`:

- `feat:` gives a minor bump.
- `fix:` gives a patch bump.
- `feat!:`, or a `BREAKING CHANGE:` footer, gives a major bump.
- `docs:`, `chore:`, `test:`, `refactor:` and `ci:` give no bump.

If a change alters behaviour a user would notice, use `feat:` or `fix:`.
