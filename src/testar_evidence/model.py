"""Reconstruct concrete execution evidence and validate references between records."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .codec import to_data
from .errors import EvidenceError
from .records import validate_event, validate_manifest

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


class EvidenceModel:
    """Concrete observations, reported evaluations, and explicitly recorded action links.

    Applying a rejected event leaves the model unchanged. Query results are independent
    copies so callers cannot change accepted history through a returned dictionary.
    """

    def __init__(self, manifest: dict[str, Any] | Manifest) -> None:
        self._manifest = validate_manifest(to_data(manifest))
        self._runs: dict[str, dict[str, Any]] = {}
        self._observations: dict[str, dict[str, Any]] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._actions: dict[str, dict[str, Any]] = {}
        self._executions: dict[str, dict[str, Any]] = {}
        self._events: dict[str, dict[str, Any]] = {}
        self._checks: list[dict[str, Any]] = []
        self._latest: dict[str, str] = {}
        self._pending: dict[str, str] = {}

    def validate(self, envelope: dict[str, Any] | EvidenceEvent) -> bool:
        """Check an event without recording it; return False for an identical retry."""
        return self._validate(validate_event(to_data(envelope)))

    def _validate(self, envelope: dict[str, Any]) -> bool:
        previous = self._events.get(envelope["id"])
        if previous is not None:
            _require(_same(previous, envelope), "conflict", "Event ID has different content")
            return False
        event = envelope["event"]
        data = event["data"]
        kind = event["kind"]
        if kind == "run_started":
            _require(data["id"] not in self._runs, "conflict", "Run ID already exists")
        elif kind == "action_defined":
            previous_action = self._actions.get(data["id"])
            _require(previous_action is None or _same(previous_action, data),
                     "conflict", "Action definition has changed")
        elif kind == "observed":
            self._validate_observation(data)
        elif kind == "action_selected":
            self._validate_selection(data)
        elif kind == "action_submitted":
            record = self._require_execution(data["execution"])
            self._require_running(record["execution"]["run"])
            _require(record["status"] == "selected", "conflict",
                     "Execution is not awaiting submission")
        elif kind == "action_result":
            self._validate_result(data)
        elif kind == "checked":
            _require(data["run"] in self._runs, "unknown_reference", "Unknown evaluation run")
            self._validate_check(data)
        elif kind == "run_finished":
            self._require_running(data["run"])
        return True

    def apply(self, envelope: dict[str, Any] | EvidenceEvent) -> bool:
        """Record a valid event once, preserving its original payload and report order."""
        prepared = validate_event(to_data(envelope))
        if not self._validate(prepared):
            return False
        event = prepared["event"]
        data = event["data"]
        kind = event["kind"]
        if kind == "run_started":
            self._runs[data["id"]] = {"run": data, "status": "running", "reason": None}
        elif kind == "action_defined":
            self._actions[data["id"]] = data
        elif kind == "observed":
            self._observations[data["id"]] = data
            self._snapshots[data["state"]["id"]] = data["state"]
            self._latest[data["run"]] = data["id"]
        elif kind == "action_selected":
            self._executions[data["id"]] = {
                "execution": data, "status": "selected", "result": None, "error": None
            }
            self._pending[data["run"]] = data["id"]
        elif kind == "action_submitted":
            record = self._executions[data["execution"]]
            record["status"] = "submitted" if data["accepted"] else "rejected"
            record["error"] = data.get("error")
            if not data["accepted"]:
                self._pending.pop(record["execution"]["run"], None)
        elif kind == "action_result":
            record = self._executions[data["execution"]]
            record["status"] = "observed"
            record["result"] = data["observation"]
            record["error"] = None
            self._pending.pop(record["execution"]["run"], None)
        elif kind == "checked":
            self._checks.append(data)
        elif kind == "run_finished":
            run = self._runs[data["run"]]
            run["status"] = "finished"
            run["reason"] = data["reason"]
            pending = self._pending.pop(data["run"], None)
            if pending is not None:
                self._executions[pending]["status"] = "incomplete"
        self._events[prepared["id"]] = prepared
        return True

    def _validate_observation(self, observation: dict[str, Any]) -> None:
        self._require_running(observation["run"])
        _require(observation["id"] not in self._observations,
                 "conflict", "Observation ID already exists")
        latest_id = self._latest.get(observation["run"])
        if latest_id is not None:
            _require(observation["sequence"] > self._observations[latest_id]["sequence"],
                     "invalid_record", "Observation sequence must increase within its run")
        previous_state = self._snapshots.get(observation["state"]["id"])
        _require(previous_state is None or _same(previous_state, observation["state"]),
                 "conflict", "Snapshot content has changed")
        for candidate in observation["candidates"]:
            _require(candidate["action"] in self._actions,
                     "unknown_reference", "Unknown candidate action")

    def _validate_selection(self, execution: dict[str, Any]) -> None:
        self._require_running(execution["run"])
        _require(execution["id"] not in self._executions, "conflict", "Execution ID already exists")
        source = self._require_observation(execution["source"])
        _require(source["run"] == execution["run"], "invalid_record",
                 "Execution source belongs to another run")
        _require(self._latest.get(execution["run"]) == source["id"], "invalid_record",
                 "Execution source must be the latest observation")
        candidate_found = any(candidate["action"] == execution["action"]
                              and candidate.get("target") == execution.get("target")
                              for candidate in source["candidates"])
        _require(candidate_found, "unknown_reference",
                 "Selected action and target are not a source candidate")
        _require(execution["run"] not in self._pending,
                 "conflict", "Run already has a pending action")

    def _validate_result(self, result: dict[str, Any]) -> None:
        record = self._require_execution(result["execution"])
        self._require_running(record["execution"]["run"])
        _require(record["status"] == "submitted", "conflict", "Execution is not awaiting a result")
        source = self._require_observation(record["execution"]["source"])
        target = self._require_observation(result["observation"])
        _require(target["run"] == source["run"] and target["sequence"] > source["sequence"],
                 "invalid_record", "Result must be a later observation in the same run")

    def _validate_check(self, check: dict[str, Any]) -> None:
        target = check["target"]
        kind = target["kind"]
        if kind in {"observation", "state_property", "widget", "widget_property"}:
            observation = self._check_observation(target["observation"], check["run"],
                                                  "target.observation")
            if kind in {"widget", "widget_property"}:
                self._check_reference(any(widget["id"] == target["widget"]
                                          for widget in observation["state"]["widgets"]),
                                      "target.widget")
        elif kind == "execution":
            execution = self._executions.get(target["execution"])
            self._check_reference(execution is not None and execution["execution"]["run"] == check["run"],
                                  "target.execution")
        elif kind == "sequence":
            start = self._check_observation(target["from_observation"], check["run"],
                                           "target.from_observation")
            end = self._check_observation(target["to_observation"], check["run"],
                                         "target.to_observation")
            self._check_reference(end["sequence"] >= start["sequence"], "target.to_observation")

    def _check_observation(self, identifier: str, run: str, field: str) -> dict[str, Any]:
        observation = self._observations.get(identifier)
        self._check_reference(observation is not None and observation["run"] == run, field)
        return observation

    @staticmethod
    def _check_reference(condition: bool, field: str) -> None:
        _require(condition, "invalid_check_target", "Evaluation has an invalid reference: " + field, field)

    def _require_running(self, identifier: str) -> None:
        run = self._runs.get(identifier)
        _require(run is not None, "unknown_reference", "Unknown run: " + identifier)
        _require(run["status"] == "running", "conflict", "Run has finished: " + identifier)

    def _require_observation(self, identifier: str) -> dict[str, Any]:
        observation = self._observations.get(identifier)
        _require(observation is not None, "unknown_reference", "Unknown observation: " + identifier)
        return observation

    def _require_execution(self, identifier: str) -> dict[str, Any]:
        execution = self._executions.get(identifier)
        _require(execution is not None, "unknown_reference", "Unknown execution: " + identifier)
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
    def runs(self) -> dict[str, dict[str, Any]]:
        return deepcopy(dict(sorted(self._runs.items())))

    @property
    def observations(self) -> dict[str, dict[str, Any]]:
        return deepcopy(dict(sorted(self._observations.items())))

    @property
    def snapshots(self) -> dict[str, dict[str, Any]]:
        return deepcopy(dict(sorted(self._snapshots.items())))

    @property
    def actions(self) -> dict[str, dict[str, Any]]:
        return deepcopy(dict(sorted(self._actions.items())))

    @property
    def executions(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self._executions)

    @property
    def checks(self) -> list[dict[str, Any]]:
        return deepcopy(self._checks)

    def run(self, identifier: str) -> dict[str, Any] | None:
        return deepcopy(self._runs.get(identifier))

    def observation(self, identifier: str) -> dict[str, Any] | None:
        return deepcopy(self._observations.get(identifier))

    def action(self, identifier: str) -> dict[str, Any] | None:
        return deepcopy(self._actions.get(identifier))

    def execution(self, identifier: str) -> dict[str, Any] | None:
        return deepcopy(self._executions.get(identifier))

    def observations_for_run(self, run: str) -> list[dict[str, Any]]:
        observations = (value for value in self._observations.values() if value["run"] == run)
        return deepcopy(sorted(observations, key=lambda value: value["sequence"]))

    def observations_for_snapshot(self, snapshot: str) -> list[dict[str, Any]]:
        return deepcopy([self._observations[identifier] for identifier in sorted(self._observations)
                         if self._observations[identifier]["state"]["id"] == snapshot])

    def executions_for_run(self, run: str) -> list[dict[str, Any]]:
        return deepcopy([value for value in self._executions.values() if value["execution"]["run"] == run])

    def candidates(self, observation: str) -> list[dict[str, Any]]:
        return deepcopy(self._require_observation(observation)["candidates"])

    def links(self) -> list[dict[str, str]]:
        return [
            {"execution": record["execution"]["id"], "run": record["execution"]["run"],
             "source": record["execution"]["source"], "action": record["execution"]["action"],
             "target": record["result"]}
            for record in self._executions.values() if record["result"] is not None
        ]

    def outgoing(self, observation: str) -> list[dict[str, str]]:
        return [link for link in self.links() if link["source"] == observation]

    def incoming(self, observation: str) -> list[dict[str, str]]:
        return [link for link in self.links() if link["target"] == observation]

    def snapshot(self) -> EvidenceModel:
        """Return an independent model containing the accepted history so far."""
        return deepcopy(self)

    def summary(self) -> dict[str, Any]:
        """Describe the accepted records using the shared conformance summary format."""
        result = {
            "status": "accepted",
            "counts": {
                "events": self.event_count, "runs": len(self._runs),
                "observations": len(self._observations), "actions": len(self._actions),
                "executions": len(self._executions), "checks": len(self._checks),
                "artifact_references": sum(len(value["artifacts"]) for value in self._observations.values()),
                "concrete_states": len(self._snapshots),
            },
            "runs": [{"id": identifier, "status": self._runs[identifier]["status"]}
                     for identifier in sorted(self._runs)],
            "observations": [self._observation_summary(self._observations[identifier])
                             for identifier in sorted(self._observations)],
            "actions": [{"id": identifier, "kind": self._actions[identifier]["kind"]}
                        for identifier in sorted(self._actions)],
            "executions": list(self._executions.values()),
            "checks": self._checks,
            "concrete_states": sorted(self._snapshots),
        }
        return deepcopy(result)

    @staticmethod
    def _observation_summary(observation: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": observation["id"], "run": observation["run"], "sequence": observation["sequence"],
            "concrete_state": observation["state"]["id"], "capture": observation["state"]["capture"],
            "candidate_scope": observation.get("candidate_scope", "unknown"),
            "widget_ids": [value["id"] for value in observation["state"]["widgets"]],
            "candidates": observation["candidates"],
            "artifact_ids": [value["id"] for value in observation["artifacts"]],
        }
