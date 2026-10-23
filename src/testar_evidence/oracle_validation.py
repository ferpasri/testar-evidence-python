"""Validate oracle targets and reporting phases against the recorded histories."""

from .errors import EvidenceError


def _require(condition, code, field, message):
    if not condition:
        raise EvidenceError(code, message, field)


def _target_reference(condition, field):
    _require(condition, "invalid_oracle_target", field, "Oracle target has an invalid reference: " + field)


def validate_oracle_result(result, test_runs, test_sequences, observations, action_executions):
    _require(result["test_run"] in test_runs, "invalid_reference", "test_run", "Unknown oracle test run")
    target = result["oracle_target"]
    target_type = target["target_type"]
    sequence_id = result.get("test_sequence")
    if target_type == "test_run":
        _target_reference(sequence_id is None, "test_sequence")
    else:
        sequence = test_sequences.get(sequence_id)
        _target_reference(sequence is not None and sequence["test_sequence"]["test_run"] == result["test_run"],
                          "test_sequence")
        _validate_target(target, sequence_id, observations, action_executions)
    _validate_assessment(result, test_runs, test_sequences)


def _observation(identifier, sequence_id, field, observations):
    observation = observations.get(identifier)
    _target_reference(observation is not None and observation["test_sequence"] == sequence_id, field)
    return observation


def _validate_target(target, sequence_id, observations, action_executions):
    target_type = target["target_type"]
    if target_type in {"observation", "state_property", "widget", "widget_property"}:
        observation = _observation(target["observation"], sequence_id, "oracle_target.observation", observations)
        if target_type in {"widget", "widget_property"}:
            _target_reference(any(widget["id"] == target["widget"] for widget in observation["recorded_state"]["widgets"]),
                              "oracle_target.widget")
    elif target_type == "action_execution":
        execution = action_executions.get(target["action_execution"])
        _target_reference(execution is not None and execution["action_execution"]["test_sequence"] == sequence_id,
                          "oracle_target.action_execution")
    elif target_type == "observation_range":
        first = _observation(target["first_observation"], sequence_id, "oracle_target.first_observation", observations)
        last = _observation(target["last_observation"], sequence_id, "oracle_target.last_observation", observations)
        _target_reference(last["observation_index"] >= first["observation_index"], "oracle_target.last_observation")


def _validate_assessment(result, test_runs, test_sequences):
    details = result["oracle_details"]
    if "evidence.assessment" not in details:
        return
    assessment = details["evidence.assessment"]
    prefix = "oracle_details.evidence.assessment"
    _require(isinstance(assessment, dict), "invalid_record", prefix, "Assessment must be an object")
    phase = assessment.get("phase")
    semantics = assessment.get("semantics")
    _require(phase in ("during_run", "end_of_run", "during_sequence", "end_of_sequence"),
             "invalid_record", prefix + ".phase", "Unknown reporting phase")
    _require(semantics in ("open_ended", "recorded_interval"),
             "invalid_record", prefix + ".semantics", "Unknown temporal semantics")
    if phase.endswith("sequence"):
        history = test_sequences.get(result.get("test_sequence"))
        _target_reference(history is not None, "test_sequence")
    else:
        history = test_runs[result["test_run"]]
    finished = history["status"] == "finished"
    _require(finished == phase.startswith("end_"), "invalid_lifecycle", prefix + ".phase",
             "Reporting phase does not match the history status")
    if "termination_reason" in assessment:
        _require(finished and isinstance(assessment["termination_reason"], str)
                 and assessment["termination_reason"] == history["reason"],
                 "invalid_record", prefix + ".termination_reason", "Reason must match the history ending")
