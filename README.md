# TESTAR evidence for Python

A standalone Python library for validating and saving portable test execution evidence in the shared [TESTAR evidence format](https://github.com/ferpasri/testar-evidence-specs).

Adapters supply test runs and sequences, recorded states, widgets, properties, available actions, executions, artifacts, and oracle results. The library checks the records and their references, then saves them as portable evidence files.

Every capture remains a separate observation. A recorded transition connects an action execution's source observation to its resulting observation, forming a state-action-state trace. Available actions remain recorded when never executed. Captured properties and oracle evaluations are separate evidence. Consumers can display these traces or use abstraction rules to build abstract models.

## Build and test

Use Python 3.11 or later. Run these commands from this repository:

```sh
python -m pip install -e .
python -m tools.prepare_conformance
python -m unittest discover -s tests -p "test_*.py"
```

The library has no runtime dependencies. Installation uses the packaging tools declared in `pyproject.toml`; tests use Python's standard `unittest` module.

A wheel is an installable Python package stored in a `.whl` file. Build one when distributing the packaged library:

```sh
python -m pip wheel --no-deps . --wheel-dir dist
```

## Import the library

A consuming project can install this checkout as a local dependency:

```sh
python -m pip install -e ../testar-evidence-python
```

Import the public API from `testar_evidence`. The distribution name is `testar-evidence` and its version is `0.3.0`. `EvidenceEvent` describes one reported event together with its identity and format version. The storage API accepts an explicit, fresh directory:

```python
from testar_evidence import EvidenceEvent, JsonlStore, Manifest, TestRun, TestSequence

with JsonlStore.create("target/my-evidence", Manifest("example:evidence:1")) as store:
    store.record(EvidenceEvent("example:event:1", "test_run_started",
        TestRun("example:run:1", "example", "1")))
    store.record(EvidenceEvent("example:event:2", "test_sequence_started",
        TestSequence("example:sequence:1", "example:run:1", 1, "1")))
    store.record(EvidenceEvent("example:event:3", "test_sequence_finished",
        {"test_sequence": "example:sequence:1", "reason": "completed"}))
    store.record(EvidenceEvent("example:event:4", "test_run_finished",
        {"test_run": "example:run:1", "reason": "completed"}))
```

The output contains `manifest.json`, `evidence.jsonl`, and referenced files under `artifacts/`. Creation refuses an existing manifest or evidence log.

The commented [record_evidence.py](examples/record_evidence.py) example records observations, two available button actions, one completed transition, and a property oracle result:

```sh
python examples/record_evidence.py target/traces --sut demo --test-objective submit-form
```

Each execution reserves a distinct timestamped directory, such as `demo_submit-form_trace-2026-10-22_15-30-12-123/`. Filename labels are sanitized; optional origin and test objective descriptions retain their supplied text in run properties. The example accepts `--origin`. An omitted test objective uses `run` only as a filename label. Load the complete generated directory in the dashboard.

## Reading saved evidence

`ReconstructedEvidence` holds recorded facts and relationships in memory for validation and inspection.

The library retains earlier records while writing so it can validate new ones. A test run contains test sequences, each with its own observations and action executions. `EvidenceReader.read(directory)` reads saved evidence without changing it. `JsonlStore.open(directory)` validates existing records before continuing a recording. `store.evidence` returns an independent snapshot for inspection.

`test_sequences_for_run`, `observations_for_sequence`, `executions_for_sequence`, `incoming`, and `outgoing` provide simple lookups. Consumers decide how to build an abstract model from these traces.

## Shared conformance data

The preparation command queries the public [specs releases](https://github.com/ferpasri/testar-evidence-specs/releases), selects the newest publication by `published_at`, including prereleases, and downloads its conformance files over HTTPS. It validates archive paths, extraction limits, case completeness, and checksums. The ignored `target/conformance/` cache includes the selected release and locally computed SHA-256 checksums. Failed preparation reports the error and preserves the previous selection.

Tests discover every prepared case and verify the cached checksums. Each case supplies an **input trace**: `manifest.json`, `evidence.jsonl`, and referenced artifacts. Reading those events reconstructs the recorded history in memory. Tests compare a summary with the **expected result** in `expected.json`, and verify complete accepted payloads, artifact bytes, and rejected-event behavior.

Reader tests load supplied evidence. Producer tests record equivalent evidence through the public API and reopen it. Cases cover capture limits and aliases, unexecuted available actions, independent sequences, action execution results, optional oracle severity, metadata, and temporal results. See the shared [conformance explanation](https://github.com/ferpasri/testar-evidence-specs/blob/v0.3.0/conformance/README.md).

After preparation, tests use the cache offline. Repeat preparation before checking compatibility with the latest release, including in CI. Deleting `target/` removes both the cache and generated output.

## Run metadata and versions

Run and sequence properties retain optional origin, test objective, adapter name, and adapter version using the [shared metadata names](https://github.com/ferpasri/testar-evidence-specs/blob/v0.3.0/docs/run-metadata.md). Supplied descriptions retain Unicode and spacing; omitted descriptions remain absent.

Releases use `0.X.Y`. The `0.X` release series is shared with the specs and related evidence projects. Each project increments `Y` independently within that series. Evidence compatibility follows its `schema_version` and validation rules.

See [SPEC.md](docs/SPEC.md) for implemented behavior and [TODO.md](TODO.md) for planned work.
