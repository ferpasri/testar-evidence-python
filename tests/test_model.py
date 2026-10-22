"""Lifecycle tests use small generic records rather than captured tool fixtures."""

from copy import deepcopy
from decimal import Decimal
import unittest

from testar_evidence.errors import EvidenceError
from testar_evidence.model import EvidenceModel


class EvidenceModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = EvidenceModel({"schema_version": 2, "model": "example:model"})
        self.next_event = 0
        self.accept("run_started", {
            "id": "run", "producer": "generic-example", "source_run": "source:run", "properties": {}
        })
        self.accept("action_defined", {"id": "activate", "kind": "activate", "properties": {}})
        self.accept("action_defined", {"id": "refresh", "kind": "refresh", "properties": {}})
        self.accept("observed", self.observation())

    def envelope(self, kind: str, data: dict) -> dict:
        self.next_event += 1
        return {"schema_version": 2, "id": "event:" + str(self.next_event),
                "event": {"kind": kind, "data": data}}

    def accept(self, kind: str, data: dict) -> dict:
        envelope = self.envelope(kind, data)
        self.assertTrue(self.model.apply(envelope))
        return envelope

    @staticmethod
    def observation(identifier: str = "observation:1", sequence: int = 1,
                    run: str = "run", snapshot: str | None = None) -> dict:
        return {
            "id": identifier, "run": run, "sequence": sequence, "timestamp_micros": sequence * 1000,
            "state": {
                "id": snapshot or "snapshot:" + identifier,
                "properties": {"ui.title": "Example", "ui.available": True},
                "widgets": [{"id": "button", "parent": None, "properties": {"ui.role": "button"}}],
                "capture": "filtered",
            },
            "candidate_scope": "generated",
            "candidates": [{"action": "activate", "target": "button"}, {"action": "refresh", "target": None}],
            "artifacts": [],
        }

    @staticmethod
    def execution(identifier: str = "execution:1", source: str = "observation:1") -> dict:
        return {"id": identifier, "run": "run", "source": source, "action": "activate",
                "target": "button", "parameters": {"button": "primary"}}

    @staticmethod
    def check(target: dict, run: str = "run", status: str = "passed") -> dict:
        return {"run": run, "name": "Example test oracle", "rule": {"id": "rule", "version": "1"},
                "target": target, "status": status, "details": {}}

    def reject(self, kind: str, data: dict, code: str, field: str | None = None) -> None:
        previous = self.model.summary()
        history = self.model.events
        with self.assertRaises(EvidenceError) as failure:
            self.model.apply(self.envelope(kind, data))
        self.assertEqual(code, failure.exception.code)
        self.assertEqual(field, failure.exception.field)
        self.assertEqual(previous, self.model.summary())
        self.assertEqual(history, self.model.events)

    def test_validate_is_side_effect_free_and_identical_retries_are_idempotent(self) -> None:
        envelope = self.envelope("checked", self.check({"kind": "observation", "observation": "observation:1"}))
        previous = self.model.summary()
        self.assertTrue(self.model.validate(envelope))
        self.assertTrue(self.model.validate(envelope))
        self.assertEqual(previous, self.model.summary())
        self.assertTrue(self.model.apply(envelope))
        self.assertFalse(self.model.validate(envelope))
        self.assertFalse(self.model.apply(envelope))
        self.assertEqual(previous["counts"]["events"] + 1, self.model.event_count)
        conflicting = deepcopy(envelope)
        conflicting["event"]["data"]["status"] = "failed"
        with self.assertRaises(EvidenceError) as failure:
            self.model.apply(conflicting)
        self.assertEqual("conflict", failure.exception.code)
        self.assertEqual("passed", self.model.checks[0]["status"])

    def test_original_inputs_and_query_results_cannot_mutate_history(self) -> None:
        envelope = self.accept("checked", self.check({"kind": "run"}))
        envelope["event"]["data"]["status"] = "failed"
        self.model.events[-1]["event"]["data"]["status"] = "error"
        self.model.checks[0]["status"] = "unknown"
        self.model.observations["observation:1"]["state"]["properties"]["ui.title"] = "Changed"
        self.model.snapshots["snapshot:observation:1"]["properties"]["ui.title"] = "Changed"
        self.model.actions["activate"]["properties"]["changed"] = True
        self.model.runs["run"]["status"] = "finished"
        self.model.manifest["model"] = "changed"
        self.model.summary()["checks"][0]["status"] = "error"
        self.assertEqual("passed", self.model.checks[0]["status"])
        self.assertEqual("Example", self.model.observation("observation:1")["state"]["properties"]["ui.title"])
        self.assertEqual({}, self.model.action("activate")["properties"])
        self.assertEqual("running", self.model.run("run")["status"])
        self.assertEqual("example:model", self.model.manifest["model"])

    def test_snapshot_retains_independent_history_and_lifecycle(self) -> None:
        self.accept("action_selected", self.execution())
        snapshot = self.model.snapshot()
        self.accept("action_submitted", {"execution": "execution:1", "accepted": True, "error": None})
        snapshot.apply(self.envelope("run_finished", {"run": "run", "reason": "snapshot completed"}))
        self.assertEqual("submitted", self.model.execution("execution:1")["status"])
        self.assertEqual("incomplete", snapshot.execution("execution:1")["status"])
        self.assertEqual("running", self.model.run("run")["status"])
        self.assertEqual("finished", snapshot.run("run")["status"])

    def test_observed_execution_creates_only_the_explicit_link(self) -> None:
        self.accept("action_selected", self.execution())
        self.accept("action_submitted", {"execution": "execution:1", "accepted": True, "error": None})
        self.accept("observed", self.observation("observation:2", 2))
        self.assertEqual([], self.model.links())
        self.accept("action_result", {"execution": "execution:1", "observation": "observation:2"})
        expected = [{"execution": "execution:1", "run": "run", "source": "observation:1",
                     "action": "activate", "target": "observation:2"}]
        self.assertEqual(expected, self.model.links())
        self.assertEqual(expected, self.model.outgoing("observation:1"))
        self.assertEqual(expected, self.model.incoming("observation:2"))
        self.assertEqual([], self.model.incoming("observation:1"))
        self.assertEqual("observed", self.model.executions_for_run("run")[0]["status"])
        self.assertEqual(2, len(self.model.candidates("observation:1")))
        self.assertEqual("refresh", self.model.candidates("observation:1")[1]["action"])

    def test_rejected_submission_keeps_its_error_and_allows_next_selection(self) -> None:
        self.accept("action_selected", self.execution())
        self.accept("action_submitted", {"execution": "execution:1", "accepted": False,
                                          "error": "Target is unavailable"})
        self.accept("observed", self.observation("observation:2", 2))
        self.reject("action_result", {"execution": "execution:1", "observation": "observation:2"}, "conflict")
        self.accept("action_selected", self.execution("execution:2", "observation:2"))
        self.assertEqual("Target is unavailable", self.model.execution("execution:1")["error"])
        self.assertEqual("rejected", self.model.execution("execution:1")["status"])
        self.assertIsNone(self.model.execution("execution:1")["result"])
        self.assertEqual([], self.model.links())

    def test_submission_without_optional_error_records_atomically_and_preserves_its_payload(self) -> None:
        self.accept("action_selected", self.execution())
        envelope = self.envelope("action_submitted", {"execution": "execution:1", "accepted": True})
        previous_count = self.model.event_count
        self.assertTrue(self.model.validate(envelope))
        self.assertTrue(self.model.apply(envelope))
        self.assertEqual(previous_count + 1, self.model.event_count)
        self.assertEqual("submitted", self.model.execution("execution:1")["status"])
        self.assertIsNone(self.model.execution("execution:1")["error"])
        self.assertEqual(envelope, self.model.events[-1])
        self.assertNotIn("error", self.model.events[-1]["event"]["data"])

    def test_omitted_and_null_action_targets_share_the_same_binding(self) -> None:
        for candidate_has_target in (False, True):
            for execution_has_target in (False, True):
                with self.subTest(candidate_has_target=candidate_has_target, execution_has_target=execution_has_target):
                    self.setUp()
                    observation = self.observation("observation:2", 2)
                    observation["candidates"] = [{"action": "refresh"}]
                    del observation["candidate_scope"]
                    if candidate_has_target:
                        observation["candidates"][0]["target"] = None
                    observed_event = self.accept("observed", observation)
                    execution = self.execution(source="observation:2")
                    execution["action"] = "refresh"
                    if execution_has_target:
                        execution["target"] = None
                    else:
                        del execution["target"]
                    selected_event = self.accept("action_selected", execution)
                    self.assertEqual(execution, self.model.execution("execution:1")["execution"])
                    self.assertEqual([observed_event, selected_event], self.model.events[-2:])
                    self.assertEqual("unknown", self.model.summary()["observations"][1]["candidate_scope"])
                    self.assertNotIn("candidate_scope", self.model.observation("observation:2"))

    def test_finishing_a_run_marks_pending_execution_incomplete_without_a_link(self) -> None:
        for accepted in (False, True):
            with self.subTest(submitted=accepted):
                self.setUp()
                self.accept("action_selected", self.execution())
                if accepted:
                    self.accept("action_submitted", {"execution": "execution:1", "accepted": True, "error": None})
                self.accept("run_finished", {"run": "run", "reason": "execution deadline"})
                self.assertEqual("incomplete", self.model.execution("execution:1")["status"])
                self.assertIsNone(self.model.execution("execution:1")["result"])
                self.assertEqual([], self.model.links())
                self.assertEqual("execution deadline", self.model.run("run")["reason"])
                self.reject("action_result", {"execution": "execution:1", "observation": "observation:1"}, "conflict")

    def test_selection_requires_latest_observation_an_exact_candidate_and_no_pending_action(self) -> None:
        wrong_target = self.execution()
        wrong_target["target"] = None
        self.reject("action_selected", wrong_target, "unknown_reference")
        self.accept("observed", self.observation("observation:2", 2))
        self.reject("action_selected", self.execution(), "invalid_record")
        self.accept("action_selected", self.execution(source="observation:2"))
        self.reject("action_selected", self.execution("execution:2", "observation:2"), "conflict")

    def test_result_requires_submission_and_a_later_observation_in_the_same_run(self) -> None:
        self.accept("action_selected", self.execution())
        self.reject("action_result", {"execution": "execution:1", "observation": "observation:1"}, "conflict")
        self.accept("action_submitted", {"execution": "execution:1", "accepted": True, "error": None})
        self.reject("action_result", {"execution": "execution:1", "observation": "observation:1"}, "invalid_record")
        self.accept("run_started", {"id": "other", "producer": "generic-example", "source_run": "source:other", "properties": {}})
        self.accept("observed", self.observation("other:observation", 2, "other"))
        self.reject("action_result", {"execution": "execution:1", "observation": "other:observation"}, "invalid_record")
        self.reject("action_result", {"execution": "execution:1", "observation": "unknown"}, "unknown_reference")

    def test_observations_require_defined_candidates_and_increasing_sequence(self) -> None:
        self.reject("observed", self.observation("observation:2", 1), "invalid_record")
        unknown_action = self.observation("observation:2", 2)
        unknown_action["candidates"][0]["action"] = "unknown"
        self.reject("observed", unknown_action, "unknown_reference")
        self.reject("observed", self.observation("observation:2", 2, "unknown"), "unknown_reference")

    def test_repeated_snapshot_visits_remain_distinct_but_snapshot_content_cannot_change(self) -> None:
        self.accept("observed", self.observation("observation:2", 2, snapshot="snapshot:observation:1"))
        self.assertEqual(2, len(self.model.observations_for_snapshot("snapshot:observation:1")))
        self.assertEqual(1, len(self.model.snapshots))
        changed = self.observation("observation:3", 3, snapshot="snapshot:observation:1")
        changed["state"]["properties"]["ui.title"] = "Different"
        self.reject("observed", changed, "conflict")

    def test_boolean_and_numeric_values_cannot_hide_conflicting_content(self) -> None:
        envelope = self.accept("checked", self.check({"kind": "run"}))
        envelope["event"]["data"]["details"]["value"] = True
        self.model = EvidenceModel({"schema_version": 2, "model": "example:model"})
        self.accept("run_started", {"id": "run", "producer": "generic-example", "source_run": "source:run", "properties": {}})
        self.assertTrue(self.model.apply(envelope))
        changed = deepcopy(envelope)
        changed["event"]["data"]["details"]["value"] = 1
        with self.assertRaises(EvidenceError) as failure:
            self.model.apply(changed)
        self.assertEqual("conflict", failure.exception.code)

    def test_equal_json_numbers_allow_retries_without_rounding_precise_values(self) -> None:
        check = self.check({"kind": "run"})
        check["details"] = {"number": Decimal("1.00"), "precise": Decimal("0.123456789012345678901234567891")}
        envelope = self.accept("checked", check)
        equivalent = deepcopy(envelope)
        equivalent["event"]["data"]["details"] = {
            "precise": Decimal("0.1234567890123456789012345678910"), "number": 1
        }
        self.assertFalse(self.model.apply(equivalent))
        equivalent["event"]["data"]["details"]["precise"] = Decimal("0.123456789012345678901234567892")
        with self.assertRaises(EvidenceError) as failure:
            self.model.apply(equivalent)
        self.assertEqual("conflict", failure.exception.code)
        self.assertEqual(check["details"], self.model.checks[0]["details"])

    def test_huge_integer_retries_preserve_values_without_decimal_string_limits(self) -> None:
        huge = 10 ** 4999
        check = self.check({"kind": "run"})
        check["details"] = {"huge": huge, "boolean": True}
        envelope = self.accept("checked", check)
        self.assertFalse(self.model.apply(deepcopy(envelope)))
        equivalent = deepcopy(envelope)
        equivalent["event"]["data"]["details"]["huge"] = Decimal(huge)
        self.assertFalse(self.model.apply(equivalent))
        equivalent["event"]["data"]["details"]["boolean"] = 1
        with self.assertRaises(EvidenceError) as failure:
            self.model.apply(equivalent)
        self.assertEqual("conflict", failure.exception.code)
        self.assertIs(True, self.model.checks[0]["details"]["boolean"])
        self.assertEqual(huge, self.model.checks[0]["details"]["huge"])

    def test_equal_number_representations_do_not_change_snapshot_or_action_identity(self) -> None:
        action = {"id": "numeric-action", "kind": "activate", "properties": {"value": Decimal("1.00")}}
        self.accept("action_defined", action)
        action["properties"]["value"] = 1
        self.accept("action_defined", action)
        observation = self.observation("observation:2", 2, snapshot="numeric-state")
        observation["state"]["properties"]["number"] = Decimal("1.00")
        self.accept("observed", observation)
        observation["id"] = "observation:3"
        observation["sequence"] = 3
        observation["state"]["properties"]["number"] = 1
        self.accept("observed", observation)
        self.assertEqual(2, len(self.model.observations_for_snapshot("numeric-state")))

    def test_late_evaluations_remain_separate_and_keep_reported_status(self) -> None:
        self.accept("checked", self.check({"kind": "run"}, status="pending"))
        self.accept("run_finished", {"run": "run", "reason": "completed"})
        self.accept("checked", self.check({"kind": "run"}, status="pending"))
        self.accept("checked", self.check({"kind": "run"}, status="passed"))
        self.assertEqual(["pending", "pending", "passed"], [value["status"] for value in self.model.checks])
        self.reject("observed", self.observation("observation:2", 2), "conflict")
        self.reject("run_finished", {"run": "run", "reason": "again"}, "conflict")

    def test_property_evaluation_can_report_an_uncaptured_property(self) -> None:
        check = self.check({"kind": "widget_property", "observation": "observation:1",
                            "widget": "button", "property": "ui.alt"}, status="failed")
        check["actual"] = {"kind": "absent"}
        self.accept("checked", check)
        self.assertEqual(check, self.model.checks[0])

    def test_evaluation_references_report_precise_fields_and_preserve_accepted_prefix(self) -> None:
        self.reject("checked", self.check({"kind": "widget", "observation": "observation:1", "widget": "unknown"}),
                    "invalid_check_target", "target.widget")
        self.reject("checked", self.check({"kind": "observation", "observation": "unknown"}),
                    "invalid_check_target", "target.observation")
        self.reject("checked", self.check({"kind": "execution", "execution": "unknown"}),
                    "invalid_check_target", "target.execution")
        self.accept("observed", self.observation("observation:2", 2))
        self.reject("checked", self.check({"kind": "sequence", "from_observation": "observation:2",
                                           "to_observation": "observation:1"}),
                    "invalid_check_target", "target.to_observation")
        self.reject("checked", self.check({"kind": "sequence", "from_observation": "unknown",
                                           "to_observation": "observation:2"}),
                    "invalid_check_target", "target.from_observation")

    def test_evaluation_cannot_target_another_runs_observation_or_execution(self) -> None:
        self.accept("run_started", {"id": "other", "producer": "generic-example", "source_run": "source:other", "properties": {}})
        self.accept("observed", self.observation("other:observation", 1, "other"))
        self.reject("checked", self.check({"kind": "state_property", "observation": "other:observation", "property": "ui.title"}),
                    "invalid_check_target", "target.observation")
        self.accept("action_selected", self.execution())
        self.reject("checked", self.check({"kind": "execution", "execution": "execution:1"}, run="other"),
                    "invalid_check_target", "target.execution")

    def test_query_order_follows_sequence_for_runs_and_ids_for_summary(self) -> None:
        self.accept("observed", self.observation("observation:10", 10))
        self.accept("observed", self.observation("observation:20", 20))
        self.assertEqual([1, 10, 20], [value["sequence"] for value in self.model.observations_for_run("run")])
        self.assertEqual(sorted(self.model.observations), [value["id"] for value in self.model.summary()["observations"]])
        self.assertEqual(["activate", "refresh"], [value["id"] for value in self.model.summary()["actions"]])
        self.assertEqual([], self.model.observations_for_run("unknown"))
        self.assertIsNone(self.model.observation("unknown"))


if __name__ == "__main__":
    unittest.main()
