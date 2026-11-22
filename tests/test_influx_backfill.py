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


def test_build_events_with_power():
    rows_climate = [{"time": T0, "state": "heat", "hvac_action_str": "heating"}]
    rows_power = [
        {"time": T0, "value": 10},
        {"time": T0 + HOUR, "value": 1500},
        {"time": T0 + 3 * HOUR, "value": 5},
    ]
    rows_water = [{"time": T0, "value": 30}, {"time": T0 + 3 * HOUR, "value": 34}]
    rows_outside = [{"time": T0, "value": 5}]
    events = backfill.build_events(rows_climate, rows_water, rows_outside, rows_power, 100)
    heating = [(e[0].timestamp(), e[2]) for e in events if e[1] == "heating"]
    assert heating == [(T0, False), (T0 + HOUR, True), (T0 + 3 * HOUR, False)]
    sessions, _ = backfill.load_model().detect(events)
    assert sessions[0].rate == 2.0


def test_power_rows_in_kilowatts():
    rows_power = [
        {"time": T0, "value": 0.05, "_measurement": "kW"},
        {"time": T0 + HOUR, "value": 1.4, "_measurement": "kW"},
    ]
    events = backfill.build_events([], [], [], rows_power, 100)
    assert [e[2] for e in events if e[1] == "heating"] == [False, True]


def test_climate_rows_carry_mode():
    rows_climate = [
        {"time": T0, "state": "heat", "hvac_action_str": "heating", "preset_mode_str": "boost"}
    ]
    events = backfill.build_events(rows_climate, [], [])
    assert (events[0][1], events[0][2]) == ("mode", "boost")
