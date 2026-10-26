# TESTAR Evidence for Python

A standalone library that validates, saves, and reads portable GUI test evidence following [TESTAR Evidence specs](https://github.com/ferpasri/testar-evidence-specs).

Adapters supply captured records and oracle results. The library validates references and lifecycle rules before writing, and preserves the reported evidence for inspection or separate abstraction.

## Build and test

Use the Python version required by [pyproject.toml](pyproject.toml). From this repository:

```sh
python -m pip install -e .
python -m tools.prepare_conformance
python -m unittest discover -s tests -p "test_*.py"
```

The library has no runtime dependencies; tests use standard `unittest`. To build an installable wheel:

```sh
python -m pip wheel --no-deps . --wheel-dir dist
```

## Import the library

A consuming project can install the checkout as a local dependency:

```sh
python -m pip install -e ../testar-evidence-python
```

Import public types from `testar_evidence`. The distribution name is `testar-evidence`.

## Recording and reading

`JsonlStore.create(directory, manifest)` starts a fresh evidence collection. Report `EvidenceEvent` values through `record`. `EvidenceReader.read(directory)` reads saved evidence; `JsonlStore.open(directory)` reopens it for continued recording. `ReconstructedEvidence` holds validated records and relationships for inspection.

See the [commented recording example](docs/examples.md) for a complete sequence with observations, available actions, an execution, and an oracle result. The [implementation specification](docs/SPEC.md) describes value handling, record access, and persistence.

## Shared conformance data

Preparation selects the newest published [specs release](https://github.com/ferpasri/testar-evidence-specs/releases), including prereleases, and downloads its cases into ignored `target/conformance/`. It verifies archive boundaries and records the selected release and checksums. Failed preparation preserves the previous selection. Tests use the verified cache offline; repeat preparation when checking a newer release.

Reader tests process each supplied trace. Producer tests record the same events through the public API and reopen the output. Both compare expected records, accepted payloads, rejection behavior, and artifacts. The specs own the [conformance contract](https://github.com/ferpasri/testar-evidence-specs/blob/main/conformance/README.md).

## Compatibility

[pyproject.toml](pyproject.toml) records the package version and build requirements. `SCHEMA_VERSION` in [src/testar_evidence/records.py](src/testar_evidence/records.py) identifies the supported evidence format. Related projects share their first two release numbers and advance patches independently; format compatibility is checked separately.

See [TODO](TODO.md) for remaining work. Shared meanings, including capture and metadata conventions, remain in the specs repository.
