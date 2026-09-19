"""Strict, deterministic plan validation and assistant guidance."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

_SCHEMA_DIR = Path(__file__).parent / "schemas"
PLAN_SCHEMA = json.loads((_SCHEMA_DIR / "plan.schema.json").read_text())
EXAMPLE_PLAN = json.loads((_SCHEMA_DIR / "example-plan.json").read_text())
_VALIDATOR = Draft202012Validator(PLAN_SCHEMA, format_checker=FormatChecker())


def validate_plan(plan: Any) -> list[dict]:
    errors: list[dict] = []
    for error in sorted(_VALIDATOR.iter_errors(plan), key=lambda item: list(item.absolute_path)):
        path = _path(error.absolute_path)
        errors.append(
            {
                "path": path,
                "code": f"schema_{error.validator}",
                "params": _safe_params(error),
            }
        )
    if errors or not isinstance(plan, dict):
        return errors

    workouts = plan.get("workouts", [])
    ids: set[str] = set()
    for index, workout in enumerate(workouts):
        prefix = f"workouts[{index}]"
        workout_id = workout["id"]
        if workout_id in ids:
            errors.append({"path": f"{prefix}.id", "code": "duplicate_workout_id", "params": {}})
        ids.add(workout_id)
        try:
            date.fromisoformat(workout["date"])
        except ValueError:
            errors.append({"path": f"{prefix}.date", "code": "invalid_date", "params": {}})
        if not workout_id.startswith(workout["date"] + "_"):
            errors.append({"path": f"{prefix}.id", "code": "id_date_mismatch", "params": {}})

        expanded = 0
        for step_index, step in enumerate(workout["steps"]):
            expanded += step.get("count", 1) * len(step.get("steps", [step]))
            simple_steps = step.get("steps", [step])
            for child_index, simple in enumerate(simple_steps):
                target = simple["target"]
                target_path = f"{prefix}.steps[{step_index}]"
                if step["type"] == "repeat":
                    target_path += f".steps[{child_index}]"
                if target["type"] == "heart_rate" and target["low"] >= target["high"]:
                    errors.append(
                        {"path": f"{target_path}.target", "code": "target_order", "params": {}}
                    )
                if target["type"] == "pace" and _pace_seconds(target["slow"]) <= _pace_seconds(target["fast"]):
                    errors.append(
                        {"path": f"{target_path}.target", "code": "pace_order", "params": {}}
                    )
        if expanded > 50:
            errors.append(
                {
                    "path": f"{prefix}.steps",
                    "code": "expanded_steps",
                    "params": {"count": expanded, "maximum": 50},
                }
            )
    return errors


def plan_schema() -> dict:
    return copy.deepcopy(PLAN_SCHEMA)


def example_plan() -> dict:
    return copy.deepcopy(EXAMPLE_PLAN)


def assistant_instructions(language: str = "en", base_url: str = "") -> dict:
    if language == "fr":
        purpose = (
            "Produisez uniquement un objet JSON conforme au schéma Workout Relay. "
            "N'ajoutez ni Markdown, ni commentaire, ni champ inconnu."
        )
        rules = [
            "Utilisez schema_version 1 et sport running.",
            "Chaque identifiant d'entraînement commence par sa date YYYY-MM-DD suivie d'un souligné.",
            "Les durées time sont en secondes et distance en mètres.",
            "Une cible d'allure utilise slow et fast au format M:SS par kilomètre; slow doit être plus lent.",
            "Une cible cardiaque utilise low et high en bpm avec low strictement inférieur à high.",
            "Après expansion des répétitions, un entraînement ne peut pas dépasser 50 étapes.",
            "Validez le JSON avec l'endpoint de validation avant de le soumettre.",
            "Une soumission valide renvoie HTTP 202. Interrogez ensuite son URL de statut jusqu'à completed ou failed.",
        ]
    else:
        purpose = (
            "Return only a JSON object conforming to the Workout Relay schema. "
            "Do not add Markdown, comments, or unknown fields."
        )
        rules = [
            "Use schema_version 1 and sport running.",
            "Every workout id starts with its YYYY-MM-DD date followed by an underscore.",
            "time durations are seconds and distance durations are metres.",
            "Pace targets use slow and fast in M:SS per kilometre; slow must be the slower pace.",
            "Heart-rate targets use low and high bpm with low strictly below high.",
            "A workout may have no more than 50 steps after repeat expansion.",
            "Validate the JSON with the validation endpoint before submitting it.",
            "A valid submission returns HTTP 202. Poll its status URL until it is completed or failed.",
        ]
    root = base_url.rstrip("/")

    def endpoint(path: str) -> str:
        return f"{root}{path}" if root else path

    return {
        "language": language,
        "purpose": purpose,
        "rules": rules,
        "authentication": "Authorization: Bearer <Workout Relay API key>",
        "schema_url": endpoint("/api/v1/plan-schema"),
        "example_url": endpoint("/api/v1/plan-example"),
        "validate_url": endpoint("/api/v1/plans/validate"),
        "submit_url": endpoint("/api/v1/plans"),
        "status_url_template": endpoint("/api/v1/plans/{submission_id}"),
    }


def _pace_seconds(value: str) -> int:
    minutes, seconds = value.split(":")
    return int(minutes) * 60 + int(seconds)


def _path(parts) -> str:
    value = "$"
    for part in parts:
        value += f"[{part}]" if isinstance(part, int) else f".{part}"
    return value


def _safe_params(error) -> dict:
    if error.validator == "required":
        missing = error.message.split("'")[1] if "'" in error.message else "field"
        return {"field": missing}
    if error.validator in {"minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"}:
        return {"limit": error.validator_value}
    if error.validator == "additionalProperties":
        return {"detail": error.message}
    if error.validator == "const":
        return {"expected": error.validator_value}
    return {}
