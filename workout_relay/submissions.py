"""Plan acceptance, shared by the REST API and the MCP connector.

Both entry points must apply the same size limit, the same validation and the
same Garmin precondition, so they call this rather than repeating it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Database, GarminConnection, PlanSubmission
from .plans import validate_plan


class PlanRejected(Exception):
    """A plan the caller can fix, with a stable code and structured errors."""

    def __init__(self, code: str, errors: list[dict] | None = None):
        super().__init__(code)
        self.code = code
        self.errors = errors or []


@dataclass(frozen=True)
class Accepted:
    submission_id: str
    workout_count: int

    def as_dict(self) -> dict:
        return {
            "id": self.submission_id,
            "status": "queued",
            "workout_count": self.workout_count,
        }


def check_plan(plan: dict, max_bytes: int) -> None:
    if len(json.dumps(plan, separators=(",", ":")).encode()) > max_bytes:
        raise PlanRejected("plan_too_large")
    errors = validate_plan(plan)
    if errors:
        raise PlanRejected("plan_invalid", errors)


def queue_plan(
    db: Session, database: Database, user_id: str, plan: dict, max_bytes: int
) -> Accepted:
    """Validate, check Garmin, and persist the plan as queued.

    The caller commits nothing else and wakes the worker afterwards.
    """
    check_plan(plan, max_bytes)
    connection = db.get(GarminConnection, user_id)
    if not connection or connection.status != "connected":
        raise PlanRejected("garmin_not_connected")
    submission = PlanSubmission(
        user_id=user_id,
        plan_id=plan["plan_id"],
        title=plan["title"],
        content=json.dumps(plan, separators=(",", ":")),
        status="queued",
    )
    db.add(submission)
    database.audit(db, "plan.queued", user_id, {"submission_id": submission.id})
    db.commit()
    return Accepted(submission.id, len(plan["workouts"]))


def recent_submissions(db: Session, user_id: str, limit: int = 10) -> list[PlanSubmission]:
    return list(
        db.scalars(
            select(PlanSubmission)
            .where(PlanSubmission.user_id == user_id)
            .order_by(PlanSubmission.created_at.desc())
            .limit(max(1, min(limit, 50)))
        ).all()
    )
