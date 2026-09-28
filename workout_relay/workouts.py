"""Convert the public plan format into Garmin workout payloads."""

from __future__ import annotations

import hashlib
import json

from garminconnect.workout import (
    BaseWorkout,
    ConditionType,
    ExecutableStep,
    RunningWorkout,
    StepType,
    TargetType,
    WorkoutSegment,
    create_repeat_group,
)

from . import exercises

_SPORT = {"sportTypeId": 1, "sportTypeKey": "running"}
_STEP_TYPES = {
    "warmup": (StepType.WARMUP, "warmup", 1),
    "cooldown": (StepType.COOLDOWN, "cooldown", 2),
    "interval": (StepType.INTERVAL, "interval", 3),
    "recovery": (StepType.RECOVERY, "recovery", 4),
}


class Counter:
    def __init__(self):
        self.value = 0

    def next(self) -> int:
        self.value += 1
        return self.value


def build_workout(workout: dict) -> RunningWorkout:
    if workout.get("sport") == "strength":
        return build_strength_workout(workout)
    counter = Counter()
    return RunningWorkout(
        workoutName=workout["title"],
        description=workout.get("description", ""),
        estimatedDurationInSecs=workout.get("estimated_duration_sec", 0),
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType=_SPORT,
                workoutSteps=[_build_step(step, counter) for step in workout["steps"]],
            )
        ],
    )


#: Read back from a real strength workout; see docs/garmin-strength-format.md.
#: The library's SportType enum has no strength entry, so the id is given here.
_STRENGTH_SPORT = {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 4}
_STRENGTH_STEP_TYPES = {
    "warmup": (1, "warmup", 1),
    "cooldown": (2, "cooldown", 2),
    "interval": (3, "interval", 3),
    "rest": (5, "rest", 5),
}
_REPS_CONDITION = {
    "conditionTypeId": 10, "conditionTypeKey": "reps", "displayOrder": 10, "displayable": True,
}
_TIME_CONDITION = {
    "conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True,
}
_NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
#: weightValue is kilograms directly; factor is unit metadata, not a multiplier.
_KILOGRAM = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}


def build_strength_workout(workout: dict) -> BaseWorkout:
    counter = Counter()
    groups = Counter()
    return BaseWorkout(
        workoutName=workout["title"],
        sportType=_STRENGTH_SPORT,
        subSportType="GENERIC",
        description=workout.get("description", ""),
        estimatedDurationInSecs=workout.get("estimated_duration_sec", 0),
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType=_STRENGTH_SPORT,
                workoutSteps=[
                    _build_strength_step(step, counter, groups) for step in workout["steps"]
                ],
            )
        ],
    )


def _build_strength_step(step: dict, counter: Counter, groups: Counter):
    if step["type"] == "repeat":
        order = counter.next()
        child_id = groups.next()
        children = [_build_strength_step(child, counter, groups) for child in step["steps"]]
        for child in children:
            child.childStepId = child_id
        group = create_repeat_group(
            iterations=step["count"], workout_steps=children, step_order=order
        )
        group.childStepId = child_id
        return group

    type_id, type_key, display = _STRENGTH_STEP_TYPES[step["type"]]
    duration = step["duration"]
    kwargs = {
        "stepOrder": counter.next(),
        "stepType": {"stepTypeId": type_id, "stepTypeKey": type_key, "displayOrder": display},
        "endCondition": _REPS_CONDITION if duration["type"] == "reps" else _TIME_CONDITION,
        "endConditionValue": float(duration["value"]),
        "targetType": _NO_TARGET,
        "weightUnit": _KILOGRAM,
    }
    if step.get("description"):
        kwargs["description"] = step["description"]

    if step["type"] != "rest":
        # Validation has already resolved these, so this cannot raise here.
        if step.get("exercise"):
            category, name = exercises.resolve(step["exercise"], step.get("category"))
        else:
            category, name = exercises.resolve_generic(step["category"])
        kwargs["category"] = category
        kwargs["exerciseName"] = name
        weight = step.get("weight_kg")
        if weight:
            kwargs["weightValue"] = float(weight)
    return ExecutableStep(**kwargs)


def content_hash(workout: dict) -> str:
    digest = hashlib.sha256(
        json.dumps(workout, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"


def _build_step(step: dict, counter: Counter):
    if step["type"] == "repeat":
        order = counter.next()
        children = [_build_step(child, counter) for child in step["steps"]]
        for child_order, child in enumerate(children, 1):
            child.stepOrder = child_order
        return create_repeat_group(
            iterations=step["count"], workout_steps=children, step_order=order
        )

    type_id, type_key, display = _STEP_TYPES[step["type"]]
    duration = step["duration"]
    if duration["type"] == "time":
        condition = {
            "conditionTypeId": ConditionType.TIME,
            "conditionTypeKey": "time",
            "displayOrder": 2,
            "displayable": True,
        }
    else:
        condition = {
            "conditionTypeId": 3,
            "conditionTypeKey": "distance",
            "displayOrder": 3,
            "displayable": True,
        }

    target, low, high = _target(step["target"])
    kwargs = {
        "stepOrder": counter.next(),
        "stepType": {
            "stepTypeId": type_id,
            "stepTypeKey": type_key,
            "displayOrder": display,
        },
        "endCondition": condition,
        "endConditionValue": float(duration["value"]),
        "targetType": target,
    }
    if duration["type"] == "distance":
        kwargs["preferredEndConditionUnit"] = {
            "unitId": 2,
            "unitKey": "kilometer",
            "factor": 100000.0,
        }
    if low is not None:
        kwargs["targetValueOne"] = low
        kwargs["targetValueTwo"] = high
    return ExecutableStep(**kwargs)


def _target(target: dict) -> tuple[dict, float | None, float | None]:
    if target["type"] == "no_target":
        return (
            {
                "workoutTargetTypeId": TargetType.NO_TARGET,
                "workoutTargetTypeKey": "no.target",
                "displayOrder": 1,
            },
            None,
            None,
        )
    if target["type"] == "heart_rate":
        return (
            {
                "workoutTargetTypeId": TargetType.HEART_RATE,
                "workoutTargetTypeKey": "heart.rate.zone",
                "displayOrder": 4,
            },
            float(target["low"]),
            float(target["high"]),
        )
    slow = _speed(target["slow"])
    fast = _speed(target["fast"])
    return (
        {
            "workoutTargetTypeId": 6,
            "workoutTargetTypeKey": "pace.zone",
            "displayOrder": 6,
        },
        slow,
        fast,
    )


def _speed(pace: str) -> float:
    minutes, seconds = pace.split(":")
    return 1000.0 / (int(minutes) * 60 + int(seconds))
