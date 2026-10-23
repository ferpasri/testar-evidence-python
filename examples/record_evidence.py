"""Record a demonstration state-action-state trace in its own named directory."""

import argparse
from datetime import datetime
from pathlib import Path
import re
from uuid import uuid4

from testar_evidence import (
    ActionDescription, ActionExecution, ActionExecutionResult, AvailableAction,
    EvidenceEvent, EvidenceReader, JsonlStore, Manifest, Observation, ObservedValue,
    Oracle, OracleResult, OracleTarget, RecordedState, RecordedTransition,
    TestRun, TestSequence, Widget,
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
    sequence = run + ":sequence:1"
    action = run + ":action:submit"
    reset = run + ":action:reset"
    before = sequence + ":observation:1"
    after = sequence + ":observation:2"
    execution = sequence + ":execution:1"

    with JsonlStore.create(directory, Manifest("example:evidence:" + identity)) as store:
        counter = 0

        def record(event_type, data):
            nonlocal counter
            counter += 1
            store.record(EvidenceEvent(run + ":event:" + str(counter), event_type, data))

        # A run can contain several sequences; this demonstration records one.
        record("test_run_started", TestRun(run, "example", identity, properties=properties))
        record("test_sequence_started", TestSequence(sequence, run, 1, "1"))
        record("action_described", ActionDescription(action, "click"))
        record("action_described", ActionDescription(reset, "click"))

        # Adapters supply captured widgets and available actions, including unexecuted choices.
        initial = RecordedState(sequence + ":state:1", {"ui.title": "Form"}, [
            Widget("submit", properties={"ui.role": "button", "ui.text": "Send"}),
            Widget("reset", properties={"ui.role": "button", "ui.text": "Reset"}),
        ])
        record("observation_recorded", Observation(before, sequence, 1, 0, initial, "generated",
            available_actions=[AvailableAction(action, "submit"), AvailableAction(reset, "reset")]))
        record("action_selected", ActionExecution(execution, sequence, before, action, "submit"))
        record("action_execution_reported", ActionExecutionResult(execution, "returned"))

        # A returned operation and its resulting observation are recorded separately.
        result = RecordedState(sequence + ":state:2", {"ui.title": "Result"}, [
            Widget("message", properties={"ui.text": "Sent"}),
        ])
        record("observation_recorded", Observation(after, sequence, 2, 1, result, "unknown"))
        record("transition_recorded", RecordedTransition(execution, after))

        # Capturing text and evaluating that text are separate evidence records.
        record("oracle_result_recorded", OracleResult(run, "Confirmation text is Sent",
            Oracle("example:confirmation", "1"),
            OracleTarget.widget_property(after, "message", "ui.text"), "passed",
            test_sequence=sequence, expected_value="Sent", observed_value=ObservedValue.value("Sent")))
        record("test_sequence_finished", {"test_sequence": sequence, "reason": "completed"})
        record("test_run_finished", {"test_run": run, "reason": "completed"})
        print("Recorded", store.evidence.event_count, "Evidence Events in", directory.resolve())

    evidence = EvidenceReader.read(directory)
    print("Recorded transitions:", len(evidence.recorded_transitions))
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
