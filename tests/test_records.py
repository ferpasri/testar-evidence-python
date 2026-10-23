from copy import deepcopy
from decimal import Decimal
import unittest

from testar_evidence import codec
from testar_evidence.errors import EvidenceError
from testar_evidence.records import (
    ActionExecutionResult, ArtifactReference, AvailableAction, EvidenceEvent, Manifest,
    Observation, ObservedValue, Oracle, OracleResult, OracleSeverity, OracleTarget,
    RecordedState, TestRun, Widget, validate_artifact, validate_event, validate_manifest, validate_path,
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
        record = Observation("observation", "sequence", 1, 1000,
            RecordedState("state", widgets=[Widget("button")]), "generated",
            [AvailableAction("activate", "button")]).to_dict()
        record.update(changes)
        return {"schema_version": 3, "id": "event", "event": {"event_type": "observation_recorded", "data": record}}

    def oracle_result(self, **changes):
        record = OracleResult("run", "Property is present", Oracle("oracle"),
            OracleTarget.widget_property("observation", "button", "ui.text"), "failed",
            test_sequence="sequence", observed_value=ObservedValue.absent()).to_dict()
        record.update(changes)
        return {"schema_version": 3, "id": "oracle", "event": {"event_type": "oracle_result_recorded", "data": record}}

    def test_manifest_and_event_builders_match_wire_structure(self):
        self.assertEqual(Manifest("evidence").to_dict(), {"evidence_id": "evidence", "schema_version": 3})
        event = EvidenceEvent("start", "test_run_started", TestRun("run", "generic", "source"))
        expected = {"schema_version": 3, "id": "start", "event": {"event_type": "test_run_started", "data": {
            "id": "run", "producer": "generic", "source_run": "source", "properties": {}}}}
        self.assertEqual(event.to_dict(), expected)
        self.assertEqual(EvidenceEvent.from_dict(expected).to_dict(), expected)

    def test_validated_values_are_independent_copies(self):
        event = self.observation()
        original = deepcopy(event)
        result = validate_event(event)
        result["event"]["data"]["recorded_state"]["widgets"].clear()
        self.assertEqual(event, original)

    def test_manifest_requires_supported_integer_version_and_known_fields(self):
        for value in ({"schema_version": True, "evidence_id": "evidence"},
                      {"schema_version": 4, "evidence_id": "evidence"},
                      {"schema_version": 3, "evidence_id": "evidence", "extra": 1},
                      {"schema_version": 3, "evidence_id": "  "}):
            with self.subTest(value=value):
                self.assert_error("invalid_record", lambda: validate_manifest(value))

    def test_identifiers_and_property_names_reject_control_characters(self):
        for properties in ({"": 1}, {"bad\nname": 1}):
            event = EvidenceEvent("start", "test_run_started", TestRun("run", "generic", "source", properties))
            self.assert_error("invalid_record", event.to_dict)
        self.assert_error("invalid_record", lambda: EvidenceEvent("bad\x7fname", "test_run_finished", {
            "test_run": "run", "reason": "completed"}).to_dict())

    def test_unknown_fields_and_invalid_field_types_are_rejected(self):
        event = self.observation()
        event["event"]["data"]["recorded_state"]["extra"] = 1
        self.assert_error("invalid_record", lambda: validate_event(event))
        for index in (-1, 1 << 64, True, Decimal("1.0"), "1"):
            with self.subTest(index=index):
                self.assert_error("invalid_record", lambda: validate_event(self.observation(observation_index=index)))

    def test_widget_parents_must_exist_and_form_an_acyclic_hierarchy(self):
        cases = [([Widget("a", "missing")], "invalid_reference"),
                 ([Widget("a", "b"), Widget("b", "a")], "invalid_record"),
                 ([Widget("a"), Widget("a")], "conflict")]
        for widgets, code in cases:
            with self.subTest(code=code):
                event = self.observation(recorded_state=RecordedState("state", widgets=widgets).to_dict(), available_actions=[])
                self.assert_error(code, lambda: validate_event(event))

    def test_available_actions_reference_local_widgets_and_must_be_unique(self):
        for actions, code in (([AvailableAction("activate", "missing")], "invalid_reference"),
                              ([AvailableAction("activate", "button")] * 2, "conflict")):
            event = self.observation(available_actions=codec.to_data(actions))
            self.assert_error(code, lambda: validate_event(event))

    def test_capture_and_available_action_scope_are_explicit(self):
        event = self.observation()
        del event["event"]["data"]["available_action_scope"]
        self.assert_error("invalid_record", lambda: validate_event(event))
        for scope in (None, "complete", "GENERATED"):
            self.assert_error("invalid_record", lambda: validate_event(self.observation(available_action_scope=scope)))
        event = self.observation()
        event["event"]["data"]["recorded_state"]["capture"] = "unavailable"
        self.assert_error("invalid_record", lambda: validate_event(event))

    def test_observed_values_distinguish_value_absence_unknown_and_omission(self):
        self.assertEqual(ObservedValue.value(None).to_dict(), {"value_status": "value", "value": None})
        self.assertEqual(ObservedValue.absent().to_dict(), {"value_status": "absent"})
        self.assertEqual(ObservedValue.unknown().to_dict(), {"value_status": "unknown"})
        omitted = self.oracle_result()
        self.assertNotIn("expected_value", omitted["event"]["data"])
        explicit_null = self.oracle_result(expected_value=None, observed_value={"value_status": "value", "value": None})
        self.assertIn("expected_value", validate_event(explicit_null)["event"]["data"])
        for observed in (None, {"value_status": "value"}, {"value_status": "absent", "value": None}):
            self.assert_error("invalid_record", lambda: validate_event(self.oracle_result(observed_value=observed)))

    def test_optional_oracle_fields_must_have_values_when_supplied(self):
        for oracle in ({"id": "oracle", "version": None}, {"id": "oracle", "parameters": None}):
            self.assert_error("invalid_record", lambda: validate_event(self.oracle_result(oracle=oracle)))
        for target in (OracleTarget.test_run(), OracleTarget.test_sequence(), OracleTarget.observation("o"),
                       OracleTarget.state_property("o", "x"), OracleTarget.widget("o", "w"),
                       OracleTarget.action_execution("e"), OracleTarget.observation_range("a", "b")):
            validate_event(self.oracle_result(oracle_target=target.to_dict()))
        result = self.oracle_result()
        del result["event"]["data"]["oracle_details"]
        self.assert_error("invalid_record", lambda: validate_event(result))

    def test_execution_results_preserve_return_and_error_meanings(self):
        for outcome, error, valid in (("returned", None, True), ("error", "runner error", True),
                                      ("returned", "error", False), ("error", None, False), (True, None, False)):
            event = EvidenceEvent("result", "action_execution_reported", ActionExecutionResult("execution", outcome, error))
            if valid:
                event.to_dict()
            else:
                self.assert_error("invalid_record", event.to_dict)

    def test_nullable_fields_are_required_even_when_no_target_or_error_exists(self):
        for record_type in ("available", "widget", "execution_result"):
            with self.subTest(record_type=record_type):
                if record_type == "execution_result":
                    event = EvidenceEvent("result", "action_execution_reported", {
                        "action_execution": "execution", "outcome": "returned"})
                    self.assert_error("invalid_record", event.to_dict)
                else:
                    event = self.observation()
                    data = event["event"]["data"]
                    if record_type == "available":
                        del data["available_actions"][0]["target_widget"]
                    else:
                        del data["recorded_state"]["widgets"][0]["parent"]
                    self.assert_error("invalid_record", lambda: validate_event(event))

    def test_severity_preserves_label_and_number_and_rejects_blank_label(self):
        result = validate_event(self.oracle_result(oracle_severity=OracleSeverity("  High  ", Decimal("-1.25"))))
        self.assertEqual({"label": "  High  ", "value": Decimal("-1.25")}, result["event"]["data"]["oracle_severity"])
        for severity in ({"label": " "}, {"label": "High", "value": None}, {"label": "High", "value": True}):
            self.assert_error("invalid_oracle_severity", lambda: validate_event(self.oracle_result(oracle_severity=severity)))
        self.assertNotIn("oracle_severity", self.oracle_result()["event"]["data"])

    def test_artifact_paths_hashes_and_unsigned_dimensions_are_validated(self):
        artifact = ArtifactReference("image", "artifacts/image.png", "image/png", "a" * 64, 0, (1 << 64) - 1)
        validate_artifact(artifact.to_dict())
        for path in ("../image.png", "/image.png", "a\\b", "a//b", "a/CON.png", "a/end.", "a/end ", "C:/x"):
            with self.subTest(path=path):
                self.assert_error("invalid_record", lambda: validate_path(path))
        for change in ({"sha256": "0"}, {"sha256": "A" * 64}, {"width": -1}, {"height": True}, {"width": 1 << 64}):
            value = {**artifact.to_dict(), **change}
            self.assert_error("invalid_record", lambda: validate_artifact(value))
