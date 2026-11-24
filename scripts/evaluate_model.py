#!/usr/bin/env python3
"""Compare Ready By's heating model forms on learned sessions.

Reads the JSON returned by the ready_by.export_sessions action (or a Ready By
diagnostics download) and prints the cross-validated error of the linear fit
and of the cold-weather form at each candidate knee, plus the form the
integration would choose. Standard library only.

Example:
    python3 scripts/evaluate_model.py sessions.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parent.parent / "custom_components" / "ready_by" / "model.py"


def load_model():
    spec = importlib.util.spec_from_file_location("ready_by_model", MODEL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sessions_from(payload: dict) -> list[dict]:
    """Sessions from an export_sessions response or a diagnostics download."""
    if "sessions" in payload:
        return payload["sessions"]
    return payload["data"]["data"]["sessions"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="export_sessions or diagnostics JSON")
    args = parser.parse_args(argv)
    model = load_model()
    raw = sessions_from(json.loads(args.path.read_text()))
    sessions = [model.Session.from_dict(s) for s in raw]
    valid = [s for s in sessions if s.is_valid()]
    kept = model._reject_outliers(valid) if len(valid) >= model.SIMPLE_FIT_MIN_SESSIONS else valid
    outside = [s.outside_avg for s in kept]
    print(f"{len(valid)} valid sessions, {len(kept)} after outliers")
    if outside:
        print(f"outside temperature {min(outside):.1f} to {max(outside):.1f} °C")

    linear = model.cross_validate(kept, model.linear_fit) if len(kept) >= 2 else None
    print(f"linear: {linear:.2f} h" if linear is not None else "linear: can't be fitted")
    for knee in model.COLD_KNEES:
        below = sum(t < knee for t in outside)
        error = model.cross_validate(kept, lambda train, knee=knee: model.cold_fit(train, knee))
        shown = f"{error:.2f} h" if error is not None else "can't be fitted"
        print(f"knee {knee:5.1f} °C ({below} below, {len(outside) - below} above): {shown}")

    chosen = model.fit_heating(sessions, seed_rate=1.0)
    knee = f", knee {chosen.knee} °C" if chosen.knee is not None else ""
    print(f"chosen: {chosen.kind}{knee}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
