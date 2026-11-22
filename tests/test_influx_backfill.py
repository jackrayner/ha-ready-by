import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parent.parent / "scripts" / "influx_backfill.py"
spec = importlib.util.spec_from_file_location("influx_backfill", SCRIPT)
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)

T0 = 1_793_534_400
HOUR = 3600


def _response(rows_by_series):
    series = [
        {"name": name, "columns": ["time", *cols], "values": values}
        for name, cols, values in rows_by_series
    ]
    return io.BytesIO(json.dumps({"results": [{"series": series}]}).encode())


def _fake_influx(request, timeout):
    q = request.full_url
    if "hot_tub%27" in q:
        return _response(
            [
                (
                    "state",
                    ["state", "hvac_action_str", "current_temperature"],
                    [
                        [T0, "heat", "idle", 36],
                        [T0 + 6 * HOUR, "heat", "heating", 33],
                        [T0 + 8 * HOUR, "heat", "idle", 37],
                    ],
                )
            ]
        )
    if "hot_tub_water" in q:
        return _response([("°C", ["value"], [[T0, 36], [T0 + 6 * HOUR, 33], [T0 + 8 * HOUR, 37]])])
    return _response([("°C", ["value"], [[T0, 5]])])


def test_climate_heating_mapping():
    assert backfill.climate_heating({"state": "heat", "hvac_action_str": "heating"})
    assert not backfill.climate_heating({"state": "heat", "hvac_action_str": "idle"})
    assert backfill.climate_heating({"state": "unavailable"}) is None
    assert backfill.climate_heating({"state": "heat", "current_temperature": 30, "temperature": 38})


def test_main_outputs_sessions(capsys, monkeypatch):
    monkeypatch.setenv("INFLUX_USERNAME", "ha")
    monkeypatch.setenv("INFLUX_PASSWORD", "secret")
    with patch.object(backfill.urllib.request, "urlopen", side_effect=_fake_influx) as urlopen:
        assert (
            backfill.main(
                [
                    "--climate",
                    "climate.hot_tub",
                    "--water",
                    "sensor.hot_tub_water",
                    "--outside",
                    "sensor.outside",
                ]
            )
            == 0
        )
    assert urlopen.call_args.args[0].get_header("Authorization").startswith("Basic ")
    out = json.loads(capsys.readouterr().out)
    assert len(out["sessions"]) == 1
    assert out["sessions"][0]["water_start"] == 33
    assert out["sessions"][0]["water_end"] == 37
    assert len(out["cooling"]) == 1


def test_main_without_water_sensor(capsys):
    with patch.object(backfill.urllib.request, "urlopen", side_effect=_fake_influx):
        backfill.main(["--climate", "climate.hot_tub", "--outside", "sensor.outside"])
    out = json.loads(capsys.readouterr().out)
    assert out["sessions"][0]["water_end"] == 37
