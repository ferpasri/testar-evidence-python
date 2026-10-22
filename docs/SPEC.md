# Python evidence library specification

The library validates and saves concrete test execution evidence. Reading saved traces and simple lookup methods support that work. Tool adapters supply captured information, action reports, and test-oracle results. The [shared specifications](https://github.com/ferpasri/testar-evidence-specs) define their meaning. This implementation uses the shared schema identified by `schema_version: 2`.

## Components

| Component | Responsibility |
| --- | --- |
| Record classes | Describe manifests, runs, widgets, states, candidates, actions, executions, and check results. |
| `EvidenceEvent` | Describe one reported event, its stable identity, and its format version. |
| `EvidenceModel` | Keep track of earlier records to check new events and retain observations and completed action links. |
| `JsonlStore` | Append valid events, store verified artifacts, reopen evidence, and recover an unfinished final line explicitly. |
| `EvidenceReader` | Read saved events and build their records and links in memory without changing the files. |
| JSON codec | Preserve JSON values and exact numbers while validating their representation. |

The public API is exported by `testar_evidence`. Dataclass records provide `to_dict()`; the recorder also accepts dictionaries in the shared format. The library checks each event before accepting it. For example, it checks that a referenced observation exists and belongs to the reported run. Reading saved files rebuilds these records for inspection, conformance tests, or continuing a recording. Lookup methods return independent copies so changing a returned record cannot change the saved evidence.

## Records and properties

An observation retains its sequence, timestamp, concrete state, widgets, candidates, candidate scope, and artifact references. Each visit keeps its own identity. Reusing a concrete-state identifier requires equal state content.

Widget parents refer to widgets in the same state; duplicate identities and hierarchy cycles are rejected. Candidates refer to defined actions and, when supplied, widgets at their observation. Generated candidates and eligible candidates retain their reported scope. An unspecified scope is unknown.

Property dictionaries preserve nested objects, lists, empty strings, booleans, numbers, and explicit null values. Missing properties remain distinct from null. Optional capture descriptions, aliases, known omissions, and assessment context are retained as supplied. The library does not infer common property names or widget equivalence.

JSON integers retain arbitrary precision. Fractional JSON numbers are read as `Decimal` and written as JSON numbers without converting them to binary floating point. Duplicate object keys, non-finite numbers, unsupported record fields, and nesting beyond 64 levels are rejected.

## Event order and execution links

Observations belong to a running run and their sequence numbers increase within that run. An execution selects an action/target candidate from the latest observation. A run has at most one pending execution.

Submission reports whether the execution wrapper accepted the action. A rejected submission keeps its error and has no completed result link. An accepted submission can receive a later observation from the same run as its result. Each completed action connects its before observation to its resulting observation, forming a state-action-state execution trace. Returning to the same screen still creates a separate visit.

Finishing a run marks a pending execution incomplete. Separate runs retain separate histories. Replaying the same event identity and content is idempotent; conflicting content for an existing identity is rejected. Rejected events change neither the accepted history nor the saved log.

## Test-oracle evaluations

A check result identifies its run, rule, target, outcome, and any supplied expectation and reading. Targets can identify a run, observation, state property, widget, widget property, execution, or observation interval. Referenced records must exist and belong to the reported run. A targeted property can be absent from the capture; the evaluation retains that supplied target without inventing a captured value.

Readings distinguish a supplied JSON value, established absence, and unknown information. An omitted expectation differs from an explicitly supplied null. Every result is retained separately, including repeated rules and targets, different statuses, and results reported after a run ends. Outcomes remain those supplied by the test oracle.

## Trace directory names in the example

The storage API takes an explicit directory. The runnable example creates a new child directory under a caller-selected parent using `<sut>_<objective>_trace-<local timestamp>`. Its default labels are `demo` and `submit-form`; the timestamp includes milliseconds. A collision adds `-2`, `-3`, and so on, reserving a directory before writing to it. Filename labels are sanitized and limited to 32 characters each; the run properties preserve the original labels. Each execution also receives distinct model and run identities.

For example, application and objective labels `parabank` and `denied-loan` produce a name such as `parabank_denied-loan_trace-2026-10-22_15-30-12-123`. Those labels describe the recording; the generic example itself uses demonstration records.

## Local storage and artifacts

The portable directory contains `manifest.json`, `evidence.jsonl`, and referenced artifact files. Evidence JSONL uses UTF-8 with one newline-terminated event per line. Each manifest or event is limited to 16 MiB. The log preserves complete accepted event payloads; JSON whitespace and field order do not define record identity.

An open writer holds an operating-system lock on `.writer.lock`. Another writer or recovery operation cannot open that directory concurrently. Appends are flushed to disk before the in-memory history advances. A failed append invalidates the writer until it is closed and the files are reopened or recovered.

Artifact references use relative portable paths and SHA-256 hashes. Paths reject traversal, reserved device names, symbolic links, and junctions. Writers use content-addressed filenames with `.png`, `.jpeg`, or `.bin` extensions. Identical bytes can share a file while observations remain separate. Readers and writers check referenced bytes against their hash.

Opening evidence rejects an unfinished final line. `JsonlStore.recover_tail(...)` validates all complete lines and their artifacts before removing only the unfinished tail. Complete corrupt records are reported rather than removed.

Abstraction, cross-source matching, and offline oracle execution belong to consumers of the concrete evidence.

## Verification

Tests exercise shared reader/producer conformance, complete event preservation, artifact integrity, rejected-event behavior, references, replay, execution lifecycles, exact numbers, writer locks, interrupted writes, and explicit tail recovery. Preparation tooling has isolated archive, release-selection, and cache-integrity tests. See the README for the commands.

