"""Reconstruct recorded execution evidence and validate references between records."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .errors import EvidenceError
from .records import validate_event, validate_manifest
from .oracle_validation import validate_oracle_result

if TYPE_CHECKING:
    from .records import EvidenceEvent, Manifest


def _require(condition: bool, code: str, message: str, field: str | None = None) -> None:
    if not condition:
        raise EvidenceError(code, message, field)


def _same(first: Any, second: Any) -> bool:
    if isinstance(first, bool) or isinstance(second, bool):
        return type(first) is type(second) and first == second
    numbers = (int, float, Decimal)
    if isinstance(first, numbers) and isinstance(second, numbers):
        first_number = Decimal(str(first)) if isinstance(first, float) else Decimal(first)
        second_number = Decimal(str(second)) if isinstance(second, float) else Decimal(second)
        return first_number == second_number
    if isinstance(first, dict) and isinstance(second, dict):
        return first.keys() == second.keys() and all(_same(first[key], second[key]) for key in first)
    if isinstance(first, list) and isinstance(second, list):
        return len(first) == len(second) and all(_same(left, right) for left, right in zip(first, second))
    return type(first) is type(second) and first == second


class ReconstructedEvidence:
    """Validate new Evidence Events and retain the accepted history for inspection.

    Rejected events leave history unchanged. Returned records are independent copies.
    """

    def __init__(self, manifest: dict[str, Any] | Manifest) -> None:
        self._manifest = validate_manifest(manifest)
        self._test_runs: dict[str, dict[str, Any]] = {}
        self._test_sequences: dict[str, dict[str, Any]] = {}
        self._observations: dict[str, dict[str, Any]] = {}
        self._recorded_states: dict[str, dict[str, Any]] = {}
        self._action_descriptions: dict[str, dict[str, Any]] = {}
        self._action_executions: dict[str, dict[str, Any]] = {}
        self._events: dict[str, dict[str, Any]] = {}
        self._oracle_results: list[dict[str, Any]] = []
        self._recorded_transitions: list[dict[str, Any]] = []
        self._latest: dict[str, str] = {}
        self._awaiting: dict[str, str] = {}

    def validate(self, envelope: dict[str, Any] | EvidenceEvent) -> bool:
        """Check without recording; return False for an identical event retry."""
        return self._validate(validate_event(envelope))

    def _validate(self, envelope: dict[str, Any]) -> bool:
        previous = self._events.get(envelope["id"])
        if previous is not None:
            _require(_same(previous, envelope), "conflict", "Event ID has different content")
            return False
        event_type = envelope["event"]["event_type"]
        data = envelope["event"]["data"]
        if event_type == "test_run_started":
            _require(data["id"] not in self._test_runs, "conflict", "Test run ID already exists")
        elif event_type == "test_sequence_started":
            self._validate_sequence(data)
        elif event_type == "action_described":
            previous_action = self._action_descriptions.get(data["id"])
            _require(previous_action is None or _same(previous_action, data),
                     "conflict", "Action description has changed")
        elif event_type == "observation_recorded":
            self._validate_observation(data)
        elif event_type == "action_selected":
            self._validate_selection(data)
        elif event_type == "action_execution_reported":
            record = self._require_execution(data["action_execution"])
            self._require_active_sequence(record["action_execution"]["test_sequence"])
            _require(record["status"] == "awaiting_execution_result", "invalid_lifecycle",
                     "Execution result already reported", "action_execution")
        elif event_type == "transition_recorded":
            self._validate_transition(data)
        elif event_type == "oracle_result_recorded":
            validate_oracle_result(data, self._test_runs, self._test_sequences,
                                   self._observations, self._action_executions)
        elif event_type == "test_sequence_finished":
            self._require_active_sequence(data["test_sequence"])
        elif event_type == "test_run_finished":
            run = self._test_runs.get(data["test_run"])
            _require(run is not None and run["status"] == "running", "invalid_lifecycle",
                     "Test run is not active", "test_run")
            _require(all(sequence["status"] == "finished" for sequence in self._test_sequences.values()
                         if sequence["test_sequence"]["test_run"] == data["test_run"]),
                     "invalid_lifecycle", "Finish the run's sequences first", "test_run")
        return True

    def apply(self, envelope: dict[str, Any] | EvidenceEvent) -> bool:
        """Validate before changing history, preserving accepted payloads and report order."""
        prepared = validate_event(envelope)
        if not self._validate(prepared):
            return False
        return self._apply_prepared(prepared)

    def _apply_prepared(self, prepared: dict[str, Any]) -> bool:
        """Apply an exclusively owned event whose structure and references were checked."""
        event_type = prepared["event"]["event_type"]
        data = prepared["event"]["data"]
        if event_type == "test_run_started":
            self._test_runs[data["id"]] = {"test_run": data, "status": "running", "reason": None}
        elif event_type == "test_sequence_started":
            self._test_sequences[data["id"]] = {"test_sequence": data, "status": "running", "reason": None}
        elif event_type == "action_described":
            self._action_descriptions[data["id"]] = data
        elif event_type == "observation_recorded":
            self._observations[data["id"]] = data
            self._recorded_states[data["recorded_state"]["id"]] = data["recorded_state"]
            self._latest[data["test_sequence"]] = data["id"]
        elif event_type == "action_selected":
            self._action_executions[data["id"]] = {
                "action_execution": data, "status": "awaiting_execution_result",
                "execution_result": None, "resulting_observation": None,
            }
            self._awaiting[data["test_sequence"]] = data["id"]
        elif event_type == "action_execution_reported":
            record = self._action_executions[data["action_execution"]]
            record["execution_result"] = data
            record["status"] = "awaiting_resulting_observation" if data["outcome"] == "returned" else "failed"
            if data["outcome"] == "error":
                self._awaiting.pop(record["action_execution"]["test_sequence"], None)
        elif event_type == "transition_recorded":
            self._record_transition(data)
        elif event_type == "oracle_result_recorded":
            self._oracle_results.append(data)
        elif event_type == "test_sequence_finished":
            sequence = self._test_sequences[data["test_sequence"]]
            sequence["status"] = "finished"
            sequence["reason"] = data["reason"]
            unfinished = self._awaiting.pop(data["test_sequence"], None)
            if unfinished is not None:
                self._action_executions[unfinished]["status"] = "incomplete"
        elif event_type == "test_run_finished":
            run = self._test_runs[data["test_run"]]
            run["status"] = "finished"
            run["reason"] = data["reason"]
        self._events[prepared["id"]] = prepared
        return True

    def _validate_sequence(self, sequence: dict[str, Any]) -> None:
        run = self._test_runs.get(sequence["test_run"])
        _require(run is not None, "invalid_reference", "Unknown parent run", "test_run")
        _require(run["status"] == "running", "invalid_lifecycle", "Parent run finished", "test_run")
        _require(sequence["id"] not in self._test_sequences, "conflict", "Test sequence ID already exists")
        _require(all(record["test_sequence"]["sequence_index"] < sequence["sequence_index"]
                     for record in self._test_sequences.values()
                     if record["test_sequence"]["test_run"] == sequence["test_run"]),
                 "invalid_order", "Sequence indexes must increase within the run", "sequence_index")

    def _validate_observation(self, observation: dict[str, Any]) -> None:
        self._require_active_sequence(observation["test_sequence"])
        _require(observation["id"] not in self._observations, "conflict", "Observation ID already exists")
        latest_id = self._latest.get(observation["test_sequence"])
        _require(latest_id is None or observation["observation_index"] > self._observations[latest_id]["observation_index"],
                 "invalid_order", "Observation index must increase within the sequence", "observation_index")
        previous_state = self._recorded_states.get(observation["recorded_state"]["id"])
        _require(previous_state is None or _same(previous_state, observation["recorded_state"]),
                 "conflict", "Recorded-state content has changed")
        for available in observation["available_actions"]:
            _require(available["action_description"] in self._action_descriptions, "invalid_reference",
                     "Unknown available action description", "available_actions.action_description")

    def _validate_selection(self, execution: dict[str, Any]) -> None:
        self._require_active_sequence(execution["test_sequence"])
        _require(execution["id"] not in self._action_executions, "conflict", "Action execution ID already exists")
        source = self._observations.get(execution["source_observation"])
        _require(source is not None and source["test_sequence"] == execution["test_sequence"]
                 and self._latest.get(execution["test_sequence"]) == source["id"],
                 "invalid_reference", "Source must be the sequence's latest observation", "source_observation")
        _require(any(action["action_description"] == execution["action_description"]
                     and action["target_widget"] == execution["target_widget"] for action in source["available_actions"]),
                 "invalid_reference", "Selected action and target are not available at source", "action_description")
        _require(execution["test_sequence"] not in self._awaiting, "invalid_lifecycle",
                 "Sequence already has an unfinished action execution", "test_sequence")

    def _validate_transition(self, transition: dict[str, Any]) -> None:
        record = self._require_execution(transition["action_execution"])
        execution = record["action_execution"]
        self._require_active_sequence(execution["test_sequence"])
        _require(record["status"] == "awaiting_resulting_observation", "invalid_lifecycle",
                 "Execution is not awaiting a resulting observation", "action_execution")
        source = self._observations[execution["source_observation"]]
        result = self._observations.get(transition["resulting_observation"])
        _require(result is not None and result["test_sequence"] == source["test_sequence"]
                 and result["observation_index"] > source["observation_index"],
                 "invalid_transition", "Result must be later in the same sequence", "resulting_observation")

    def _record_transition(self, transition: dict[str, Any]) -> None:
        record = self._action_executions[transition["action_execution"]]
        execution = record["action_execution"]
        record["status"] = "completed"
        record["resulting_observation"] = transition["resulting_observation"]
        self._awaiting.pop(execution["test_sequence"])
        self._recorded_transitions.append({
            "action_execution": execution["id"], "test_sequence": execution["test_sequence"],
            "source_observation": execution["source_observation"],
            "resulting_observation": transition["resulting_observation"],
            "action_description": execution["action_description"], "target_widget": execution["target_widget"],
        })

    def _require_active_sequence(self, identifier: str) -> None:
        sequence = self._test_sequences.get(identifier)
        _require(sequence is not None, "invalid_reference", "Unknown test sequence", "test_sequence")
        _require(sequence["status"] == "running", "invalid_lifecycle", "Test sequence finished", "test_sequence")

    def _require_observation(self, identifier: str) -> dict[str, Any]:
        observation = self._observations.get(identifier)
        _require(observation is not None, "invalid_reference", "Unknown observation", "observation")
        return observation

    def _require_execution(self, identifier: str) -> dict[str, Any]:
        execution = self._action_executions.get(identifier)
        _require(execution is not None, "invalid_reference", "Unknown action execution", "action_execution")
        return execution

    @property
    def manifest(self) -> dict[str, Any]:
        return deepcopy(self._manifest)

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def events(self) -> list[dict[str, Any]]:
        return deepcopy(list(self._events.values()))

    @property
    def test_runs(self) -> dict[str, dict[str, Any]]:
        return deepcopy(dict(sorted(self._test_runs.items())))

    @property
    def test_sequences(self) -> dict[str, dict[str, Any]]:
        return deepcopy(dict(sorted(self._test_sequences.items())))

    @property
    def observations(self) -> dict[str, dict[str, Any]]:
        return deepcopy(dict(sorted(self._observations.items())))

    @property
    def recorded_states(self) -> dict[str, dict[str, Any]]:
        return deepcopy(dict(sorted(self._recorded_states.items())))

    @property
    def action_descriptions(self) -> dict[str, dict[str, Any]]:
        return deepcopy(dict(sorted(self._action_descriptions.items())))

    @property
    def action_executions(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self._action_executions)

    @property
    def oracle_results(self) -> list[dict[str, Any]]:
        return deepcopy(self._oracle_results)

    @property
    def recorded_transitions(self) -> list[dict[str, Any]]:
        return deepcopy(self._recorded_transitions)

    def test_run(self, identifier: str) -> dict[str, Any] | None:
        return deepcopy(self._test_runs.get(identifier))

    def test_sequence(self, identifier: str) -> dict[str, Any] | None:
        return deepcopy(self._test_sequences.get(identifier))

    def observation(self, identifier: str) -> dict[str, Any] | None:
        return deepcopy(self._observations.get(identifier))

    def recorded_state(self, identifier: str) -> dict[str, Any] | None:
        return deepcopy(self._recorded_states.get(identifier))

    def action_description(self, identifier: str) -> dict[str, Any] | None:
        return deepcopy(self._action_descriptions.get(identifier))

    def action_execution(self, identifier: str) -> dict[str, Any] | None:
        return deepcopy(self._action_executions.get(identifier))

    def test_sequences_for_run(self, test_run: str) -> list[dict[str, Any]]:
        records = (value for value in self._test_sequences.values() if value["test_sequence"]["test_run"] == test_run)
        return deepcopy(sorted(records, key=lambda value: value["test_sequence"]["sequence_index"]))

    def observations_for_sequence(self, test_sequence: str) -> list[dict[str, Any]]:
        records = (value for value in self._observations.values() if value["test_sequence"] == test_sequence)
        return deepcopy(sorted(records, key=lambda value: value["observation_index"]))

    def observations_for_recorded_state(self, recorded_state: str) -> list[dict[str, Any]]:
        return deepcopy([self._observations[identifier] for identifier in sorted(self._observations)
                         if self._observations[identifier]["recorded_state"]["id"] == recorded_state])

    def executions_for_sequence(self, test_sequence: str) -> list[dict[str, Any]]:
        return deepcopy([value for value in self._action_executions.values()
                         if value["action_execution"]["test_sequence"] == test_sequence])

    def available_actions(self, observation: str) -> list[dict[str, Any]]:
        return deepcopy(self._require_observation(observation)["available_actions"])

    def outgoing(self, observation: str) -> list[dict[str, Any]]:
        return deepcopy([value for value in self._recorded_transitions if value["source_observation"] == observation])

    def incoming(self, observation: str) -> list[dict[str, Any]]:
        return deepcopy([value for value in self._recorded_transitions if value["resulting_observation"] == observation])

    def snapshot(self) -> ReconstructedEvidence:
        """Return an independent copy of the accepted history."""
        return deepcopy(self)

    def summary(self) -> dict[str, Any]:
        """Describe accepted records using the shared conformance summary format."""
        return deepcopy({
            "status": "accepted",
            "counts": {
                "evidence_events": self.event_count, "test_runs": len(self._test_runs),
                "test_sequences": len(self._test_sequences), "observations": len(self._observations),
                "action_descriptions": len(self._action_descriptions), "action_executions": len(self._action_executions),
                "recorded_transitions": len(self._recorded_transitions), "oracle_results": len(self._oracle_results),
                "artifact_references": sum(len(value["artifacts"]) for value in self._observations.values()),
                "recorded_states": len(self._recorded_states),
            },
            "test_runs": [{"id": identifier, "status": record["status"]}
                          for identifier, record in sorted(self._test_runs.items())],
            "test_sequences": [{"id": identifier, "test_run": record["test_sequence"]["test_run"],
                                "sequence_index": record["test_sequence"]["sequence_index"], "status": record["status"]}
                               for identifier, record in sorted(self._test_sequences.items())],
            "observations": [self._observation_summary(value) for identifier, value in sorted(self._observations.items())],
            "action_descriptions": [{"id": identifier, "action_type": value["action_type"]}
                                    for identifier, value in sorted(self._action_descriptions.items())],
            "action_executions": list(self._action_executions.values()),
            "recorded_transitions": self._recorded_transitions, "oracle_results": self._oracle_results,
            "recorded_states": sorted(self._recorded_states),
        })

    @staticmethod
    def _observation_summary(observation: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": observation["id"], "test_sequence": observation["test_sequence"],
            "observation_index": observation["observation_index"], "recorded_state": observation["recorded_state"]["id"],
            "capture": observation["recorded_state"]["capture"],
            "available_action_scope": observation["available_action_scope"],
            "widget_ids": [value["id"] for value in observation["recorded_state"]["widgets"]],
            "available_actions": observation["available_actions"],
            "artifact_ids": [value["id"] for value in observation["artifacts"]],
        }
