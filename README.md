# TESTAR evidence for Python

A standalone Python library for validating and saving portable test execution evidence in the shared [TESTAR evidence format](https://github.com/ferpasri/testar-evidence-specs).

Tool adapters supply runs, observed states, widgets, properties, discovered actions, executions, screenshots, and test-oracle results. The library checks that the records and their references are valid, then saves them as portable evidence files.

Every visit remains a separate observation. Each completed action connects the observation before the action to its resulting observation, forming a state-action-state execution trace. Discovered candidates remain available even when never selected. Consumers can use the saved evidence to display traces or build derived models.

Captured properties and oracle evaluations are separate evidence. Check results identify what was evaluated, the rule, its outcome, and any reported reading. The library preserves supplied property names, values, capture descriptions, known omissions, and declared aliases.

## Build and test

Use Python 3.11 or later. Run these commands from this repository:

```sh
python -m pip install -e .
python -m tools.prepare_conformance
python -m unittest discover -s tests -p "test_*.py"
```

The library has no runtime dependencies. Installation uses the packaging tools declared in `pyproject.toml`; tests use Python's standard `unittest` module.

A wheel is an installable Python package stored in a `.whl` file. Build one when you want to distribute the packaged library:

```sh
python -m pip wheel --no-deps . --wheel-dir dist
```

## Import the library

A consuming project can install this checkout as a local dependency:

```sh
python -m pip install -e ../testar-evidence-python
```

Import the public API from `testar_evidence`. The distribution name is `testar-evidence` and its version is `0.2.2`. `EvidenceEvent` describes one reported event together with its identity and format version. The storage API accepts an explicit, fresh output directory:

```python
from testar_evidence import EvidenceEvent, JsonlStore, Manifest, Run

with JsonlStore.create("target/my-evidence", Manifest("example:model:1")) as store:
    store.record(EvidenceEvent("example:event:1", "run_started",
        Run("example:run:1", "example", "1")))
    store.record(EvidenceEvent("example:event:2", "run_finished",
        {"run": "example:run:1", "reason": "completed"}))
```

The output directory contains `manifest.json`, `evidence.jsonl`, and any referenced files under `artifacts/`. `JsonlStore.create(...)` refuses an existing manifest or evidence log so it cannot overwrite a previous trace. The runnable example below chooses a new trace directory automatically.

See [local_model.py](examples/local_model.py) for observations, candidate actions, one completed execution, and a property evaluation:

```sh
python examples/local_model.py target/traces --sut demo --test-objective submit-form --origin "Example script"
```

The first argument is the parent directory. Each execution creates a child directory combining the application name, test objective, and local timestamp, for example:

```text
target/traces/demo_submit-form_trace-2026-10-22_15-30-12-123/
```

The filename labels come from `--sut` and `--test-objective`. Characters unsuitable for filenames are replaced and each label is limited to 32 characters. If the name already exists, a numeric suffix such as `-2` reserves a different directory. Each execution also has distinct model and run identities. The original labels are retained in run properties. The optional `--origin` names the source of the demonstration. Without a test objective, the filename uses `run` and the objective property is omitted.

This example records demonstration data; the labels do not launch an application or select a test script. Load a complete trace directory in the evidence dashboard.

## Reading and inspecting saved traces

The library keeps track of earlier records while writing so it can check new ones. For example, a selected action must refer to an existing observation and one of its candidates.

`EvidenceReader.read(directory)` reads saved events and builds the corresponding records and links in memory. This supports inspecting a trace and checking that files were saved correctly. `JsonlStore.open(directory)` reads existing evidence before continuing to append valid events.

The returned `EvidenceModel` also offers simple Python methods: `observations_for_run(...)` lists visits in a run, `executions_for_run(...)` lists selected action occurrences, and `outgoing(...)` lists completed action links leaving an observation. These methods return independent copies. Reading and these lookup methods support recording and inspection; consumers decide how to group observations into derived models.

## Shared conformance data

Each case supplies an input trace: `manifest.json`, `evidence.jsonl` (one recorded event per line), and referenced artifacts. The library reads those events to build the corresponding records and links in memory (the reconstructed model). Tests compare a summary of that model with `expected.json`, which describes the expected records or rejection. They also verify complete accepted event contents and artifact bytes. The reconstructed model preserves concrete evidence; consumers can derive abstract or merged models separately.

Preparation queries the public [specs releases](https://github.com/ferpasri/testar-evidence-specs/releases) and selects the newest publication, including prereleases. It downloads the conformance files into ignored `target/conformance/` and records the release and file checksums. Tests verify that selection and use the prepared files offline. Repeat preparation to check the newest release, including before CI tests. Deleting `target/` removes the prepared data and example output.

Reader tests open each supplied trace. Producer tests submit its events through the recording API, reopen the generated files, and compare against the same expectations. Invalid cases check the reported error and preserve the previously accepted evidence. The shared [conformance explanation](https://github.com/ferpasri/testar-evidence-specs/blob/main/conformance/README.md#input-trace-reconstructed-model-and-expected-result) describes these file roles and comparisons.

## Run metadata

Run properties support optional origin, test objective, adapter name, and adapter version using the [shared run metadata names](https://github.com/ferpasri/testar-evidence-specs/blob/main/docs/run-metadata.md). Supplied descriptions keep their original text; omitted descriptions stay omitted. See [the implementation specification](docs/SPEC.md#run-metadata) for their use.

## Release versions

Releases use `0.X.Y`. The `0.X` release series is shared with the specs and related evidence projects. Each project increments `Y` independently for fixes and improvements within that series. Evidence compatibility follows its `schema_version` and validation rules.

See [SPEC.md](docs/SPEC.md) for the implemented behavior and [TODO.md](TODO.md) for planned work.

