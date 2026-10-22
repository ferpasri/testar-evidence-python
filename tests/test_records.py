from copy import deepcopy
from decimal import Decimal
import unittest

from testar_evidence import codec
from testar_evidence.errors import EvidenceError
from testar_evidence.records import (
    ActionCandidate,
    ArtifactReference,
    CheckReading,
    CheckResult,
    CheckRule,
    CheckTarget,
    ConcreteState,
    EvidenceEvent,
    Manifest,
    Observation,
    Run,
    Widget,
    validate_artifact,
    validate_event,
    validate_manifest,
    validate_path,
)


class JsonCodecTests(unittest.TestCase):
    def test_numbers_keep_exact_integer_and_decimal_values(self):
        text = '{"integer":18446744073709551615,"decimal":0.12345678901234567890123456789}'
        value = codec.loads(text)
        self.assertEqual(value["integer"], (1 << 64) - 1)
        self.assertIsInstance(value["integer"], int)
        self.assertEqual(value["decimal"], Decimal("0.12345678901234567890123456789"))
        self.assertEqual(codec.loads(codec.dumps(value)), value)

    def test_duplicate_fields_nonfinite_values_and_trailing_input_are_rejected(self):
        for text in ('{"value":1,"value":2}', '{"nested":{"v":1,"v":2}}',
                     'NaN', 'Infinity', '-Infinity', '{} {}'):
            with self.subTest(text=text), self.assertRaises(EvidenceError) as caught:
                codec.loads(text)
            self.assertEqual(caught.exception.code, "invalid_record")
        for value in (float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(value=value), self.assertRaises(EvidenceError):
                codec.dumps(value)

    def test_nesting_limit_ignores_brackets_inside_strings(self):
        self.assertEqual(codec.loads('"[{}]"'), "[{}]")
        codec.loads("[" * 64 + "0" + "]" * 64)
        with self.assertRaises(EvidenceError):
            codec.loads("[" * 65 + "0" + "]" * 65)
        value = []
        for _ in range(63):
            value = [value]
        codec.dumps(value)
        with self.assertRaises(EvidenceError):
            codec.dumps([value])

    def test_large_integer_readings_do_not_depend_on_python_digit_conversion_limit(self):
        text = "9" * 5000
        value = codec.loads(text)
        self.assertIsInstance(value, int)
        self.assertEqual(codec.dumps(value), text)
        self.assertEqual(codec.dumps(codec.loads("-" + text)), "-" + text)

    def test_unicode_text_is_portable_and_unpaired_surrogates_are_rejected(self):
        self.assertEqual(codec.loads('"\\ud83d\\ude00"'), "😀")
        self.assertEqual(codec.loads(codec.dumps("á 😀")), "á 😀")
        for text in ('"\\ud800"', '{"\\udfff":1}'):
            with self.subTest(text=text), self.assertRaises(EvidenceError):
                codec.loads(text)
        with self.assertRaises(EvidenceError):
            codec.dumps("\ud800")
        self.assertEqual(codec.to_data((1, "two")), [1, "two"])
        with self.assertRaises(EvidenceError):
            codec.to_data({1, 2})

    def test_copy_rejects_cycles_and_nontext_keys(self):
        cyclic = []
        cyclic.append(cyclic)
        for value in (cyclic, {1: "numeric key"}, {"unsupported": object()}):
            with self.subTest(value=type(value).__name__), self.assertRaises(EvidenceError):
                codec.to_data(value)

    def test_sorted_output_is_deterministic_and_boolean_remains_distinct(self):
        self.assertEqual(codec.dumps({"b": 1, "a": True}, sort_keys=True), '{"a":true,"b":1}')
        self.assertNotEqual(codec.dumps(True), codec.dumps(1))


class RecordValidationTests(unittest.TestCase):
    def assert_error(self, code, operation):
        with self.assertRaises(EvidenceError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def observation(self, **changes):
        record = Observation("observation", "run", 1, 1000,
                             ConcreteState("snapshot", widgets=[Widget("button")]),
                             [ActionCandidate("activate", "button")]).to_dict()
        record.update(changes)
        return {"schema_version": 2, "id": "event", "event": {"kind": "observed", "data": record}}

    def check(self, **changes):
        record = CheckResult("run", "Property is present", CheckRule("rule"),
                             CheckTarget.widget_property("observation", "button", "ui.text"),
                             "failed", actual=CheckReading.absent()).to_dict()
        record.update(changes)
        return {"schema_version": 2, "id": "check", "event": {"kind": "checked", "data": record}}

    def test_manifest_and_event_builders_match_wire_structure(self):
        self.assertEqual(Manifest("model").to_dict(), {"model": "model", "schema_version": 2})
        event = EvidenceEvent("start", "run_started", Run("run", "generic", "source"))
        expected = {"schema_version": 2, "id": "start", "event": {"kind": "run_started", "data": {
            "id": "run", "producer": "generic", "source_run": "source", "properties": {}}}}
        self.assertEqual(event.to_dict(), expected)
        self.assertEqual(EvidenceEvent.from_dict(expected).to_dict(), expected)

    def test_validated_values_are_independent_copies(self):
        event = self.observation()
        original = deepcopy(event)
        result = validate_event(event)
        result["event"]["data"]["state"]["widgets"].clear()
        self.assertEqual(event, original)

    def test_manifest_requires_supported_integer_version_and_known_fields(self):
        for value in ({"schema_version": True, "model": "model"}, {"schema_version": 3, "model": "model"},
                      {"schema_version": 2, "model": "model", "extra": 1},
                      {"schema_version": 2, "model": "  "}):
            with self.subTest(value=value):
                self.assert_error("invalid_record", lambda: validate_manifest(value))

    def test_identifiers_and_property_names_reject_control_characters(self):
        for properties in ({"": 1}, {"bad\nname": 1}):
            event = EvidenceEvent("start", "run_started", Run("run", "generic", "source", properties))
            self.assert_error("invalid_record", event.to_dict)
        self.assert_error("invalid_record", lambda: EvidenceEvent("bad\x7fname", "run_finished", {
            "run": "run", "reason": "completed"}).to_dict())

    def test_unknown_fields_and_invalid_field_types_are_rejected(self):
        event = self.observation()
        event["event"]["data"]["state"]["extra"] = 1
        self.assert_error("invalid_record", lambda: validate_event(event))
        for sequence in (-1, 1 << 64, True, Decimal("1.0"), "1"):
            with self.subTest(sequence=sequence):
                self.assert_error("invalid_record", lambda: validate_event(self.observation(sequence=sequence)))

    def test_widget_parents_must_exist_and_form_an_acyclic_hierarchy(self):
        cases = [([Widget("a", "missing")], "unknown_reference"),
                 ([Widget("a", "b"), Widget("b", "a")], "invalid_record"),
                 ([Widget("a"), Widget("a")], "conflict")]
        for widgets, code in cases:
            with self.subTest(code=code):
                event = self.observation(state=ConcreteState("snapshot", widgets=widgets).to_dict(), candidates=[])
                self.assert_error(code, lambda: validate_event(event))

    def test_candidates_reference_local_widgets_and_must_be_unique(self):
        for candidates, code in (([ActionCandidate("activate", "missing")], "unknown_reference"),
                                 ([ActionCandidate("activate", "button")] * 2, "conflict")):
            event = self.observation(candidates=codec.to_data(candidates))
            self.assert_error(code, lambda: validate_event(event))

    def test_capture_and_candidate_scope_have_explicit_meanings(self):
        event = self.observation()
        del event["event"]["data"]["candidate_scope"]
        validate_event(event)
        for scope in (None, "complete", "GENERATED"):
            self.assert_error("invalid_record", lambda: validate_event(self.observation(candidate_scope=scope)))
        event = self.observation()
        event["event"]["data"]["state"]["capture"] = "unavailable"
        self.assert_error("invalid_record", lambda: validate_event(event))

    def test_property_readings_distinguish_missing_capture_absence_and_null(self):
        self.assertEqual(CheckReading.value(None).to_dict(), {"kind": "value", "value": None})
        self.assertEqual(CheckReading.absent().to_dict(), {"kind": "absent"})
        self.assertEqual(CheckReading.unknown().to_dict(), {"kind": "unknown"})
        omitted = self.check()
        self.assertNotIn("expected", omitted["event"]["data"])
        explicit_null = self.check(expected=None, actual={"kind": "value", "value": None})
        self.assertIn("expected", validate_event(explicit_null)["event"]["data"])
        for actual in (None, {"kind": "value"}, {"kind": "absent", "value": None}):
            self.assert_error("invalid_record", lambda: validate_event(self.check(actual=actual)))

    def test_optional_rule_fields_must_have_values_when_supplied(self):
        for rule in ({"id": "rule", "version": None}, {"id": "rule", "parameters": None}):
            self.assert_error("invalid_record", lambda: validate_event(self.check(rule=rule)))
        for target in (CheckTarget.run(), CheckTarget.observation("o"), CheckTarget.state_property("o", "x"),
                       CheckTarget.widget("o", "w"), CheckTarget.execution("e"), CheckTarget.sequence("a", "b")):
            validate_event(self.check(target=target.to_dict()))

    def test_accepted_submission_cannot_report_an_error(self):
        for accepted, error, valid in ((True, None, True), (False, "rejected", True),
                                       (True, "error", False), (1, None, False), (False, 7, False)):
            event = EvidenceEvent("submission", "action_submitted", {
                "execution": "execution", "accepted": accepted, "error": error})
            if valid:
                event.to_dict()
            else:
                self.assert_error("invalid_record", event.to_dict)

    def test_artifact_paths_are_portable_and_digest_and_dimensions_validated(self):
        artifact = ArtifactReference("image", "artifacts/image.png", "image/png", "a" * 64, 1, 2)
        validate_artifact(artifact.to_dict())
        for path in ("../image.png", "/image.png", "a\\b", "a//b", "a/CON.png", "a/end.", "a/end ", "C:/x"):
            with self.subTest(path=path):
                self.assert_error("invalid_record", lambda: validate_path(path))
        for change in ({"sha256": "0"}, {"width": 0}, {"height": True}, {"width": 1 << 32}):
            value = {**artifact.to_dict(), **change}
            self.assert_error("invalid_record", lambda: validate_artifact(value))


if __name__ == "__main__":
    unittest.main()
