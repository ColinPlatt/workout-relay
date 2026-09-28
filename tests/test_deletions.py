"""Removing workouts: the session call, the queueing rules, and the worker.

Deleting is the one irreversible thing this service does to a Garmin account,
so the tests lean on what must never happen — touching a workout we did not
create, deleting one mid-publish, or leaving rows behind that would make a
later re-send believe the workout still exists.
"""

import copy
import json

import pytest
from sqlalchemy import select

from workout_relay import garmin
from workout_relay.app import UserLockPool, process_next_submission
from workout_relay.database import (
    Database,
    GarminConnection,
    PlanSubmission,
    User,
    WorkoutLink,
    WorkoutOperation,
)
from workout_relay.garmin import LiveGarminSession, MockGarminGateway
from workout_relay.plans import EXAMPLE_PLAN
from workout_relay.retention import GarminTokens
from workout_relay.security import TokenVault
from workout_relay.submissions import PlanRejected, queue_deletion, scheduled_workouts
from test_garmin import RecoveryGarmin
from test_worker import worker_settings  # noqa: F401  (parametrizes sqlite/postgres)


class DeletableGarmin(RecoveryGarmin):
    """A client that records removals and can report things already gone."""

    def __init__(self, missing=()):
        super().__init__()
        self.missing = set(missing)
        self.unschedule_calls = []
        self.delete_calls = []

    def unschedule_workout(self, schedule_id):
        self.unschedule_calls.append(schedule_id)
        if "schedule" in self.missing:
            raise garmin.GarminConnectConnectionError("API Error 404 - not found")

    def delete_workout(self, workout_id):
        self.delete_calls.append(workout_id)
        if "workout" in self.missing:
            raise garmin.GarminConnectConnectionError("API Error 404 - not found")


# --- the session call ------------------------------------------------------


def test_remove_unschedules_then_deletes():
    client = DeletableGarmin()
    outcome = LiveGarminSession(client).remove("42", "84")
    assert client.unschedule_calls == [84] and client.delete_calls == [42]
    assert outcome == {"unscheduled": True, "deleted": True, "already_gone": False}


@pytest.mark.parametrize("missing", ["schedule", "workout"])
def test_remove_treats_a_precise_404_as_the_outcome_we_wanted(missing):
    client = DeletableGarmin([missing])
    outcome = LiveGarminSession(client).remove("42", "84")
    # Whichever half was already gone, the delete is still attempted and the
    # call reports success: removing twice must be safe.
    assert outcome["already_gone"] is True
    assert client.delete_calls == [42]


def test_remove_without_a_schedule_id_skips_the_calendar_call():
    client = DeletableGarmin()
    outcome = LiveGarminSession(client).remove("42", None)
    assert client.unschedule_calls == [] and client.delete_calls == [42]
    assert outcome["unscheduled"] is False and outcome["deleted"] is True


def test_remove_surfaces_a_real_failure_rather_than_swallowing_it():
    client = DeletableGarmin()

    def boom(workout_id):
        raise garmin.GarminConnectConnectionError("API Error 500 - server error")

    client.delete_workout = boom
    with pytest.raises(garmin.GarminError):
        LiveGarminSession(client).remove("42", "84")


# --- queueing rules --------------------------------------------------------


def seed_links(database, vault, links):
    """An account with Garmin connected and the given workouts on record."""
    database.initialize()
    with database.session() as db:
        user = User(email="delete@example.com", password_hash="unused")
        db.add(user)
        db.flush()
        db.add(
            GarminConnection(
                user_id=user.id,
                encrypted_tokens=vault._vault.encrypt("test-token"),
                retention="persistent",
            )
        )
        for index, (key, date) in enumerate(links):
            # Garmin ids are numeric, and `remove` casts them, so the fixture
            # has to be numeric too or it tests a shape that cannot occur.
            db.add(
                WorkoutLink(
                    user_id=user.id,
                    workout_key=key,
                    title=f"Session {key}",
                    garmin_workout_id=str(1000 + index),
                    garmin_schedule_id=str(2000 + index),
                    scheduled_date=date,
                    content_hash="hash-" + key,
                )
            )
        db.commit()
        return user.id


@pytest.fixture
def store(settings):
    database = Database(settings.database_url)
    vault = GarminTokens(TokenVault(settings.master_encryption_key), settings.garmin_visit_minutes)
    yield database, vault
    database.engine.dispose()


def test_listing_shows_what_could_be_deleted(store):
    database, vault = store
    user_id = seed_links(database, vault, [("a", "2026-10-01"), ("b", "2026-10-05")])
    with database.session() as db:
        items = scheduled_workouts(db, user_id)
    assert [item["workout_id"] for item in items] == ["b", "a"]  # newest date first
    assert items[0]["title"] == "Session b"


