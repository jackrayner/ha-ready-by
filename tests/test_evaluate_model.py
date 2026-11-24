import importlib.util
import json
from pathlib import Path

from test_model import _cold_sessions

SCRIPT = Path(__file__).parent.parent / "scripts" / "evaluate_model.py"
spec = importlib.util.spec_from_file_location("evaluate_model", SCRIPT)
evaluate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluate)


def test_reports_forms_and_choice(tmp_path, capsys):
    sessions = [s.as_dict() for s in _cold_sessions(40, knee=2.0, penalty=0.06)]
    export = tmp_path / "export.json"
    export.write_text(json.dumps({"sessions": sessions, "cooling": []}))
    assert evaluate.main([str(export)]) == 0
    out = capsys.readouterr().out
    assert "linear:" in out and "knee   2.0 °C" in out
    assert "chosen: cold" in out


def test_reads_diagnostics(tmp_path, capsys):
    sessions = [s.as_dict() for s in _cold_sessions(20, knee=2.0, penalty=0.0)]
    diag = tmp_path / "diag.json"
    diag.write_text(json.dumps({"data": {"data": {"sessions": sessions}}}))
    assert evaluate.main([str(diag)]) == 0
    assert "chosen: full" in capsys.readouterr().out
