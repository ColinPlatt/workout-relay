import copy

from workout_relay.plans import EXAMPLE_PLAN, assistant_instructions, validate_plan
from workout_relay.workouts import build_workout


def test_example_is_valid_and_buildable():
    assert validate_plan(EXAMPLE_PLAN) == []
    objects = [build_workout(workout) for workout in EXAMPLE_PLAN["workouts"]]
    assert [item.workoutName for item in objects] == ["6 x 400 m", "Easy aerobic run"]


def test_semantic_errors_are_structured_and_repairable():
    plan = copy.deepcopy(EXAMPLE_PLAN)
    workout = plan["workouts"][0]
    workout["id"] = "2026-10-02_wrong_date"
    workout["steps"][1]["count"] = 30
    workout["steps"][1]["steps"][0]["target"] = {
        "type": "pace",
        "slow": "4:00",
        "fast": "4:30",
    }
    errors = validate_plan(plan)
    assert {item["code"] for item in errors} == {
        "expanded_steps",
        "pace_order",
    }
    assert all("path" in item and "params" in item for item in errors)


def test_schema_rejects_unknown_fields_and_markdown_shaped_input():
    plan = copy.deepcopy(EXAMPLE_PLAN)
    plan["surprise"] = True
    assert any(item["code"] == "schema_additionalProperties" for item in validate_plan(plan))
    assert validate_plan("```json\n{}\n```")[0]["code"] == "schema_type"


def test_assistant_guidance_is_bilingual():
    english = assistant_instructions("en")
    french = assistant_instructions("fr")
    assert english["language"] == "en" and french["language"] == "fr"
    assert "Return only" in english["purpose"]
    assert "Produisez uniquement" in french["purpose"]
    assert english["validate_url"] == french["validate_url"]


def test_workout_identity_is_independent_of_its_date():
    plan = copy.deepcopy(EXAMPLE_PLAN)
    plan["workouts"][0]["date"] = "2026-10-09"
    assert validate_plan(plan) == []  # legacy date-prefixed IDs remain valid
    plan["workouts"][0]["id"] = "training-block-1_intervals"
    assert validate_plan(plan) == []
    plan["workouts"][1]["id"] = plan["workouts"][0]["id"]
    assert "duplicate_workout_id" in {error["code"] for error in validate_plan(plan)}
