"""Plan acceptance, shared by the REST API and the MCP connector.

Both entry points must apply the same size limit, the same validation and the
same Garmin precondition, so they call this rather than repeating it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Database, GarminConnection, PlanSubmission, WorkoutLink
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


MAX_DELETIONS = 50


def scheduled_workouts(db: Session, user_id: str, limit: int = 100) -> list[dict]:
    """Workouts this account has on Garmin, newest date first.

    The delete tools read from here, so a person can see what exists before
    naming anything for removal.
    """
    links = db.scalars(
        select(WorkoutLink)
        .where(WorkoutLink.user_id == user_id)
        .order_by(WorkoutLink.scheduled_date.desc())
        .limit(max(1, min(limit, 200)))
    ).all()
    return [
        {
            "workout_id": link.workout_key,
            "title": link.title,
            "scheduled_date": link.scheduled_date,
            "sent_at": link.updated_at.isoformat() if link.updated_at else None,
        }
        for link in links
    ]


def queue_deletion(
    db: Session,
    database: Database,
    user_id: str,
    workout_ids: list,
    include_past: bool = False,
) -> Accepted:
    """Queue a removal of workouts this account scheduled through the service.

    Only ids we still track are accepted: a Garmin id from a caller is never
    honoured, so a connector cannot reach a workout the person made elsewhere.
    A past date is refused unless asked for explicitly, because removing a
    session that has already happened rewrites training history.
    """
    if not isinstance(workout_ids, list) or not workout_ids:
        raise PlanRejected("no_workouts_named")
    if len(workout_ids) > MAX_DELETIONS:
        raise PlanRejected("too_many_workouts")
    wanted = [str(item) for item in workout_ids]
    links = {
        link.workout_key: link
        for link in db.scalars(
            select(WorkoutLink).where(
                WorkoutLink.user_id == user_id, WorkoutLink.workout_key.in_(wanted)
            )
        ).all()
    }
    missing = [item for item in wanted if item not in links]
    if missing:
        raise PlanRejected(
            "workout_not_found",
            [{"path": f"workout_ids[{wanted.index(item)}]", "code": "workout_not_found",
              "params": {"workout_id": item}} for item in missing],
        )
    if not include_past:
        today = date.today().isoformat()
        past = [key for key in wanted if (links[key].scheduled_date or "") < today]
        if past:
            raise PlanRejected(
                "workout_in_past",
                [{"path": f"workout_ids[{wanted.index(key)}]", "code": "workout_in_past",
                  "params": {"workout_id": key, "scheduled_date": links[key].scheduled_date}}
                 for key in past],
            )
    submission = PlanSubmission(
        user_id=user_id,
        kind="deletion",
        plan_id="deletion",
        title=f"Delete {len(wanted)} workout(s)",
        content=json.dumps({"workout_ids": wanted}, separators=(",", ":")),
        # Its own status, so a process on the previous release never claims a
        # job whose payload it cannot read.
        status="queued_delete",
    )
    db.add(submission)
    database.audit(db, "workouts.delete_queued", user_id, {"count": len(wanted)})
    db.commit()
    return Accepted(submission.id, len(wanted))


def recent_submissions(db: Session, user_id: str, limit: int = 10) -> list[PlanSubmission]:
    return list(
        db.scalars(
            select(PlanSubmission)
            .where(PlanSubmission.user_id == user_id)
            .order_by(PlanSubmission.created_at.desc())
            .limit(max(1, min(limit, 50)))
        ).all()
    )
