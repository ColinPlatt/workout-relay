"""Strength workouts: the schema branch, the validation, and the payload.

The payload assertions compare against `tests/fixtures/garmin_strength_workout.json`,
a workout Garmin itself produced. Asserting against an invented shape is the
failure this whole exercise exists to avoid — it would pass while Garmin
rejected the upload.
"""

import json
from pathlib import Path

import pytest

from workout_relay.plans import plan_warnings, validate_plan
from workout_relay.workouts import build_workout

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "garmin_strength_workout.json").read_text()
)


def garmin_steps(steps=None):
    if steps is None:
        steps = FIXTURE["workoutSegments"][0]["workoutSteps"]
    for step in steps:
        if step["type"] == "RepeatGroupDTO":
            yield step
            yield from garmin_steps(step["workoutSteps"])
        else:
            yield step


def plan(steps, **workout):
    return {
        "schema_version": 1,
        "plan_id": "strength-plan",
        "title": "Strength",
        "workouts": [{
            "id": "w1", "date": "2026-10-01", "sport": "strength",
            "title": "Lower body", "steps": steps, **workout,
        }],
    }


def built(steps, **workout):
    return build_workout(plan(steps, **workout)["workouts"][0]).to_dict()


def executable(payload):
    def walk(steps):
        for step in steps:
            if step["type"] == "RepeatGroupDTO":
                yield from walk(step["workoutSteps"])
            else:
                yield step

    return list(walk(payload["workoutSegments"][0]["workoutSteps"]))


REPS = {"type": "reps", "value": 10}
SECONDS = {"type": "time", "value": 60}


# --- the schema branch -----------------------------------------------------


def test_a_valid_strength_plan_is_accepted():
    assert validate_plan(plan([
        {"type": "warmup", "exercise": "PLANK", "duration": SECONDS},
        {"type": "repeat", "count": 3, "steps": [
            {"type": "interval", "exercise": "ROMANIAN_DEADLIFT", "duration": REPS, "weight_kg": 8},
            {"type": "rest", "duration": SECONDS},
        ]},
    ])) == []


def test_running_targets_are_not_accepted_on_a_strength_step():
    """The sports must not bleed into each other: a pace target here would be
    silently dropped by the builder rather than refused."""
    errors = validate_plan(plan([
        {"type": "interval", "exercise": "PLANK", "duration": SECONDS,
         "target": {"type": "heart_rate", "low": 120, "high": 150}},
    ]))
    assert errors and errors[0]["code"].startswith("schema_")


def test_a_running_plan_is_untouched_by_the_strength_branch():
    from workout_relay.plans import EXAMPLE_PLAN

    assert validate_plan(EXAMPLE_PLAN) == []
    payload = build_workout(EXAMPLE_PLAN["workouts"][0]).to_dict()
    assert payload["sportType"]["sportTypeKey"] == "running"


def test_reps_are_only_available_to_strength():
    errors = validate_plan({
        "schema_version": 1, "plan_id": "run-plan", "title": "Run",
        "workouts": [{"id": "w1", "date": "2026-10-01", "sport": "running", "title": "Run",
                      "steps": [{"type": "interval", "duration": REPS,
                                 "target": {"type": "no_target"}}]}],
    })
    assert errors


# --- validation ------------------------------------------------------------


def test_an_invented_exercise_is_refused_with_the_one_that_was_meant():
    errors = validate_plan(plan([
        {"type": "interval", "exercise": "DB_BENCH_PRESS", "duration": REPS},
    ]))
    assert errors[0]["code"] == "unknown_exercise"
    suggested = [item["exercise"] for item in errors[0]["params"]["suggestions"]]
    assert "DUMBBELL_BENCH_PRESS" in suggested


def test_an_exercise_in_the_wrong_category_is_refused_with_the_right_one():
    errors = validate_plan(plan([
        {"type": "interval", "exercise": "KETTLEBELL_SWING", "category": "DEADLIFT",
         "duration": REPS},
    ]))
    assert errors[0]["code"] == "wrong_category"
    assert errors[0]["params"]["categories"] == ["HIP_RAISE"]


def test_a_rest_step_may_not_name_an_exercise_or_carry_weight():
    codes = {error["code"] for error in validate_plan(plan([
        {"type": "rest", "exercise": "PLANK", "weight_kg": 10, "duration": SECONDS},
    ]))}
    assert codes == {"rest_names_an_exercise", "rest_carries_weight"}


