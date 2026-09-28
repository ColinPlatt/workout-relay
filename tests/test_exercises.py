"""The exercise catalogue, checked against what Garmin itself produced.

The fixture is a real strength workout read back from Garmin Connect. Every
`(category, exerciseName)` pair in it must resolve, because those are pairs
Garmin accepted — if the catalogue disagrees with them, the catalogue is wrong.
"""

import json
from pathlib import Path

import pytest

from workout_relay import exercises as ex

FIXTURE = Path(__file__).parent / "fixtures" / "garmin_strength_workout.json"


def garmin_steps():
    workout = json.loads(FIXTURE.read_text())

    def walk(steps):
        for step in steps:
            if step["type"] == "RepeatGroupDTO":
                yield from walk(step["workoutSteps"])
            else:
                yield step

    return list(walk(workout["workoutSegments"][0]["workoutSteps"]))


def test_every_pair_garmin_produced_resolves():
    named = [
        (step["category"], step["exerciseName"])
        for step in garmin_steps()
        if step.get("category") and step.get("exerciseName")
    ]
    assert named, "fixture should contain named exercises"
    for category, name in named:
        assert ex.resolve(name, category) == (category, name)


def test_every_category_only_step_garmin_produced_resolves():
    generic = [
        step["category"]
        for step in garmin_steps()
        if step.get("category") and step.get("exerciseName") == ex.GENERIC
    ]
    assert generic, "fixture should contain category-only steps"
    for category in generic:
        assert ex.resolve_generic(category) == (category, "")


def test_rest_steps_name_no_exercise_at_all():
    rests = [step for step in garmin_steps() if step["stepType"]["stepTypeKey"] == "rest"]
    assert rests
    assert all(step["category"] is None and step["exerciseName"] is None for step in rests)


# --- resolving -------------------------------------------------------------


def test_the_category_is_derived_because_it_cannot_be_guessed():
    """The bug this catalogue exists to fix: names imply nothing."""
    assert ex.resolve("KETTLEBELL_SWING") == ("HIP_RAISE", "KETTLEBELL_SWING")
    assert ex.resolve("DEAD_BUG") == ("HIP_STABILITY", "DEAD_BUG")
    assert ex.resolve("ROMANIAN_DEADLIFT") == ("DEADLIFT", "ROMANIAN_DEADLIFT")


@pytest.mark.parametrize(
    "written",
    ["side_plank", "Side Plank", "side-plank", "SIDE PLANK"],
)
def test_names_are_accepted_however_they_are_written(written):
    assert ex.normalise(written) == "SIDE_PLANK"


def test_a_name_starting_with_a_digit_gets_garmins_leading_underscore():
    assert ex.resolve("90 degree static hold") == ("PLANK", "_90_DEGREE_STATIC_HOLD")


def test_a_name_only_equipment_categories_hold_must_be_disambiguated():
    """CHEST_PRESS is a sled press or a suspension press and nothing else, so
    there is no plain version to fall back to and the caller must choose."""
    with pytest.raises(ex.Ambiguous) as raised:
        ex.resolve("CHEST_PRESS")
    # Naming the candidates is the whole value of the refusal.
    assert raised.value.candidates == ["SLED", "SUSPENSION"]
    assert ex.resolve("CHEST_PRESS", "SLED") == ("SLED", "CHEST_PRESS")


@pytest.mark.parametrize(
    "name,expected",
    [("PLANK", "PLANK"), ("ROW", "ROW"), ("SQUAT", "SQUAT"), ("CURL", "CURL")],
)
def test_a_name_that_is_also_a_category_means_that_category(name, expected):
    """Exact equality, not inference: PLANK's home is the PLANK category, and
    BANDED_EXERCISES/SUSPENSION hold equipment variants of it."""
    assert ex.resolve(name) == (expected, name)


@pytest.mark.parametrize(
    "name,expected",
    [("SIDE_PLANK", "PLANK"), ("RUSSIAN_TWIST", "CORE"), ("MOUNTAIN_CLIMBER", "PLANK"),
     ("ZERCHER_SQUAT", "SQUAT"), ("SPRINT", "RUN")],
)
def test_a_bare_name_means_the_plain_version_not_the_equipment_one(name, expected):
    assert ex.resolve(name) == (expected, name)


def test_the_equipment_variant_is_still_reachable_by_naming_it():
    assert ex.resolve("SIDE_PLANK", "SUSPENSION") == ("SUSPENSION", "SIDE_PLANK")
    assert ex.resolve("RUSSIAN_TWIST", "SANDBAG") == ("SANDBAG", "RUSSIAN_TWIST")


def test_almost_every_name_resolves_without_a_category():
    """The design claim: naming a category should be rare, not routine."""
    needing = [
        name for name, found in ex._index().items()
        if len(found) > 1 and ex._canonical(name, found) is None
    ]
    assert sorted(needing) == ["CHEST_PRESS", "GLUTE_BRIDGE"]


def test_a_name_paired_with_the_wrong_category_is_refused_with_the_right_one():
    with pytest.raises(ex.WrongCategory) as raised:
        ex.resolve("KETTLEBELL_SWING", "DEADLIFT")
    assert raised.value.candidates == ["HIP_RAISE"]


def test_a_category_that_is_also_an_exercise_resolves_to_the_exercise():
    """30 categories contain an exercise of the same name; both are real."""
    assert ex.resolve("PLANK", "PLANK") == ("PLANK", "PLANK")
    assert ex.resolve_generic("PLANK") == ("PLANK", ex.GENERIC)


# --- substitutions ---------------------------------------------------------


@pytest.mark.parametrize(
    "written,expected",
    [
        ("DB_BENCH_PRESS", "DUMBBELL_BENCH_PRESS"),
        ("BULGARIAN_SPLIT_SQUATS", "BARBELL_BULGARIAN_SPLIT_SQUAT"),
        ("PUSHUPS", "PUSH_UP"),
    ],
)
def test_an_unknown_name_offers_the_movement_that_was_meant(written, expected):
    with pytest.raises(ex.Unknown) as raised:
        ex.resolve(written)
    assert expected in [item["exercise"] for item in raised.value.suggestions]
    # A suggestion without its category would leave the caller guessing again.
    assert all(item["category"] for item in raised.value.suggestions)


def test_nonsense_offers_nothing_rather_than_a_confident_wrong_answer():
    with pytest.raises(ex.Unknown) as raised:
        ex.resolve("QQQQ_ZZZZ_XXXX")
    assert raised.value.suggestions == []


def test_an_unknown_category_for_a_generic_step_is_refused():
    with pytest.raises(ex.Unknown):
        ex.resolve_generic("NOT_A_CATEGORY")


# --- searching -------------------------------------------------------------


def test_searching_narrows_by_category():
    found = ex.search("", category="DEADLIFT", limit=200)
    assert found and all(item["category"] == "DEADLIFT" for item in found)
    assert "ROMANIAN_DEADLIFT" in [item["exercise"] for item in found]


def test_searching_reports_whether_a_movement_carries_weight():
    found = {item["exercise"]: item for item in ex.search("", category="PLANK", limit=200)}
    assert found["PLANK"]["bodyweight"] is True
    assert ex.is_bodyweight("PLANK", "PLANK") is True


def test_the_catalogue_is_the_size_garmin_published():
    assert len(ex.categories()) == 47
    assert sum(len(ex.exercises_in(name)) for name in ex.categories()) == 1531
