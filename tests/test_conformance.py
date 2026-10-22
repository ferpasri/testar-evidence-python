"""Reader and producer tests using every case from the prepared specs release."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from testar_evidence import EvidenceError, EvidenceModel, EvidenceReader, JsonlStore
from tests.support import conformance_cases, read_events, read_json


def _error(error: EvidenceError, envelope: dict) -> dict:
    return {"code": error.code, "field": error.field, "event": envelope["id"]}


def _artifacts(envelope: dict) -> list[dict]:
    event = envelope["event"]
    return event["data"]["artifacts"] if event["kind"] == "observed" else []


def _read_case(source: Path) -> None:
    assertion = unittest.TestCase()
    expected = read_json(source / "expected.json")
    events = read_events(source)
    before = (source / "evidence.jsonl").read_bytes()
    if expected["status"] == "accepted":
        model = EvidenceReader.read(source)
        assertion.assertEqual(expected, model.summary())
        assertion.assertEqual(events, model.events)
    else:
        with assertion.assertRaises(EvidenceError) as failure:
            EvidenceReader.read(source)
        assertion.assertEqual(expected["error"]["code"], failure.exception.code)
        assertion.assertEqual(expected["error"]["field"], failure.exception.field)
        model = EvidenceModel(read_json(source / "manifest.json"))
        accepted = []
        rejected = None
        for event in events:
            previous = deepcopy(model.summary())
            previous_events = deepcopy(model.events)
            try:
                assertion.assertTrue(model.apply(event))
                accepted.append(event)
            except EvidenceError as error:
                rejected = _error(error, event)
                assertion.assertEqual(previous, model.summary())
                assertion.assertEqual(previous_events, model.events)
                break
        assertion.assertEqual(expected["error"], rejected)
        assertion.assertEqual(expected["accepted_prefix"], model.summary())
        assertion.assertEqual(accepted, model.events)
    assertion.assertEqual(before, (source / "evidence.jsonl").read_bytes())


def _produce_case(source: Path) -> None:
    assertion = unittest.TestCase()
    expected = read_json(source / "expected.json")
    manifest = read_json(source / "manifest.json")
    accepted = []
    rejected = None
    with TemporaryDirectory(prefix="testar-evidence-conformance-") as temporary:
        destination = Path(temporary) / "produced"
        with JsonlStore.create(destination, manifest) as store:
            for event in read_events(source):
                for artifact in _artifacts(event):
                    content = (source / artifact["path"]).read_bytes()
                    reference = store.put_artifact(content, artifact["media_type"],
                        width=artifact.get("width"), height=artifact.get("height"))
                    assertion.assertEqual(artifact, reference)
                    assertion.assertEqual(content, store.read_artifact(reference))
                previous = deepcopy(store.model.summary())
                previous_events = deepcopy(store.model.events)
                before = (destination / "evidence.jsonl").read_bytes()
                try:
                    assertion.assertTrue(store.record(event))
                except EvidenceError as error:
                    rejected = _error(error, event)
                    assertion.assertEqual(previous, store.model.summary())
                    assertion.assertEqual(previous_events, store.model.events)
                    assertion.assertEqual(before, (destination / "evidence.jsonl").read_bytes())
                    break
                accepted.append(event)
                after = (destination / "evidence.jsonl").read_bytes()
                current = deepcopy(store.model.summary())
                assertion.assertFalse(store.record(deepcopy(event)),
                    "Identical retries must not append a second record")
                assertion.assertEqual(after, (destination / "evidence.jsonl").read_bytes())
                assertion.assertEqual(current, store.model.summary())
                assertion.assertEqual(accepted, store.model.events)
            result = store.model.summary()
        if expected["status"] == "accepted":
            assertion.assertIsNone(rejected)
            assertion.assertEqual(expected, result)
        else:
            assertion.assertEqual(expected["error"], rejected)
            assertion.assertEqual(expected["accepted_prefix"], result)
        with JsonlStore.open(destination) as reopened:
            assertion.assertEqual(result, reopened.model.summary())
            assertion.assertEqual(accepted, reopened.model.events)
            for event in accepted:
                for artifact in _artifacts(event):
                    assertion.assertEqual((source / artifact["path"]).read_bytes(),
                        reopened.read_artifact(artifact))
        assertion.assertEqual(manifest, read_json(destination / "manifest.json"))
        assertion.assertEqual(accepted, read_events(destination),
            "The complete accepted event payloads must survive storage")
        assertion.assertEqual(result, EvidenceReader.read(destination).summary())


def _case_test(operation, source: Path, description: str):
    def run_case():
        operation(source)

    run_case.__name__ = f"{operation.__name__}_{source.name}"
    return unittest.FunctionTestCase(run_case, description=description)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    try:
        cases = conformance_cases()
    except OSError:
        suite.addTest(unittest.FunctionTestCase(conformance_cases,
            description="Prepare and verify the shared specs cases before testing"))
        return suite
    for case in cases:
        suite.addTest(_case_test(_read_case, case, f"Read shared case: {case.name}"))
        suite.addTest(_case_test(_produce_case, case, f"Produce and reopen shared case: {case.name}"))
    return suite