def test_an_id_we_do_not_track_is_refused(store):
    database, vault = store
    user_id = seed_links(database, vault, [("a", "2026-10-01")])
    with database.session() as db:
        with pytest.raises(PlanRejected) as rejected:
            queue_deletion(db, database, user_id, ["a", "not-ours"])
    assert rejected.value.code == "workout_not_found"
    assert rejected.value.errors[0]["params"]["workout_id"] == "not-ours"


def test_a_garmin_id_is_never_accepted_from_a_caller(store):
    database, vault = store
    user_id = seed_links(database, vault, [("a", "2026-10-01")])
    with database.session() as db:
        # 1000 is the real Garmin workout id, deliberately not a valid handle.
        with pytest.raises(PlanRejected) as rejected:
            queue_deletion(db, database, user_id, ["1000"])
    assert rejected.value.code == "workout_not_found"


def test_another_account_cannot_name_our_workout(store):
    database, vault = store
    seed_links(database, vault, [("a", "2026-10-01")])
    with database.session() as db:
        stranger = User(email="stranger@example.com", password_hash="unused")
        db.add(stranger)
        db.commit()
        with pytest.raises(PlanRejected) as rejected:
            queue_deletion(db, database, stranger.id, ["a"])
    assert rejected.value.code == "workout_not_found"


def test_a_past_session_is_refused_unless_asked_for(store):
    database, vault = store
    user_id = seed_links(database, vault, [("old", "2020-01-01")])
    with database.session() as db:
        with pytest.raises(PlanRejected) as rejected:
            queue_deletion(db, database, user_id, ["old"])
        assert rejected.value.code == "workout_in_past"
        accepted = queue_deletion(db, database, user_id, ["old"], include_past=True)
    assert accepted.workout_count == 1


def test_an_empty_or_oversized_batch_is_refused(store):
    database, vault = store
    user_id = seed_links(database, vault, [("a", "2026-10-01")])
    with database.session() as db:
        for ids, code in (([], "no_workouts_named"), (["a"] * 51, "too_many_workouts")):
            with pytest.raises(PlanRejected) as rejected:
                queue_deletion(db, database, user_id, ids)
            assert rejected.value.code == code


# --- the worker ------------------------------------------------------------


class DeletingGateway(MockGarminGateway):
    def __init__(self, client):
        super().__init__()
        self.client = client

    def open_session(self, token_bundle):
        return LiveGarminSession(self.client)


async def run_worker(database, vault, gateway):
    assert await process_next_submission(database, vault, gateway, UserLockPool())


@pytest.mark.anyio
async def test_deleting_removes_the_workout_and_every_row_that_tracked_it(worker_settings):
    database = Database(worker_settings.database_url)
    vault = GarminTokens(
        TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes
    )
    user_id = seed_links(database, vault, [("a", "2026-10-01")])
    with database.session() as db:
        db.add(
            WorkoutOperation(
                user_id=user_id,
                workout_key="a",
                content_hash="hash-a",
                progress=json.dumps({"stage": "completed"}),
            )
        )
        submission_id = queue_deletion(db, database, user_id, ["a"]).submission_id
        db.commit()
    client = DeletableGarmin()
    await run_worker(database, vault, DeletingGateway(client))

    assert client.unschedule_calls == [2000] and client.delete_calls == [1000]
    with database.session() as db:
        submission = db.get(PlanSubmission, submission_id)
        assert submission.status == "completed"
        assert json.loads(submission.result)["counts"] == {
            "deleted": 1, "already_gone": 0, "refused": 0
        }
        # Both rows must go, or a later re-send would skip the workout as
        # already published.
        assert db.scalar(select(WorkoutLink).where(WorkoutLink.workout_key == "a")) is None
        assert db.scalar(
            select(WorkoutOperation).where(WorkoutOperation.workout_key == "a")
        ) is None
    database.engine.dispose()


@pytest.mark.anyio
async def test_deletion_keeps_tokens_refreshed_while_opening_garmin(worker_settings):
    database = Database(worker_settings.database_url)
    vault = GarminTokens(
        TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes
    )
    user_id = seed_links(database, vault, [("a", "2026-10-01")])
    with database.session() as db:
        queue_deletion(db, database, user_id, ["a"])
        db.commit()

    await run_worker(database, vault, DeletingGateway(DeletableGarmin()))

    with database.session() as db:
        connection = db.get(GarminConnection, user_id)
        assert vault.read(connection) == '{"token":"refreshed"}'
    database.engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("journal_schedule, expected_calls", [("9999", [9999]), (None, [])])
