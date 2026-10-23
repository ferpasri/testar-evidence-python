"""Generic records, validation, and portable test execution evidence."""

from .errors import EvidenceError
from .model import EvidenceModel
from .records import (
    SCHEMA_VERSION,
    UNSPECIFIED,
    ActionCandidate,
    ActionDefinition,
    ArtifactReference,
    CheckReading,
    CheckResult,
    CheckRule,
    CheckTarget,
    ConcreteState,
    EvidenceEvent,
    Execution,
    Manifest,
    Observation,
    Run,
    Widget,
)
from .storage import EvidenceReader, JsonlStore

__version__ = "0.2.2"

__all__ = [
    "SCHEMA_VERSION", "UNSPECIFIED", "ActionCandidate", "ActionDefinition", "ArtifactReference",
    "CheckReading", "CheckResult", "CheckRule", "CheckTarget", "ConcreteState",
    "EvidenceEvent", "EvidenceError", "EvidenceModel", "EvidenceReader", "Execution",
    "JsonlStore", "Manifest", "Observation", "Run", "Widget", "__version__",
]
