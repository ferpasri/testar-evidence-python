"""Generic evidence records and validation of individual wire events."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import re
from typing import Any

from .codec import to_data
from .errors import EvidenceError

SCHEMA_VERSION = 2
MAX_UNSIGNED_64 = (1 << 64) - 1


class _Unspecified:
    def __repr__(self) -> str:
        return "UNSPECIFIED"


UNSPECIFIED = _Unspecified()


class _Record:
    def to_dict(self) -> dict[str, Any]:
        return to_data({item.name: getattr(self, item.name) for item in fields(self)
                        if getattr(self, item.name) is not UNSPECIFIED})


@dataclass(frozen=True)
class Manifest(_Record):
    model: str
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_manifest(super().to_dict())

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Manifest:
        return cls(**validate_manifest(value))


@dataclass(frozen=True)
class EvidenceEvent:
    id: str
    kind: str
    data: Any
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return validate_event({"schema_version": self.schema_version, "id": self.id,
                               "event": {"kind": self.kind, "data": to_data(self.data)}})

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EvidenceEvent:
        record = validate_event(value)
        return cls(record["id"], record["event"]["kind"], record["event"]["data"], record["schema_version"])


@dataclass(frozen=True)
class Run(_Record):
    id: str
    producer: str
    source_run: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Widget(_Record):
    id: str
    parent: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConcreteState(_Record):
    id: str
    properties: dict[str, Any] = field(default_factory=dict)
    widgets: list[Widget | dict[str, Any]] = field(default_factory=list)
    capture: str = "filtered"


@dataclass(frozen=True)
class ActionCandidate(_Record):
    action: str
    target: str | None = None


@dataclass(frozen=True)
class ActionDefinition(_Record):
    id: str
    kind: str
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
    run: str
    sequence: int
    timestamp_micros: int
    state: ConcreteState | dict[str, Any]
    candidates: list[ActionCandidate | dict[str, Any]] = field(default_factory=list)
    candidate_scope: str = "unknown"
    artifacts: list[ArtifactReference | dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Execution(_Record):
    id: str
    run: str
    source: str
    action: str
    target: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckRule(_Record):
    id: str
    version: str | None = None
    parameters: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in super().to_dict().items() if value is not None}


@dataclass(frozen=True)
class CheckReading:
    kind: str
    reading: Any = UNSPECIFIED

    def to_dict(self) -> dict[str, Any]:
        result = {"kind": self.kind}
        if self.reading is not UNSPECIFIED:
            result["value"] = to_data(self.reading)
        _reading(result)
        return result

    @classmethod
    def value(cls, value: Any) -> CheckReading:
        return cls("value", value)

    @classmethod
    def absent(cls) -> CheckReading:
        return cls("absent")

    @classmethod
    def unknown(cls) -> CheckReading:
        return cls("unknown")


@dataclass(frozen=True)
class CheckTarget:
    kind: str
    references: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = {"kind": self.kind, **self.references}
        _target(result)
        return result

    @classmethod
    def run(cls) -> CheckTarget:
        return cls("run")

    @classmethod
    def observation(cls, observation: str) -> CheckTarget:
        return cls("observation", {"observation": observation})

    @classmethod
    def state_property(cls, observation: str, property: str) -> CheckTarget:
        return cls("state_property", {"observation": observation, "property": property})

    @classmethod
    def widget(cls, observation: str, widget: str) -> CheckTarget:
        return cls("widget", {"observation": observation, "widget": widget})

    @classmethod
    def widget_property(cls, observation: str, widget: str, property: str) -> CheckTarget:
        return cls("widget_property", {"observation": observation, "widget": widget, "property": property})

    @classmethod
    def execution(cls, execution: str) -> CheckTarget:
        return cls("execution", {"execution": execution})

    @classmethod
    def sequence(cls, from_observation: str, to_observation: str) -> CheckTarget:
        return cls("sequence", {"from_observation": from_observation, "to_observation": to_observation})


@dataclass(frozen=True)
class CheckResult(_Record):
    run: str
    name: str
    rule: CheckRule | dict[str, Any]
    target: CheckTarget | dict[str, Any]
    status: str
    expected: Any = UNSPECIFIED
    actual: CheckReading | dict[str, Any] | _Unspecified = UNSPECIFIED
    details: dict[str, Any] = field(default_factory=dict)


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
    _require(isinstance(data["sha256"], str) and re.fullmatch(r"[0-9a-fA-F]{64}", data["sha256"]) is not None,
             "Artifact SHA-256 must have 64 hexadecimal characters", "sha256")
    for dimension in ("width", "height"):
        if data.get(dimension) is not None:
            _unsigned(data[dimension], dimension, (1 << 32) - 1)
            _require(data[dimension] > 0, "Artifact dimensions must be positive", dimension)


def _state(value: Any) -> set[str]:
    data = _fields(value, {"id", "properties", "widgets", "capture"}, name="state")
    _text(data["id"], "id")
    _properties(data["properties"])
    _choice(data["capture"], {"complete", "filtered", "unavailable"}, "capture")
    widgets = _list(data["widgets"], "widgets")
    _require(data["capture"] != "unavailable" or not widgets, "Unavailable capture cannot contain widgets")
    parents: dict[str, str | None] = {}
    for widget in widgets:
        _fields(widget, {"id", "properties"}, {"parent"}, "widget")
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
            _require(current in parents, f"Unknown parent widget: {current}", code="unknown_reference")
            ancestry.add(current)
            current = parents[current]
        completed.update(ancestry)
    return set(parents)


def _observation(value: Any) -> None:
    data = _fields(value, {"id", "run", "sequence", "timestamp_micros", "state", "candidates", "artifacts"},
                   {"candidate_scope"}, "observation")
    _texts(data, ("id", "run"))
    _unsigned(data["sequence"], "sequence")
    _unsigned(data["timestamp_micros"], "timestamp_micros")
    _choice(data.get("candidate_scope", "unknown"), {"unknown", "generated", "eligible"}, "candidate_scope")
    widgets = _state(data["state"])
    candidates: set[tuple[str, str | None]] = set()
    for candidate in _list(data["candidates"], "candidates"):
        _fields(candidate, {"action"}, {"target"}, "candidate")
        _text(candidate["action"], "action")
        _optional_text(candidate, "target")
        target = candidate.get("target")
        _require(target is None or target in widgets, f"Unknown candidate widget: {target}", code="unknown_reference")
        key = (candidate["action"], target)
        _require(key not in candidates, "Duplicate action candidate", code="conflict")
        candidates.add(key)
    artifacts: set[str] = set()
    for artifact in _list(data["artifacts"], "artifacts"):
        _artifact(artifact)
        _require(artifact["id"] not in artifacts, "Duplicate artifact ID", code="conflict")
        artifacts.add(artifact["id"])


def _target(value: Any) -> None:
    data = _object(value, "target")
    kinds = {"run": (), "observation": ("observation",), "state_property": ("observation", "property"),
             "widget": ("observation", "widget"), "widget_property": ("observation", "widget", "property"),
             "execution": ("execution",), "sequence": ("from_observation", "to_observation")}
    _choice(data.get("kind"), set(kinds), "target kind")
    names = kinds[data["kind"]]
    _fields(data, {"kind", *names}, name="target")
    _texts(data, names)


def _reading(value: Any) -> None:
    data = _object(value, "actual")
    _choice(data.get("kind"), {"value", "absent", "unknown"}, "reading kind")
    required = {"kind", "value"} if data["kind"] == "value" else {"kind"}
    _fields(data, required, name="actual")


def _check(value: Any) -> None:
    data = _fields(value, {"run", "name", "rule", "target", "status"}, {"expected", "actual", "details"}, "check")
    _texts(data, ("run", "name"))
    _choice(data["status"], {"passed", "failed", "pending", "unknown", "error"}, "status")
    rule = _fields(data["rule"], {"id"}, {"version", "parameters"}, "rule")
    _text(rule["id"], "id")
    if "version" in rule:
        _text(rule["version"], "version")
    if "parameters" in rule:
        _properties(rule["parameters"], "parameters")
    _target(data["target"])
    if "actual" in data:
        _reading(data["actual"])
    if "details" in data:
        _properties(data["details"], "details")


def validate_manifest(value: dict[str, Any]) -> dict[str, Any]:
    result = to_data(value)
    _fields(result, {"schema_version", "model"}, name="manifest")
    _require(type(result["schema_version"]) is int and result["schema_version"] == SCHEMA_VERSION,
             f"Supported schema version is {SCHEMA_VERSION}", "schema_version")
    _text(result["model"], "model")
    return result


def validate_event(value: dict[str, Any]) -> dict[str, Any]:
    """Validate one event and its local references; return an independent copy."""
    result = to_data(value)
    _fields(result, {"schema_version", "id", "event"}, name="evidence event")
    _require(type(result["schema_version"]) is int and result["schema_version"] == SCHEMA_VERSION,
             f"Supported schema version is {SCHEMA_VERSION}", "schema_version")
    _text(result["id"], "id")
    event = _fields(result["event"], {"kind", "data"}, name="event")
    _text(event["kind"], "kind")
    kind = event["kind"]
    data = _object(event["data"], "data")
    if kind == "run_started":
        _fields(data, {"id", "producer", "source_run", "properties"}, name="run")
        _texts(data, ("id", "producer", "source_run"))
        _properties(data["properties"])
    elif kind == "action_defined":
        _fields(data, {"id", "kind", "properties"}, name="action")
        _texts(data, ("id", "kind"))
        _properties(data["properties"])
    elif kind == "observed":
        _observation(data)
    elif kind == "action_selected":
        _fields(data, {"id", "run", "source", "action", "parameters"}, {"target"}, "execution")
        _texts(data, ("id", "run", "source", "action"))
        _optional_text(data, "target")
        _properties(data["parameters"], "parameters")
    elif kind == "action_submitted":
        _fields(data, {"execution", "accepted"}, {"error"}, "submission")
        _text(data["execution"], "execution")
        _require(type(data["accepted"]) is bool, "Submission accepted must be a boolean", "accepted")
        _require(data.get("error") is None or isinstance(data["error"], str), "Submission error must be text or null", "error")
        _require(not data["accepted"] or data.get("error") is None, "Accepted submission cannot carry an error")
    elif kind == "action_result":
        _fields(data, {"execution", "observation"}, name="result")
        _texts(data, ("execution", "observation"))
    elif kind == "checked":
        _check(data)
    elif kind == "run_finished":
        _fields(data, {"run", "reason"}, name="ending")
        _texts(data, ("run", "reason"))
    else:
        raise EvidenceError("invalid_record", f"Unknown event kind: {kind}", "kind")
    return result
