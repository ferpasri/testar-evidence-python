"""Generic records, validation, and portable test execution evidence."""

from .errors import EvidenceError
from .reconstructed_evidence import ReconstructedEvidence
from .records import (
    SCHEMA_VERSION,
    UNSPECIFIED,
    ActionDescription,
    ActionExecution,
    ActionExecutionResult,
    ArtifactReference,
    AvailableAction,
    EvidenceEvent,
    Manifest,
    Observation,
    ObservedValue,
    Oracle,
    OracleResult,
    OracleSeverity,
    OracleTarget,
    RecordedState,
    RecordedTransition,
    TestRun,
    TestSequence,
    Widget,
)
from .storage import EvidenceReader, JsonlStore

__version__ = "0.3.2"

__all__ = [
    "ActionDescription",
    "ActionExecution",
    "ActionExecutionResult",
    "ArtifactReference",
    "AvailableAction",
    "EvidenceError",
    "EvidenceEvent",
    "ReconstructedEvidence",
    "EvidenceReader",
    "JsonlStore",
    "Manifest",
    "Observation",
    "ObservedValue",
    "Oracle",
    "OracleResult",
    "OracleSeverity",
    "OracleTarget",
    "RecordedState",
    "RecordedTransition",
    "SCHEMA_VERSION",
    "TestRun",
    "TestSequence",
    "UNSPECIFIED",
    "Widget",
    "__version__",
]