async def test_deletion_uses_newer_journal_identity(
    worker_settings, journal_schedule, expected_calls
):
    database = Database(worker_settings.database_url)
    vault = GarminTokens(
        TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes
    )
    user_id = seed_links(database, vault, [("a", "2026-10-01")])
    with database.session() as db:
        db.add(WorkoutOperation(
            user_id=user_id,
            workout_key="a",
            content_hash="new-hash",
            progress=json.dumps({
                "stage": "updated",
                "workout_id": "999",
                "schedule_id": journal_schedule,
                "scheduled_date": "2026-10-03",
            }),
        ))
        queue_deletion(db, database, user_id, ["a"])
        db.commit()
    client = DeletableGarmin()

    await run_worker(database, vault, DeletingGateway(client))

    assert client.unschedule_calls == expected_calls
    assert client.delete_calls == [999]
    database.engine.dispose()


@pytest.mark.anyio
async def test_a_workout_already_gone_in_garmin_counts_as_deleted(worker_settings):
    database = Database(worker_settings.database_url)
    vault = GarminTokens(
        TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes
    )
    user_id = seed_links(database, vault, [("a", "2026-10-01")])
    with database.session() as db:
        submission_id = queue_deletion(db, database, user_id, ["a"]).submission_id
        db.commit()
    await run_worker(database, vault, DeletingGateway(DeletableGarmin(["schedule", "workout"])))
    with database.session() as db:
        result = json.loads(db.get(PlanSubmission, submission_id).result)
    assert result["counts"] == {"deleted": 0, "already_gone": 1, "refused": 0}
    assert result["workouts"][0]["action"] == "already_gone"
    database.engine.dispose()


@pytest.mark.anyio
async def test_an_unresolved_upload_blocks_deletion_instead_of_racing_it(worker_settings):
    database = Database(worker_settings.database_url)
    vault = GarminTokens(
        TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes
    )
    user_id = seed_links(database, vault, [("a", "2026-10-01")])
    with database.session() as db:
        db.add(
            WorkoutOperation(
                user_id=user_id,
                workout_key="a",
                content_hash="hash-a",
                # Mid-create: Garmin may still be about to accept this.
                progress=json.dumps({"stage": "creating"}),
            )
        )
        submission_id = queue_deletion(db, database, user_id, ["a"]).submission_id
        db.commit()
    client = DeletableGarmin()
    await run_worker(database, vault, DeletingGateway(client))

    assert client.delete_calls == []  # nothing was touched at Garmin
    with database.session() as db:
        result = json.loads(db.get(PlanSubmission, submission_id).result)
        assert result["counts"]["refused"] == 1
        assert result["workouts"][0]["code"] == "garmin_prior_upload_unresolved"
        # The link survives a refusal, so the workout is still tracked.
        assert db.scalar(select(WorkoutLink).where(WorkoutLink.workout_key == "a")) is not None
    database.engine.dispose()


@pytest.mark.anyio
async def test_a_workout_can_be_sent_again_after_being_deleted(worker_settings):
    """The proof that deletion really released the workout, not just hid it."""
    database = Database(worker_settings.database_url)
    vault = GarminTokens(
        TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes
    )
    database.initialize()
    plan = copy.deepcopy(EXAMPLE_PLAN)
    plan["workouts"] = [plan["workouts"][0]]
    with database.session() as db:
        user = User(email="resend@example.com", password_hash="unused")
        db.add(user)
        db.flush()
        user_id = user.id
        db.add(
            GarminConnection(
                user_id=user_id,
                encrypted_tokens=vault._vault.encrypt("test-token"),
                retention="persistent",
            )
        )
        db.add(
            PlanSubmission(
                user_id=user_id, plan_id=plan["plan_id"], title=plan["title"],
                content=json.dumps(plan), status="queued",
            )
        )
        db.commit()

    client = DeletableGarmin()
    gateway = DeletingGateway(client)
    await run_worker(database, vault, gateway)  # publish
    with database.session() as db:
        link = db.scalar(select(WorkoutLink).where(WorkoutLink.user_id == user_id))
        assert link is not None
        key = link.workout_key
        queue_deletion(db, database, user_id, [key], include_past=True)
        db.commit()
    await run_worker(database, vault, gateway)  # delete
    assert client.delete_calls

    with database.session() as db:
        db.add(
            PlanSubmission(
                user_id=user_id, plan_id=plan["plan_id"], title=plan["title"],
                content=json.dumps(plan), status="queued",
            )
        )
        db.commit()
    before = client.creates
    await run_worker(database, vault, gateway)  # publish again
    assert client.creates == before + 1  # recreated, not skipped as unchanged
    with database.session() as db:
        assert db.scalar(select(WorkoutLink).where(WorkoutLink.user_id == user_id)) is not None
    database.engine.dispose()
