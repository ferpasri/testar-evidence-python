# Python evidence library specification

The library validates and stores records using the [shared evidence contract](https://github.com/ferpasri/testar-evidence-specs/blob/main/docs/evidence-format.md). The [shared terminology](https://github.com/ferpasri/testar-evidence-specs/blob/main/docs/terminology.md) defines record meanings. Public APIs are in `testar_evidence`; package and format identifiers are described in the [README](../README.md#compatibility).

## Components

| Type | Responsibility |
| --- | --- |
| `TestRun` / `TestSequence` | Describe an invocation and its independent sequence histories. |
| `Observation` / `RecordedState` / `Widget` | Preserve captured moments, state content, widget hierarchy, properties, and capture limits. |
| `ActionDescription` / `AvailableAction` | Describe operations and reported choices, including choices never executed. |
| `ActionExecution` / `ActionExecutionResult` / `RecordedTransition` | Preserve selection, parameters, the runner's outcome, and a resulting-observation connection. |
| `Oracle` / `OracleResult` / `OracleTarget` | Identify checks and preserve their targets and outcomes. |
| `ObservedValue` / `OracleSeverity` | Preserve reported values, established absence or uncertainty, and optional source-defined importance. |
| `EvidenceEvent` | Carry event identity/version and its reported payload. |
| `ReconstructedEvidence` | Retain recorded facts and relationships in memory for validation and inspection. |
| `JsonlStore` / `EvidenceReader` | Write validated evidence and read it back. |

## Validation and value handling

`ReconstructedEvidence.validate` checks without mutation; `apply` validates before changing records. Identical retries return false. `EvidenceError` exposes a code and field when applicable. Rejected events change neither accepted history nor the saved log.

The shared contract owns run and sequence ordering, available-action bindings, recorded transitions, and oracle-target rules. The reconstructor enforces those rules on recording and reading. Observation-order links belong to consumers rather than recorded action events.

Properties retain nested JSON, arbitrary-precision integers and decimals, and explicit null. Indexes, timestamps, and artifact dimensions use Python integers validated against the unsigned 64-bit range. Fractional JSON values use `Decimal`. Dataclass records provide `to_dict()`; recording also accepts dictionaries in the shared format. `UNSPECIFIED` omits an optional field; `expected_value=None` records supplied JSON null.

Source properties, capture descriptions, aliases, omissions, and optional run/sequence metadata remain as supplied. Oracle status and source-defined severity remain independent. Adapters establish property meanings; see the shared [capture guidance](https://github.com/ferpasri/testar-evidence-specs/blob/main/docs/capture-evidence.md).

## Reading and storage

`JsonlStore.create(directory, manifest)` refuses existing evidence files and holds an exclusive writer lock. Each successful record is validated with artifact hashes, appended as newline-terminated UTF-8 JSON, and synchronized before acknowledgment. A persistence failure invalidates the writer until reopening.

`EvidenceReader.read(directory)` validates saved evidence without opening a writer. `JsonlStore.open(directory)` validates it before continued recording. `store.evidence` returns an independent snapshot. Accepted records and returned collections are independent copies; later changes to supplied dataclasses or dictionaries cannot alter history. `ReconstructedEvidence` is intended for one caller at a time.

`JsonlStore.available_actions(observation)` returns an independent copy of one committed observation's action bindings under the store lock. Unknown observations raise `invalid_reference`; a closed store raises `closed`.

Lookup methods expose recorded sequences, observations, executions, and incoming/outgoing transitions. They preserve recorded facts and do not construct an abstract model.

Artifacts use SHA-256 identities and relative paths. Identical bytes reuse files; PNG, JPEG, and other media use `.png`, `.jpeg`, and `.bin` paths. Reading and recording verify referenced bytes. Path validation rejects traversal and unsafe platform-specific names.

An unfinished final line requires explicit `recover_tail`: recovery validates the complete prefix and artifacts before removing only that unfinished fragment. Complete invalid records are reported rather than discarded.

The reader rejects invalid UTF-8, duplicate keys, unknown structural fields, and trailing JSON values. Records and manifests are bounded to 16 MiB and nesting to 64 levels.

## Verification

Reader and producer tests discover all prepared specs cases, verify cache checksums, and compare expected summaries, complete accepted payloads, rejection prefixes, retries, and artifacts. Focused tests cover implementation boundaries including exact values, independent snapshots, writer locks, failed writes, and recovery. Preparation and test commands are in the [README](../README.md#build-and-test).
