"""A compact, long-lived archive of the inputs Ready By learns from.

Once a minute it records the water and outside temperatures, the heater's raw
power (in watts), whether it was heating and its mode. Rows are appended as
gzip members to one file per month, so a year is about 5–10 MB. Unlike the
recorder, it isn't purged after days, so the model can be relearned from a
full year (for example after a detection setting changes).

Each appended chunk starts with a header naming the source entities, so rows
recorded from other entities are never mixed into a relearn.
"""

from __future__ import annotations

import gzip
import json
import logging
import shutil
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

FILE_PREFIX = "inputs-"
FILE_SUFFIX = ".jsonl.gz"


@dataclass(frozen=True)
class Row:
    when: datetime
    water: float | None
    outside: float | None
    power: float | None
    heating: bool | None
    mode: str | None

    def encode(self) -> list[Any]:
        return [
            int(self.when.timestamp()),
            self.water,
            self.outside,
            self.power,
            None if self.heating is None else int(self.heating),
            self.mode,
        ]

    @classmethod
    def decode(cls, raw: list[Any]) -> Row:
        when, water, outside, power, heating, mode = raw
        return cls(
            when=datetime.fromtimestamp(when, tz=UTC),
            water=water,
            outside=outside,
            power=power,
            heating=None if heating is None else bool(heating),
            mode=mode,
        )


def events_from_rows(
    rows: Iterable[Row], *, power_threshold: float, use_power: bool
) -> list[tuple[datetime, str, Any]]:
    """Detector events from archived rows.

    With use_power, heating is re-derived from the raw power and the current
    threshold, so a changed threshold relearns correctly. Otherwise the heating
    flag recorded at the time is used.
    """
    events: list[tuple[datetime, str, Any]] = []
    for row in rows:
        events.append((row.when, "water", row.water))
        events.append((row.when, "outside", row.outside))
        events.append((row.when, "mode", row.mode))
        if use_power:
            if row.power is not None:
                events.append((row.when, "heating", row.power > power_threshold))
        elif row.heating is not None:
            events.append((row.when, "heating", row.heating))
    return events


class InputArchive:
    """Blocking file I/O; call the methods from an executor."""

    def __init__(self, directory: Path, limit_bytes: int) -> None:
        self.directory = directory
        self.limit_bytes = limit_bytes

    def _path(self, when: datetime) -> Path:
        return self.directory / f"{FILE_PREFIX}{when:%Y-%m}{FILE_SUFFIX}"

    def files(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(self.directory.glob(f"{FILE_PREFIX}*{FILE_SUFFIX}"))

    def size(self) -> int:
        return sum(p.stat().st_size for p in self.files())

    def append(self, sources: dict[str, str | None], rows: list[Row]) -> None:
        """Append rows, grouped by month, each group as a gzip member with a header."""
        if not rows:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        by_month: dict[Path, list[Row]] = {}
        for row in rows:
            by_month.setdefault(self._path(row.when), []).append(row)
        header = json.dumps({"sources": sources}, separators=(",", ":"))
        for path, month_rows in by_month.items():
            lines = [header] + [json.dumps(r.encode(), separators=(",", ":")) for r in month_rows]
            with gzip.open(path, "at", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        self.prune()

    def prune(self) -> None:
        """Delete the oldest months while the archive is over its size limit."""
        files = self.files()
        total = sum(p.stat().st_size for p in files)
        while len(files) > 1 and total > self.limit_bytes:
            oldest = files.pop(0)
            total -= oldest.stat().st_size
            oldest.unlink()
            _LOGGER.info("Ready By archive over its size limit; removed %s", oldest.name)

    def read(self, sources: dict[str, str | None]) -> list[Row]:
        """Every archived row recorded from exactly these source entities, oldest first."""
        rows: list[Row] = []
        for path in self.files():
            current: dict[str, Any] | None = None
            try:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    for line in f:
                        record = json.loads(line)
                        if isinstance(record, dict):
                            current = record.get("sources")
                        elif current == sources:
                            rows.append(Row.decode(record))
            except (EOFError, OSError, zlib.error, json.JSONDecodeError, ValueError) as err:
                # A write cut short (e.g. power loss) leaves a damaged tail; keep what came before.
                _LOGGER.warning("Stopped reading %s at a damaged entry: %s", path.name, err)
        rows.sort(key=lambda r: r.when)
        return rows

    def remove(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)
