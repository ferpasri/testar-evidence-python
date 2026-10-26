# Recording example

Run these commands from the repository root.

The example writes demonstration evidence through the public API. It illustrates recording boundaries and metadata; the [implementation specification](SPEC.md) defines behavior.

The commented [record_evidence.py](../examples/record_evidence.py) example records observations, two available button actions, one completed transition, and a property oracle result:

```sh
python examples/record_evidence.py target/traces --sut demo --test-objective submit-form
```

Each execution reserves a distinct timestamped directory, such as `demo_submit-form_trace-2026-10-22_15-30-12-123/`. Filename labels are sanitized; optional origin and test objective descriptions retain their supplied text in run properties. The example accepts `--origin`. An omitted test objective uses `run` only as a filename label. Load the complete generated directory in the dashboard.
