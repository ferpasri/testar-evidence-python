"""Independent histories, explicit transitions, and oracle references."""

from copy import deepcopy
from decimal import Decimal
import unittest

from testar_evidence import (
    ActionDescription, ActionExecution, ActionExecutionResult, AvailableAction,
    EvidenceError, EvidenceEvent, ReconstructedEvidence, Manifest, Observation, ObservedValue,
    Oracle, OracleResult, OracleSeverity, OracleTarget, RecordedState, RecordedTransition,
    TestRun, TestSequence, Widget,
)


class ReconstructedEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.evidence = ReconstructedEvidence(Manifest("example:evidence"))
        self.next_event = 0
        self.accept("test_run_started", TestRun("test-run", "example", "source:run"))
        self.accept("test_sequence_started", TestSequence("sequence", "test-run", 1, "source:sequence"))
        self.accept("action_described", ActionDescription("activate", "activate"))
        self.accept("action_described", ActionDescription("refresh", "refresh"))
        self.accept("observation_recorded", self.observation())

    def envelope(self, event_type, data):
        self.next_event += 1
        return EvidenceEvent("event:" + str(self.next_event), event_type, data)

    def accept(self, event_type, data):
        envelope = self.envelope(event_type, data).to_dict()
        self.assertTrue(self.evidence.apply(envelope))
        return envelope

    @staticmethod
    def observation(identifier="observation:1", index=1, sequence="sequence", recorded_state=None):
        state = RecordedState(recorded_state or "state:" + identifier,
            {"ui.title": "Example", "ui.available": True},
            [Widget("button", properties={"ui.role": "button"})])
        return Observation(identifier, sequence, index, index * 1000, state, "generated",
            [AvailableAction("activate", "button"), AvailableAction("refresh")]).to_dict()

    @staticmethod
    def execution(identifier="execution:1", source="observation:1", sequence="sequence"):
        return ActionExecution(identifier, sequence, source, "activate", "button", {"button": "primary"})

    @staticmethod
    def oracle_result(target=None, **changes):
        result = OracleResult("test-run", "Example test oracle", Oracle("oracle", "1"),
            target or OracleTarget.test_run(), "passed").to_dict()
        result.update(changes)
        return result

    def reject(self, event_type, data, code, field=None):
        previous = self.evidence.summary()
        events = self.evidence.events
        with self.assertRaises(EvidenceError) as failure:
            self.evidence.apply(self.envelope(event_type, data))
        self.assertEqual(code, failure.exception.code)
        self.assertEqual(field, failure.exception.field)
        self.assertEqual(previous, self.evidence.summary())
        self.assertEqual(events, self.evidence.events)

    def finish(self):
        self.accept("test_sequence_finished", {"test_sequence": "sequence", "reason": "completed"})
        self.accept("test_run_finished", {"test_run": "test-run", "reason": "completed"})

    def test_validate_is_side_effect_free_and_identical_retries_are_idempotent(self):
        envelope = self.envelope("oracle_result_recorded", self.oracle_result())
        previous = self.evidence.summary()
        self.assertTrue(self.evidence.validate(envelope))
        self.assertTrue(self.evidence.validate(envelope))
        self.assertEqual(previous, self.evidence.summary())
        self.assertTrue(self.evidence.apply(envelope))
        self.assertFalse(self.evidence.validate(envelope))
        self.assertFalse(self.evidence.apply(envelope))
        self.assertEqual(previous["counts"]["evidence_events"] + 1, self.evidence.event_count)
        conflicting = envelope.to_dict()
        conflicting["event"]["data"]["oracle_status"] = "failed"
        with self.assertRaises(EvidenceError):
            self.evidence.apply(conflicting)
        self.assertEqual("passed", self.evidence.oracle_results[0]["oracle_status"])

    def test_original_inputs_and_returned_records_cannot_mutate_history(self):
        envelope = self.accept("oracle_result_recorded", self.oracle_result())
        envelope["event"]["data"]["oracle_status"] = "failed"
        self.evidence.events[-1]["event"]["data"]["oracle_status"] = "error"
        self.evidence.oracle_results[0]["oracle_status"] = "unknown"
        self.evidence.observations["observation:1"]["recorded_state"]["properties"]["ui.title"] = "Changed"
        self.evidence.recorded_states["state:observation:1"]["properties"]["ui.title"] = "Changed"
        self.evidence.action_descriptions["activate"]["properties"]["changed"] = True
        self.evidence.test_runs["test-run"]["status"] = "finished"
        self.evidence.manifest["evidence_id"] = "changed"
        self.evidence.summary()["oracle_results"][0]["oracle_status"] = "error"
        self.assertEqual("passed", self.evidence.oracle_results[0]["oracle_status"])
        self.assertEqual("Example", self.evidence.recorded_state("state:observation:1")["properties"]["ui.title"])
        self.assertEqual({}, self.evidence.action_description("activate")["properties"])
        self.assertEqual("running", self.evidence.test_run("test-run")["status"])
        self.assertEqual("example:evidence", self.evidence.manifest["evidence_id"])

    def test_snapshot_retains_independent_history_and_lifecycle(self):
        self.accept("action_selected", self.execution())
        snapshot = self.evidence.snapshot()
        self.accept("action_execution_reported", ActionExecutionResult("execution:1", "returned"))
        snapshot.apply(self.envelope("test_sequence_finished", {"test_sequence": "sequence", "reason": "stopped"}))
        self.assertEqual("awaiting_resulting_observation", self.evidence.action_execution("execution:1")["status"])
        self.assertEqual("incomplete", snapshot.action_execution("execution:1")["status"])
        self.assertEqual("running", self.evidence.test_sequence("sequence")["status"])
        self.assertEqual("finished", snapshot.test_sequence("sequence")["status"])

    def test_completed_execution_creates_only_the_explicit_recorded_transition(self):
        self.accept("action_selected", self.execution())
        self.accept("action_execution_reported", ActionExecutionResult("execution:1", "returned"))
        self.accept("observation_recorded", self.observation("observation:2", 2))
        self.assertEqual([], self.evidence.recorded_transitions)
        self.accept("transition_recorded", RecordedTransition("execution:1", "observation:2"))
        expected = [{"action_execution": "execution:1", "test_sequence": "sequence", "source_observation": "observation:1",
                     "resulting_observation": "observation:2", "action_description": "activate", "target_widget": "button"}]
        self.assertEqual(expected, self.evidence.recorded_transitions)
        self.assertEqual(expected, self.evidence.outgoing("observation:1"))
        self.assertEqual(expected, self.evidence.incoming("observation:2"))
        self.assertEqual([], self.evidence.incoming("observation:1"))
        self.assertEqual("completed", self.evidence.executions_for_sequence("sequence")[0]["status"])
        self.assertEqual(2, len(self.evidence.available_actions("observation:1")))
        self.assertEqual("refresh", self.evidence.available_actions("observation:1")[1]["action_description"])

    def test_execution_error_keeps_message_and_allows_another_selection(self):
        self.accept("action_selected", self.execution())
        self.accept("action_execution_reported", ActionExecutionResult("execution:1", "error", "Target is unavailable"))
        self.accept("observation_recorded", self.observation("observation:2", 2))
        self.reject("transition_recorded", RecordedTransition("execution:1", "observation:2"),
                    "invalid_lifecycle", "action_execution")
        self.accept("action_selected", self.execution("execution:2", "observation:2"))
        record = self.evidence.action_execution("execution:1")
        self.assertEqual("Target is unavailable", record["execution_result"]["error"])
        self.assertEqual("failed", record["status"])
        self.assertIsNone(record["resulting_observation"])
        self.assertEqual([], self.evidence.recorded_transitions)

    def test_sequence_end_marks_unfinished_execution_incomplete(self):
        for returned in (False, True):
            with self.subTest(returned=returned):
                self.setUp()
                self.accept("action_selected", self.execution())
                if returned:
                    self.accept("action_execution_reported", ActionExecutionResult("execution:1", "returned"))
                self.finish()
                self.assertEqual("incomplete", self.evidence.action_execution("execution:1")["status"])
                self.assertEqual([], self.evidence.recorded_transitions)
                self.reject("transition_recorded", RecordedTransition("execution:1", "observation:1"),
                            "invalid_lifecycle", "test_sequence")

    def test_selection_requires_latest_observation_exact_available_action_and_no_unfinished_action(self):
        wrong_target = self.execution().to_dict()
        wrong_target["target_widget"] = None
        self.reject("action_selected", wrong_target, "invalid_reference", "action_description")
        self.accept("observation_recorded", self.observation("observation:2", 2))
        self.reject("action_selected", self.execution(), "invalid_reference", "source_observation")
        self.accept("action_selected", self.execution(source="observation:2"))
        self.reject("action_selected", self.execution("execution:2", "observation:2"),
                    "invalid_lifecycle", "test_sequence")

    def test_transition_requires_returned_execution_and_later_observation_in_same_sequence(self):
        self.accept("action_selected", self.execution())
        self.reject("transition_recorded", RecordedTransition("execution:1", "observation:1"),
                    "invalid_lifecycle", "action_execution")
        self.accept("action_execution_reported", ActionExecutionResult("execution:1", "returned"))
        self.reject("transition_recorded", RecordedTransition("execution:1", "observation:1"),
                    "invalid_transition", "resulting_observation")
        self.accept("test_sequence_started", TestSequence("other", "test-run", 2, "source:other"))
        self.accept("observation_recorded", self.observation("other:observation", 2, "other"))
        for resulting in ("other:observation", "unknown"):
            self.reject("transition_recorded", RecordedTransition("execution:1", resulting),
                        "invalid_transition", "resulting_observation")

    def test_observations_require_described_actions_and_increasing_index(self):
        self.reject("observation_recorded", self.observation("observation:2", 1), "invalid_order", "observation_index")
        unknown_action = self.observation("observation:2", 2)
        unknown_action["available_actions"][0]["action_description"] = "unknown"
        self.reject("observation_recorded", unknown_action, "invalid_reference", "available_actions.action_description")
        self.reject("observation_recorded", self.observation("observation:2", 2, "unknown"),
                    "invalid_reference", "test_sequence")

    def test_repeated_recorded_state_keeps_distinct_observations_and_fixed_content(self):
        self.accept("observation_recorded", self.observation("observation:2", 2, recorded_state="state:observation:1"))
        self.assertEqual(2, len(self.evidence.observations_for_recorded_state("state:observation:1")))
        self.assertEqual(1, len(self.evidence.recorded_states))
        changed = self.observation("observation:3", 3, recorded_state="state:observation:1")
        changed["recorded_state"]["properties"]["ui.title"] = "Different"
        self.reject("observation_recorded", changed, "conflict")

    def test_numeric_retries_preserve_precision_and_distinguish_booleans(self):
        oracle = self.oracle_result(oracle_details={"number": Decimal("1.00"),
            "precise": Decimal("0.123456789012345678901234567891"), "boolean": True, "huge": 10 ** 4999})
        envelope = self.accept("oracle_result_recorded", oracle)
        equivalent = deepcopy(envelope)
        equivalent["event"]["data"]["oracle_details"]["number"] = 1
        equivalent["event"]["data"]["oracle_details"]["huge"] = Decimal(10 ** 4999)
        self.assertFalse(self.evidence.apply(equivalent))
        for key, value in (("boolean", 1), ("precise", Decimal("0.123456789012345678901234567892"))):
            changed = deepcopy(equivalent)
            changed["event"]["data"]["oracle_details"][key] = value
            with self.assertRaises(EvidenceError) as failure:
                self.evidence.apply(changed)
            self.assertEqual("conflict", failure.exception.code)
        self.assertEqual(oracle["oracle_details"], self.evidence.oracle_results[0]["oracle_details"])

    def test_equal_number_representations_preserve_state_and_action_identity(self):
        action = ActionDescription("numeric-action", "activate", {"value": Decimal("1.00")}).to_dict()
        self.accept("action_described", action)
        action["properties"]["value"] = 1
        self.accept("action_described", action)
        observation = self.observation("observation:2", 2, recorded_state="numeric-state")
        observation["recorded_state"]["properties"]["number"] = Decimal("1.00")
        self.accept("observation_recorded", observation)
        observation["id"] = "observation:3"
        observation["observation_index"] = 3
        observation["recorded_state"]["properties"]["number"] = 1
        self.accept("observation_recorded", observation)
        self.assertEqual(2, len(self.evidence.observations_for_recorded_state("numeric-state")))

    def test_late_oracle_results_remain_separate_and_keep_reported_status(self):
        self.accept("oracle_result_recorded", self.oracle_result(oracle_status="pending"))
        self.finish()
        self.accept("oracle_result_recorded", self.oracle_result(oracle_status="pending"))
        self.accept("oracle_result_recorded", self.oracle_result())
        self.assertEqual(["pending", "pending", "passed"], [value["oracle_status"] for value in self.evidence.oracle_results])
        self.reject("observation_recorded", self.observation("observation:2", 2), "invalid_lifecycle", "test_sequence")
        self.reject("test_run_finished", {"test_run": "test-run", "reason": "again"}, "invalid_lifecycle", "test_run")

    def test_property_oracle_can_report_established_absence(self):
        result = self.oracle_result(OracleTarget.widget_property("observation:1", "button", "ui.alt"),
            test_sequence="sequence", oracle_status="failed", observed_value=ObservedValue.absent())
        self.accept("oracle_result_recorded", result)
        self.assertEqual("absent", self.evidence.oracle_results[0]["observed_value"]["value_status"])

    def test_oracle_target_errors_identify_fields_and_preserve_accepted_history(self):
        for target, field in ((OracleTarget.widget("observation:1", "unknown"), "widget"),
                              (OracleTarget.observation("unknown"), "observation"),
                              (OracleTarget.action_execution("unknown"), "action_execution")):
            self.reject("oracle_result_recorded", self.oracle_result(target, test_sequence="sequence"),
                        "invalid_oracle_target", "oracle_target." + field)
        self.accept("observation_recorded", self.observation("observation:2", 2))
        self.reject("oracle_result_recorded", self.oracle_result(
            OracleTarget.observation_range("observation:2", "observation:1"), test_sequence="sequence"),
            "invalid_oracle_target", "oracle_target.last_observation")

    def test_oracle_targets_belong_to_declared_sequence_and_parent_run(self):
        self.accept("test_sequence_started", TestSequence("other", "test-run", 2, "other"))
        self.accept("observation_recorded", self.observation("other:observation", 1, "other"))
        self.reject("oracle_result_recorded", self.oracle_result(OracleTarget.observation("other:observation"),
            test_sequence="sequence"), "invalid_oracle_target", "oracle_target.observation")
        self.reject("oracle_result_recorded", self.oracle_result(test_sequence="sequence"),
                    "invalid_oracle_target", "test_sequence")
        self.reject("oracle_result_recorded", self.oracle_result(OracleTarget.test_sequence()),
                    "invalid_oracle_target", "test_sequence")

    def test_lookups_order_observations_by_index_and_summaries_by_id(self):
        self.accept("observation_recorded", self.observation("observation:10", 10))
        self.accept("observation_recorded", self.observation("observation:20", 20))
        self.assertEqual([1, 10, 20], [value["observation_index"] for value in self.evidence.observations_for_sequence("sequence")])
        self.assertEqual(sorted(self.evidence.observations), [value["id"] for value in self.evidence.summary()["observations"]])
        self.assertEqual(["activate", "refresh"], [value["id"] for value in self.evidence.summary()["action_descriptions"]])
        self.assertEqual([], self.evidence.observations_for_sequence("unknown"))
        self.assertIsNone(self.evidence.observation("unknown"))

    def test_interleaved_sequences_have_independent_actions_and_transition_order(self):
        self.accept("test_sequence_started", TestSequence("other", "test-run", 2, "other"))
        self.accept("observation_recorded", self.observation("other:1", 1, "other"))
        self.accept("action_selected", self.execution())
        self.accept("action_selected", self.execution("other:execution", "other:1", "other"))
        self.accept("action_execution_reported", ActionExecutionResult("other:execution", "returned"))
        self.accept("action_execution_reported", ActionExecutionResult("execution:1", "returned"))
        self.accept("observation_recorded", self.observation("other:2", 2, "other"))
        self.accept("observation_recorded", self.observation("observation:2", 2))
        self.accept("transition_recorded", RecordedTransition("other:execution", "other:2"))
        self.accept("transition_recorded", RecordedTransition("execution:1", "observation:2"))
        self.assertEqual(["execution:1", "other:execution"], list(self.evidence.action_executions))
        self.assertEqual(["other:execution", "execution:1"], [value["action_execution"] for value in self.evidence.recorded_transitions])
        self.reject("test_run_finished", {"test_run": "test-run", "reason": "completed"}, "invalid_lifecycle", "test_run")

    def test_sequence_indexes_increase_and_parent_must_be_active(self):
        self.reject("test_sequence_started", TestSequence("other", "test-run", 1, "other"),
                    "invalid_order", "sequence_index")
        self.reject("test_sequence_started", TestSequence("other", "missing", 2, "other"),
                    "invalid_reference", "test_run")
        self.finish()
        self.reject("test_sequence_started", TestSequence("other", "test-run", 2, "other"),
                    "invalid_lifecycle", "test_run")

    def test_optional_severity_keeps_source_label_and_value_without_changing_status(self):
        result = self.oracle_result(oracle_severity=OracleSeverity("  Priorité équipe  ", Decimal("25.125")))
        self.accept("oracle_result_recorded", result)
        self.accept("oracle_result_recorded", self.oracle_result())
        self.assertEqual({"label": "  Priorité équipe  ", "value": Decimal("25.125")},
                         self.evidence.oracle_results[0]["oracle_severity"])
        self.assertEqual("passed", self.evidence.oracle_results[0]["oracle_status"])
        self.assertNotIn("oracle_severity", self.evidence.oracle_results[1])

    def test_temporal_assessment_matches_sequence_or_run_end(self):
        assessment = {"phase": "during_sequence", "semantics": "open_ended"}
        result = self.oracle_result(OracleTarget.test_sequence(), test_sequence="sequence",
            oracle_status="pending", oracle_details={"evidence.assessment": assessment})
        self.accept("oracle_result_recorded", result)
        self.finish()
        self.reject("oracle_result_recorded", result, "invalid_lifecycle", "oracle_details.evidence.assessment.phase")
        assessment["phase"] = "end_of_sequence"
        assessment["termination_reason"] = "completed"
        self.accept("oracle_result_recorded", result)
        assessment["phase"] = "end_of_run"
        self.accept("oracle_result_recorded", result)
        assessment["termination_reason"] = "different"
        self.reject("oracle_result_recorded", result, "invalid_record",
                    "oracle_details.evidence.assessment.termination_reason")
