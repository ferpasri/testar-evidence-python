"""Shared-case loading; every example comes from the prepared specs release."""

from pathlib import Path

from testar_evidence.codec import dumps, loads
from tools.prepare_conformance import load_prepared


REPOSITORY = Path(__file__).resolve().parents[1]


def conformance_cases() -> list[Path]:
    root = load_prepared(REPOSITORY)
    return sorted(path for path in (root / "conformance" / "cases").iterdir() if path.is_dir())


def read_json(path: Path):
    return loads(path.read_text(encoding="utf-8"))


def read_events(directory: Path) -> list[dict]:
    events = []
    for line in (directory / "evidence.jsonl").read_text(encoding="utf-8").splitlines():
        event = loads(line)
        if loads(dumps(event)) != event:
            raise AssertionError("Every input event field must survive JSON serialization")
        events.append(event)
    return events
