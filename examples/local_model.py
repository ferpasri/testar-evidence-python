"""Record a demonstration state-action-state trace in its own named directory."""

import argparse
from datetime import datetime
from pathlib import Path
import re
from uuid import uuid4

from testar_evidence import (
    ActionCandidate,
    ActionDefinition,
    CheckReading,
    CheckResult,
    CheckRule,
    CheckTarget,
    ConcreteState,
    EvidenceEvent,
    Execution,
    JsonlStore,
    Manifest,
    Observation,
    Run,
    Widget,
)


def _name_part(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-_")
    return cleaned[:32].rstrip("-_") or fallback


def create_trace_directory(parent: Path, sut: str, test_objective: str | None) -> Path:
    """Reserve a new directory, even if two runs have the same timestamp."""
    parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]
    name = f"{_name_part(sut, 'sut')}_{_name_part(test_objective if test_objective is not None else 'run', 'objective')}_trace-{timestamp}"
    suffix = 1
    while True:
        directory = parent / (name if suffix == 1 else f"{name}-{suffix}")
        try:
            directory.mkdir()
            return directory
        except FileExistsError:
            suffix += 1


def main(parent: Path, sut: str = "demo", test_objective: str | None = None,
         origin: str | None = None) -> Path:
    properties = {"example.sut": sut}
    for key, value in (("evidence.origin", origin), ("evidence.test_objective", test_objective)):
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Run descriptions must be nonblank strings")
            properties[key] = value
    directory = create_trace_directory(parent, sut, test_objective)
    identity = uuid4().hex
    run = "example:run:" + identity
    action = run + ":action:submit"
    before = run + ":observation:1"
    after = run + ":observation:2"
    execution = run + ":execution:1"

    with JsonlStore.create(directory, Manifest("example:model:" + identity)) as store:
        counter = 0

        def record(kind, data):
            nonlocal counter
            counter += 1
            store.record(EvidenceEvent(run + ":event:" + str(counter), kind, data))

        record("run_started", Run(run, "example", identity,
            properties=properties))
        record("action_defined", ActionDefinition(action, "click"))

        # Adapters supply the captured widgets and the candidates they discovered.
        initial = ConcreteState(run + ":snapshot:1", {"ui.title": "Form"}, [
            Widget("submit", properties={"ui.role": "button", "ui.text": "Send"}),
        ])
        record("observed", Observation(before, run, 1, 0, initial,
            candidates=[ActionCandidate(action, "submit")], candidate_scope="generated"))
        record("action_selected", Execution(execution, run, before, action, "submit"))
        record("action_submitted", {"execution": execution, "accepted": True, "error": None})

        result = ConcreteState(run + ":snapshot:2", {"ui.title": "Result"}, [
            Widget("message", properties={"ui.text": "Sent"}),
        ])
        record("observed", Observation(after, run, 2, 1, result))
        record("action_result", {"execution": execution, "observation": after})

        # Capturing text and evaluating that text are separate evidence records.
        record("checked", CheckResult(run, "Confirmation text is Sent",
            CheckRule("example:confirmation", "1"),
            CheckTarget.widget_property(after, "message", "ui.text"), "passed",
            expected="Sent", actual=CheckReading.value("Sent")))
        record("run_finished", {"run": run, "reason": "completed"})
        print("Recorded", store.model.event_count, "events in", directory.resolve())

    with JsonlStore.open(directory) as reopened:
        print("Completed action links:", len(reopened.model.links()))
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent", nargs="?", type=Path, default=Path("target/traces"),
        help="Parent directory for the new trace")
    parser.add_argument("--sut", default="demo", help="Application name used to label the trace")
    parser.add_argument("--test-objective", help="What this run intends to investigate or verify")
    parser.add_argument("--origin", help="Tool, script, or integration supplying the execution")
    arguments = parser.parse_args()
    main(arguments.parent, arguments.sut, arguments.test_objective, arguments.origin)