def test_a_rest_measured_in_reps_makes_no_sense():
    errors = validate_plan(plan([{"type": "rest", "duration": REPS}]))
    assert errors[0]["code"] == "rest_must_be_timed"


def test_a_step_naming_nothing_at_all_is_refused():
    errors = validate_plan(plan([{"type": "interval", "duration": REPS}]))
    assert errors[0]["code"] == "no_exercise_named"


def test_weight_on_a_bodyweight_movement_is_questioned():
    errors = validate_plan(plan([
        {"type": "interval", "exercise": "PLANK", "duration": SECONDS, "weight_kg": 20},
    ]))
    assert errors[0]["code"] == "weight_on_bodyweight_exercise"


# --- the discouraged fallback ----------------------------------------------


def test_a_category_only_step_is_refused_without_a_written_reason():
    """Allowed, but not by accident: Garmin's generic step says less to the
    person than a named movement, so it has to be asked for."""
    errors = validate_plan(plan([
        {"type": "interval", "category": "CARRY", "duration": SECONDS},
    ]))
    assert errors[0]["code"] == "generic_exercise_needs_reason"
    # The refusal shows what could have been used instead.
    assert "FARMERS_CARRY" in errors[0]["params"]["examples"]


def test_a_category_only_step_with_a_reason_submits_but_warns():
    discouraged = plan([
        {"type": "interval", "category": "CARRY", "duration": SECONDS,
         "reason": "Suitcase carry with one kettlebell; no listed carry matches"},
    ])
    assert validate_plan(discouraged) == []
    warnings = plan_warnings(discouraged)
    assert warnings[0]["code"] == "generic_exercise_used"
    assert warnings[0]["params"]["reason"].startswith("Suitcase carry")


def test_a_plan_with_no_shortcuts_warns_about_nothing():
    assert plan_warnings(plan([
        {"type": "interval", "exercise": "FARMERS_CARRY", "duration": SECONDS},
    ])) == []


def test_an_unknown_category_is_refused():
    errors = validate_plan(plan([
        {"type": "interval", "category": "NOT_REAL", "duration": SECONDS,
         "reason": "trying to sneak something past validation"},
    ]))
    assert errors[0]["code"] == "unknown_category"


# --- the payload, against what Garmin produced -----------------------------


def test_the_sport_matches_the_one_garmin_sent_back():
    payload = built([{"type": "interval", "exercise": "PLANK", "duration": SECONDS}])
    assert payload["sportType"] == FIXTURE["sportType"]
    assert payload["subSportType"] == FIXTURE["subSportType"]
    assert payload["workoutSegments"][0]["sportType"] == FIXTURE["sportType"]


def test_the_end_conditions_match_the_ones_garmin_sent_back():
    garmin = {
        step["endCondition"]["conditionTypeKey"]: step["endCondition"]
        for step in garmin_steps() if step.get("endCondition")
    }
    payload = built([
        {"type": "interval", "exercise": "ROMANIAN_DEADLIFT", "duration": REPS},
        {"type": "rest", "duration": SECONDS},
        {"type": "repeat", "count": 2, "steps": [
            {"type": "interval", "exercise": "PLANK", "duration": SECONDS}]},
    ])
    ours = payload["workoutSegments"][0]["workoutSteps"]
    assert ours[0]["endCondition"] == garmin["reps"]
    assert ours[1]["endCondition"] == garmin["time"]
    assert ours[2]["endCondition"] == garmin["iterations"]


def test_the_step_types_match_the_ones_garmin_sent_back():
    garmin = {
        step["stepType"]["stepTypeKey"]: step["stepType"] for step in garmin_steps()
    }
    payload = built([
        {"type": "warmup", "exercise": "PLANK", "duration": SECONDS},
        {"type": "interval", "exercise": "PLANK", "duration": SECONDS},
        {"type": "rest", "duration": SECONDS},
    ])
    ours = payload["workoutSegments"][0]["workoutSteps"]
    for step, key in zip(ours, ["warmup", "interval", "rest"]):
        assert step["stepType"] == garmin[key]


