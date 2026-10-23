# Python evidence library specification

The library implements [schema 3](https://github.com/ferpasri/testar-evidence-specs/blob/v0.3.0/docs/evidence-format.md). Adapters report facts from their tools; validation checks records and references before storage. The shared [terminology](https://github.com/ferpasri/testar-evidence-specs/blob/v0.3.0/docs/terminology.md) defines their meaning. The public API is exported by `testar_evidence`. Dataclass records provide `to_dict()`; the recorder also accepts dictionaries in the shared format.

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

## Histories and transitions

A test run starts before its sequences. Sequence indexes increase within the run; observation indexes increase independently within each sequence. Sequences can be interleaved without mixing their executions. A run finishes after all its started sequences finish. Repeated captures remain separate observations, including when recorded-state content is shared.

Selection refers to the latest observation and a reported available-action/widget pair. Each sequence has at most one execution awaiting its execution result or resulting observation. A returned call awaits a later observation in the same sequence. An error retains its message and permits another selection; partial effects can be captured without inventing a completed transition. Finishing a sequence makes unfinished executions incomplete.

Execution status is `awaiting_execution_result`, `awaiting_resulting_observation`, `failed`, `completed`, or `incomplete`. Recorded transitions retain transition-recording order; executions retain selection order. An observation-order link is a viewer relationship rather than an action inferred by this library.

`ReconstructedEvidence.validate` checks without mutation. `apply` validates before changing records. Identical event retries return false; conflicting event IDs or changed state/description content are rejected. `EvidenceError` exposes a code and field when applicable. A failed validation leaves history and the saved log intact.

## Properties and oracle results

Properties preserve nested JSON, arbitrary-precision integers and decimals, and explicit null. Missing fields remain distinct from supplied null where the contract permits both. Sequence indexes, observation indexes, timestamps, and artifact dimensions use Python integers checked against the unsigned 64-bit range. Fractional JSON values use `Decimal` to retain their precision. Widget parents remain inside one state and form an acyclic hierarchy.

Oracle targets cover a run, sequence, observation, widget, property, action execution, or inclusive observation range. Sequence-level references belong to the declared sequence and parent run. A whole-run target omits its sequence reference. A property target can name an absent property. Broad targets do not establish individual property coverage.

`UNSPECIFIED` omits an optional field; `expected_value=None` records a supplied JSON null. `ObservedValue` distinguishes value, established absence, and unknown information. Oracle severity is optional and preserves its source label and optional numeric value without imposing a universal range or changing oracle status. Each result remains separate, including repeated or late results after histories end.

Optional assessment metadata in `oracle_details` preserves temporal semantics, reporting phase, and the corresponding ending reason. Sequence and run phases refer to the appropriate lifecycle. Ending a history does not change pending oracle statuses.

## Metadata and capture

Run and sequence properties preserve supplied origin, test objective, adapter, and adapter-version descriptions. Unicode and spacing are retained; omitted descriptions remain absent. Sequence metadata remains separate from parent-run metadata.

Capture scope describes whether the supplied widget tree is complete, filtered, or unavailable. A tool can capture every available widget while saving only selected properties. The capture description identifies which properties are collected and, if known, explains exclusions or limitations.

Capture descriptions, property aliases, and known omissions are retained as supplied. Adapters establish source-property meanings. State-level `ui.title` represents a captured page, screen, or window title, while a widget's title attribute, visible text, and accessible name have distinct meanings. See the shared [capture guidance](https://github.com/ferpasri/testar-evidence-specs/blob/v0.3.0/docs/capture-evidence.md).

An observation retains all supplied available actions. Its scope states whether they are generated, eligible under the tool's selection rules, or of unknown scope. Availability and execution remain separate evidence.

## Reading and storage

`JsonlStore` manages `manifest.json`, `evidence.jsonl`, and referenced `artifacts/`. It holds an exclusive writer lock and refuses to overwrite existing evidence on creation. `record` validates the event and artifact hashes, appends newline-terminated UTF-8 JSON, and synchronizes before acknowledgment. A persistence failure invalidates the writer until reopening.

`EvidenceReader.read` reconstructs history without opening a writer. `store.evidence` returns an independent snapshot. Accepted records and returned collections are independent copies. Changing a supplied dataclass or dictionary later cannot alter accepted history. `ReconstructedEvidence` is intended for one caller at a time. `test_sequences_for_run`, `observations_for_sequence`, `observations_for_recorded_state`, and `executions_for_sequence` support inspection; `incoming` and `outgoing` return recorded transitions.

Artifacts use SHA-256 content identities and portable relative paths. Identical bytes reuse stored files. PNG, JPEG, and other media use `.png`, `.jpeg`, and `.bin` paths. Hash verification detects missing or changed bytes when recording or reading.

Opening an unfinished final line fails. Explicit `recover_tail` validates the complete prefix and artifacts before removing only the unfinished fragment; complete invalid records are retained and reported. The JSON codec and reader reject invalid UTF-8, duplicate keys, unknown structural fields, unsafe paths, and trailing JSON values. Records and manifests are limited to 16 MiB and nesting to 64 levels.

## Example and verification

The commented `record_evidence.py` example records one run and sequence, two observations, available actions, one completed transition and a property oracle result. Each execution reserves a distinct timestamped directory with optional SUT and test objective labels. Supplied metadata retains its original text; filename labels are sanitized separately.

Reader and producer tests use the newest prepared specs release and verify its checksums. They compare all released expected summaries and accepted payloads, rejection prefixes, retries, reopen behavior, and artifacts. Unit tests cover immutable state identities, hierarchy, interleaved sequences, transitions, exact numbers, oracle targets/severity, writer locks, interrupted writes, and recovery. See the [README](../README.md#shared-conformance-data) for preparation commands.
