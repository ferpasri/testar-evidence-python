"""Generic evidence records used by adapters and the schema-3 JSON format."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from decimal import Decimal
import math
import re
from typing import Any

from .codec import to_data
from .errors import EvidenceError

SCHEMA_VERSION = 3
MAX_UNSIGNED_64 = (1 << 64) - 1


class _Unspecified:
    def __repr__(self) -> str:
        return "UNSPECIFIED"

    def __deepcopy__(self, memo):
        return self


UNSPECIFIED = _Unspecified()


class _Record:
    def to_dict(self) -> dict[str, Any]:
        return to_data({item.name: getattr(self, item.name) for item in fields(self)
                        if getattr(self, item.name) is not UNSPECIFIED})


@dataclass(frozen=True)
class Manifest(_Record):
    evidence_id: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_manifest(super().to_dict())

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Manifest:
        return cls(**validate_manifest(value))


@dataclass(frozen=True)
class EvidenceEvent:
    id: str
    event_type: str
    data: Any
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return validate_event({"schema_version": self.schema_version, "id": self.id,
                               "event": {"event_type": self.event_type, "data": to_data(self.data)}})

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EvidenceEvent:
        record = validate_event(value)
        return cls(record["id"], record["event"]["event_type"], record["event"]["data"], record["schema_version"])


@dataclass(frozen=True)
class TestRun(_Record):
    id: str
    producer: str
    source_run: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TestSequence(_Record):
    id: str
    test_run: str
    sequence_index: int
    source_sequence: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Widget(_Record):
    id: str
    parent: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordedState(_Record):
    id: str
    properties: dict[str, Any] = field(default_factory=dict)
    widgets: list[Widget | dict[str, Any]] = field(default_factory=list)
    capture: str = "filtered"


@dataclass(frozen=True)
class AvailableAction(_Record):
    action_description: str
    target_widget: str | None = None


@dataclass(frozen=True)
class ActionDescription(_Record):
    id: str
    action_type: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactReference(_Record):
    id: str
    path: str
    media_type: str
    sha256: str
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class Observation(_Record):
    id: str
    test_sequence: str
    observation_index: int
    timestamp_micros: int
    recorded_state: RecordedState | dict[str, Any]
    available_action_scope: str
    available_actions: list[AvailableAction | dict[str, Any]] = field(default_factory=list)
    artifacts: list[ArtifactReference | dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ActionExecution(_Record):
    id: str
    test_sequence: str
    source_observation: str
    action_description: str
    target_widget: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionExecutionResult(_Record):
    action_execution: str
    outcome: str
    error: str | None = None


@dataclass(frozen=True)
class RecordedTransition(_Record):
    action_execution: str
    resulting_observation: str


@dataclass(frozen=True)
class Oracle(_Record):
    id: str
    version: str | _Unspecified = UNSPECIFIED
    parameters: dict[str, Any] | _Unspecified = UNSPECIFIED


@dataclass(frozen=True)
class OracleSeverity(_Record):
    label: str
    value: int | float | Decimal | _Unspecified = UNSPECIFIED


@dataclass(frozen=True)
class ObservedValue:
    value_status: str
    observed: Any = UNSPECIFIED

    def to_dict(self) -> dict[str, Any]:
        result = {"value_status": self.value_status}
        if self.observed is not UNSPECIFIED:
            result["value"] = to_data(self.observed)
        _observed_value(result)
        return result

    @classmethod
    def value(cls, value: Any) -> ObservedValue:
        return cls("value", value)

    @classmethod
    def absent(cls) -> ObservedValue:
        return cls("absent")

    @classmethod
    def unknown(cls) -> ObservedValue:
        return cls("unknown")


@dataclass(frozen=True)
class OracleTarget:
    target_type: str
    references: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = {"target_type": self.target_type, **self.references}
        _oracle_target(result)
        return result

    @classmethod
    def test_run(cls) -> OracleTarget:
        return cls("test_run")

    @classmethod
    def test_sequence(cls) -> OracleTarget:
        return cls("test_sequence")

    @classmethod
    def observation(cls, observation: str) -> OracleTarget:
        return cls("observation", {"observation": observation})

    @classmethod
    def state_property(cls, observation: str, property: str) -> OracleTarget:
        return cls("state_property", {"observation": observation, "property": property})

    @classmethod
    def widget(cls, observation: str, widget: str) -> OracleTarget:
        return cls("widget", {"observation": observation, "widget": widget})

    @classmethod
    def widget_property(cls, observation: str, widget: str, property: str) -> OracleTarget:
        return cls("widget_property", {"observation": observation, "widget": widget, "property": property})

    @classmethod
    def action_execution(cls, action_execution: str) -> OracleTarget:
        return cls("action_execution", {"action_execution": action_execution})

    @classmethod
    def observation_range(cls, first_observation: str, last_observation: str) -> OracleTarget:
        return cls("observation_range", {"first_observation": first_observation, "last_observation": last_observation})


@dataclass(frozen=True)
class OracleResult(_Record):
    test_run: str
    name: str
    oracle: Oracle | dict[str, Any]
    oracle_target: OracleTarget | dict[str, Any]
    oracle_status: str
    test_sequence: str | _Unspecified = UNSPECIFIED
    expected_value: Any = UNSPECIFIED
    observed_value: ObservedValue | dict[str, Any] | _Unspecified = UNSPECIFIED
    oracle_severity: OracleSeverity | dict[str, Any] | _Unspecified = UNSPECIFIED
    oracle_details: dict[str, Any] = field(default_factory=dict)


def _require(condition: bool, message: str, field: str | None = None, code: str = "invalid_record") -> None:
    if not condition:
        raise EvidenceError(code, message, field)


def _text(value: Any, name: str) -> str:
    _require(isinstance(value, str) and bool(value.strip())
             and not any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value),
             f"{name} must be nonblank text without control characters", name)
    return value


def _object(value: Any, name: str = "record") -> dict[str, Any]:
    _require(isinstance(value, dict), f"{name} must be an object", name)
    return value


def _fields(value: Any, required: set[str], optional: set[str] | None = None,
            name: str = "record") -> dict[str, Any]:
    result = _object(value, name)
    for key in result:
        _require(key in required or key in (optional or set()), f"Unknown {name} field: {key}", key)
    for key in required:
        _require(key in result, f"{name}.{key} is required", key)
    return result


def _properties(value: Any, name: str = "properties") -> None:
    for key in _object(value, name):
        _text(key, "Property name")


def _texts(data: dict[str, Any], names: tuple[str, ...]) -> None:
    for name in names:
        _text(data[name], name)


def _optional_text(data: dict[str, Any], name: str) -> None:
    if data.get(name) is not None:
        _text(data[name], name)


def _unsigned(value: Any, name: str, maximum: int = MAX_UNSIGNED_64) -> None:
    _require(type(value) is int and 0 <= value <= maximum,
             f"{name} must be an unsigned integer no greater than {maximum}", name)


def _list(value: Any, name: str) -> list[Any]:
    _require(isinstance(value, list), f"{name} must be an array", name)
    return value


def _choice(value: Any, choices: set[str], name: str) -> None:
    _require(isinstance(value, str) and value in choices, f"Unsupported {name}: {value!r}", name)


def validate_path(path: str) -> str:
    """Validate a portable relative artifact path without accessing the filesystem."""
    _text(path, "path")
    _require(not path.startswith("/") and "\\" not in path and ":" not in path,
             "Artifact path must be portable and relative", "path")
    for component in path.split("/"):
        device = re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", component.split(".", 1)[0].upper())
        _require(component not in {"", ".", ".."} and not component.endswith((".", " "))
                 and not any(character in '<>"|?*' for character in component) and device is None,
                 f"Unsafe artifact path: {path}", "path")
    return path


def validate_artifact(value: dict[str, Any]) -> dict[str, Any]:
    result = to_data(value)
    _artifact(result)
    return result


def _artifact(value: Any) -> None:
    data = _fields(value, {"id", "path", "media_type", "sha256"}, {"width", "height"}, "artifact")
    _texts(data, ("id", "media_type"))
    validate_path(data["path"])
    _require(isinstance(data["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", data["sha256"]) is not None,
             "Artifact SHA-256 must have 64 lowercase hexadecimal characters", "sha256")
    for dimension in ("width", "height"):
        if data.get(dimension) is not None:
            _unsigned(data[dimension], dimension)


def _state(value: Any) -> set[str]:
    data = _fields(value, {"id", "properties", "widgets", "capture"}, name="state")
    _text(data["id"], "id")
    _properties(data["properties"])
    _choice(data["capture"], {"complete", "filtered", "unavailable"}, "capture")
    widgets = _list(data["widgets"], "widgets")
    _require(data["capture"] != "unavailable" or not widgets, "Unavailable capture cannot contain widgets")
    parents: dict[str, str | None] = {}
    for widget in widgets:
        _fields(widget, {"id", "parent", "properties"}, name="widget")
        _text(widget["id"], "id")
        _optional_text(widget, "parent")
        _properties(widget["properties"])
        _require(widget["id"] not in parents, f"Duplicate widget: {widget['id']}", code="conflict")
        parents[widget["id"]] = widget.get("parent")
    completed: set[str] = set()
    for identifier in parents:
        ancestry: set[str] = set()
        current = identifier
        while current is not None and current not in completed:
            _require(current not in ancestry, "Widget hierarchy contains a cycle")
            _require(current in parents, f"Unknown parent widget: {current}", code="invalid_reference")
            ancestry.add(current)
            current = parents[current]
        completed.update(ancestry)
    return set(parents)



def _observation(value: Any) -> None:
    data = _fields(value, {"id", "test_sequence", "observation_index", "timestamp_micros", "recorded_state",
                          "available_actions", "available_action_scope", "artifacts"}, name="observation")
    _texts(data, ("id", "test_sequence"))
    _unsigned(data["observation_index"], "observation_index")
    _unsigned(data["timestamp_micros"], "timestamp_micros")
    _choice(data["available_action_scope"], {"unknown", "generated", "eligible"}, "available_action_scope")
    widgets = _state(data["recorded_state"])
    available_actions: set[tuple[str, str | None]] = set()
    for action in _list(data["available_actions"], "available_actions"):
        _fields(action, {"action_description", "target_widget"}, name="available action")
        _text(action["action_description"], "action_description")
        _optional_text(action, "target_widget")
        target_widget = action["target_widget"]
        _require(target_widget is None or target_widget in widgets, "Unknown available-action widget",
                 "target_widget", "invalid_reference")
        key = (action["action_description"], target_widget)
        _require(key not in available_actions, "Duplicate available action", code="conflict")
        available_actions.add(key)
    artifacts: set[str] = set()
    for artifact in _list(data["artifacts"], "artifacts"):
        _artifact(artifact)
        _require(artifact["id"] not in artifacts, "Duplicate artifact ID", code="conflict")
        artifacts.add(artifact["id"])


def _oracle_target(value: Any) -> None:
    data = _object(value, "oracle_target")
    target_fields = {
        "test_run": (), "test_sequence": (), "observation": ("observation",),
        "state_property": ("observation", "property"), "widget": ("observation", "widget"),
        "widget_property": ("observation", "widget", "property"),
        "action_execution": ("action_execution",),
        "observation_range": ("first_observation", "last_observation"),
    }
    _choice(data.get("target_type"), set(target_fields), "oracle_target.target_type")
    names = target_fields[data["target_type"]]
    _fields(data, {"target_type", *names}, name="oracle_target")
    _texts(data, names)


def _observed_value(value: Any) -> None:
    data = _object(value, "observed_value")
    _choice(data.get("value_status"), {"value", "absent", "unknown"}, "observed_value.value_status")
    required = {"value_status", "value"} if data["value_status"] == "value" else {"value_status"}
    _fields(data, required, name="observed_value")


def _oracle_severity(value: Any) -> None:
    data = _fields(value, {"label"}, {"value"}, "oracle_severity")
    label = data["label"]
    _require(isinstance(label, str) and bool(label.strip())
             and not any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in label),
             "Oracle severity label must be nonblank text without control characters",
             "oracle_severity.label", "invalid_oracle_severity")
    if "value" in data:
        number = data["value"]
        finite = isinstance(number, int) or isinstance(number, float) and math.isfinite(number)
        finite = finite or isinstance(number, Decimal) and number.is_finite()
        _require(type(number) is not bool and finite, "Oracle severity value must be a finite number",
                 "oracle_severity.value", "invalid_oracle_severity")


def _oracle_result(value: Any) -> None:
    data = _fields(value, {"test_run", "name", "oracle", "oracle_target", "oracle_status", "oracle_details"},
                   {"test_sequence", "expected_value", "observed_value", "oracle_severity"}, "oracle result")
    _texts(data, ("test_run", "name"))
    if "test_sequence" in data:
        _text(data["test_sequence"], "test_sequence")
    _choice(data["oracle_status"], {"passed", "failed", "pending", "unknown", "error"}, "oracle_status")
    oracle = _fields(data["oracle"], {"id"}, {"version", "parameters"}, "oracle")
    _text(oracle["id"], "id")
    if "version" in oracle:
        _text(oracle["version"], "version")
    if "parameters" in oracle:
        _properties(oracle["parameters"], "parameters")
    _oracle_target(data["oracle_target"])
    if "observed_value" in data:
        _observed_value(data["observed_value"])
    if "oracle_severity" in data:
        _oracle_severity(data["oracle_severity"])
    _properties(data["oracle_details"], "oracle_details")


def validate_manifest(value: dict[str, Any]) -> dict[str, Any]:
    result = to_data(value)
    _fields(result, {"schema_version", "evidence_id"}, name="manifest")
    _require(type(result["schema_version"]) is int and result["schema_version"] == SCHEMA_VERSION,
             f"Supported schema version is {SCHEMA_VERSION}", "schema_version")
    _text(result["evidence_id"], "evidence_id")
    return result


def validate_event(value: dict[str, Any]) -> dict[str, Any]:
    """Validate one Evidence Event and its local references; return an independent copy."""
    result = to_data(value)
    _fields(result, {"schema_version", "id", "event"}, name="Evidence Event")
    _require(type(result["schema_version"]) is int and result["schema_version"] == SCHEMA_VERSION,
             f"Supported schema version is {SCHEMA_VERSION}", "schema_version")
    _text(result["id"], "id")
    event = _fields(result["event"], {"event_type", "data"}, name="event")
    event_type = event["event_type"]
    _text(event_type, "event_type")
    data = _object(event["data"], "data")
    if event_type == "test_run_started":
        _fields(data, {"id", "producer", "source_run", "properties"}, name="test run")
        _texts(data, ("id", "producer", "source_run"))
        _properties(data["properties"])
    elif event_type == "test_sequence_started":
        _fields(data, {"id", "test_run", "sequence_index", "source_sequence", "properties"}, name="test sequence")
        _texts(data, ("id", "test_run", "source_sequence"))
        _unsigned(data["sequence_index"], "sequence_index")
        _require(data["sequence_index"] > 0, "Test sequence index must be positive", "sequence_index")
        _properties(data["properties"])
    elif event_type == "action_described":
        _fields(data, {"id", "action_type", "properties"}, name="action description")
        _texts(data, ("id", "action_type"))
        _properties(data["properties"])
    elif event_type == "observation_recorded":
        _observation(data)
    elif event_type == "action_selected":
        _fields(data, {"id", "test_sequence", "source_observation", "action_description", "target_widget", "parameters"},
                name="action execution")
        _texts(data, ("id", "test_sequence", "source_observation", "action_description"))
        _optional_text(data, "target_widget")
        _properties(data["parameters"], "parameters")
    elif event_type == "action_execution_reported":
        _fields(data, {"action_execution", "outcome", "error"}, name="action execution result")
        _text(data["action_execution"], "action_execution")
        _choice(data["outcome"], {"returned", "error"}, "outcome")
        _require(data["error"] is None if data["outcome"] == "returned" else isinstance(data["error"], str),
                 "Returned calls have null error; errors carry their reported text", "error")
    elif event_type == "transition_recorded":
        _fields(data, {"action_execution", "resulting_observation"}, name="recorded transition")
        _texts(data, ("action_execution", "resulting_observation"))
    elif event_type == "oracle_result_recorded":
        _oracle_result(data)
    elif event_type == "test_sequence_finished":
        _fields(data, {"test_sequence", "reason"}, name="test sequence ending")
        _texts(data, ("test_sequence", "reason"))
    elif event_type == "test_run_finished":
        _fields(data, {"test_run", "reason"}, name="test run ending")
        _texts(data, ("test_run", "reason"))
    else:
        raise EvidenceError("invalid_record", f"Unknown event type: {event_type}", "event_type")
    return result