def test_weight_is_sent_in_kilograms_with_garmins_unit():
    """factor 1000 is unit metadata; 8 kg is 8.0, not 8000."""
    sample = next(
        step for step in garmin_steps()
        if step.get("weightValue") and step["weightValue"] > 0
    )
    payload = built([
        {"type": "interval", "exercise": "ROMANIAN_DEADLIFT", "duration": REPS, "weight_kg": 8},
    ])
    step = payload["workoutSegments"][0]["workoutSteps"][0]
    assert step["weightValue"] == 8.0
    assert step["weightUnit"] == sample["weightUnit"]


def test_a_bodyweight_step_sends_no_weight_value():
    payload = built([{"type": "interval", "exercise": "PLANK", "duration": SECONDS}])
    assert "weightValue" not in payload["workoutSegments"][0]["workoutSteps"][0]


def test_the_exercise_is_carried_on_the_step_the_way_garmin_carries_it():
    sample = next(
        step for step in garmin_steps()
        if step.get("exerciseName") == "ROMANIAN_DEADLIFT"
    )
    payload = built([
        {"type": "interval", "exercise": "ROMANIAN_DEADLIFT", "duration": REPS},
    ])
    step = payload["workoutSegments"][0]["workoutSteps"][0]
    assert (step["category"], step["exerciseName"]) == (sample["category"], sample["exerciseName"])


def test_a_category_only_step_sends_the_empty_name_garmin_uses():
    sample = next(
        step for step in garmin_steps()
        if step.get("category") == "CARRY" and step.get("exerciseName") == ""
    )
    payload = built([
        {"type": "interval", "category": "CARRY", "duration": SECONDS,
         "reason": "Suitcase carry with one kettlebell; no listed carry matches"},
    ])
    step = payload["workoutSegments"][0]["workoutSteps"][0]
    assert step["category"] == sample["category"]
    # An empty string, not a missing field: that is Garmin's generic step.
    assert step["exerciseName"] == ""


def test_a_rest_step_names_no_exercise_in_the_payload():
    payload = built([{"type": "rest", "duration": SECONDS}])
    step = payload["workoutSegments"][0]["workoutSteps"][0]
    assert "category" not in step and "exerciseName" not in step


def test_sets_become_a_repeat_group_like_garmins():
    sample = next(step for step in garmin_steps() if step["type"] == "RepeatGroupDTO")
    payload = built([
        {"type": "repeat", "count": 3, "steps": [
            {"type": "interval", "exercise": "PLANK", "duration": SECONDS},
            {"type": "rest", "duration": SECONDS},
        ]},
    ])
    group = payload["workoutSegments"][0]["workoutSteps"][0]
    assert group["type"] == "RepeatGroupDTO"
    assert group["stepType"] == sample["stepType"]
    assert group["numberOfIterations"] == 3
    assert len(group["workoutSteps"]) == 2


def test_coaching_notes_travel_as_the_step_description():
    payload = built([
        {"type": "interval", "exercise": "PLANK", "duration": SECONDS,
         "description": "Brace hard, squeeze glutes, no hip sag."},
    ])
    step = payload["workoutSegments"][0]["workoutSteps"][0]
    assert step["description"] == "Brace hard, squeeze glutes, no hip sag."


def test_every_step_we_build_carries_the_fields_garmin_puts_on_every_step():
    """A missing targetType or weightUnit is the kind of omission Garmin
    accepts silently and the watch then renders wrongly."""
    payload = built([
        {"type": "interval", "exercise": "PLANK", "duration": SECONDS},
        {"type": "rest", "duration": SECONDS},
        {"type": "repeat", "count": 2, "steps": [
            {"type": "interval", "exercise": "AIR_SQUAT", "duration": REPS}]},
    ])
    for step in executable(payload):
        assert step["targetType"]["workoutTargetTypeKey"] == "no.target"
        assert step["weightUnit"]["unitKey"] == "kilogram"


@pytest.mark.parametrize("written", ["romanian deadlift", "Romanian-Deadlift"])
def test_a_plan_may_write_the_name_naturally(written):
    assert validate_plan(plan([
        {"type": "interval", "exercise": written, "duration": REPS},
    ])) == []
    payload = built([{"type": "interval", "exercise": written, "duration": REPS}])
    step = payload["workoutSegments"][0]["workoutSteps"][0]
    assert step["exerciseName"] == "ROMANIAN_DEADLIFT"
