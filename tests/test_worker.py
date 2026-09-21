import asyncio
import copy
import json
import os
import threading
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url

from workout_relay.app import UserLockPool, create_app, process_next_submission
from workout_relay.database import Database, GarminConnection, PlanSubmission, User, WorkoutLink, WorkoutOperation
from workout_relay.garmin import LiveGarminSession, MockGarminGateway
from workout_relay.plans import EXAMPLE_PLAN
from workout_relay.retention import GarminTokens
from workout_relay.security import TokenVault
from workout_relay.workouts import content_hash
from test_garmin import RecoveryGarmin


@pytest.fixture(params=["sqlite", "postgresql"])
def worker_settings(settings, request):
    if request.param == "sqlite":
        yield settings
        return
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL must point to a disposable test database")
    # Isolate every test without modifying existing application tables.
    schema = "relay_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
    test_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        yield replace(settings, database_url=test_url.render_as_string(hide_password=False))
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
        admin.dispose()


def seed(database, tokens):
    database.initialize()
    plan = copy.deepcopy(EXAMPLE_PLAN)
    plan["workouts"] = [plan["workouts"][0]]
    with database.session() as db:
        user = User(email="worker@example.com", password_hash="unused")
        db.add(user)
        db.flush()
        db.add(GarminConnection(user_id=user.id, encrypted_tokens=tokens._vault.encrypt("test-token"), retention="persistent"))
        job = PlanSubmission(user_id=user.id, plan_id=plan["plan_id"], title=plan["title"], content=json.dumps(plan), status="queued")
        db.add(job)
        db.commit()
        return job.id, user.id, plan


async def wait_started(event):
    for _ in range(300):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("worker did not start")


class BlockingGateway(MockGarminGateway):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.client = RecoveryGarmin()

    def open_session(self, token_bundle):
        self.started.set()
        if not self.release.wait(10):
            raise RuntimeError("test did not release worker")
        return LiveGarminSession(self.client)


@pytest.mark.anyio
async def test_overlapping_startup_cannot_requeue_or_claim_active_job(worker_settings):
    database = Database(worker_settings.database_url)
    vault = GarminTokens(TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes)
    job_id, _, _ = seed(database, vault)
    gateway = BlockingGateway()
    first = asyncio.create_task(process_next_submission(database, vault, gateway, UserLockPool()))
    replacement = create_app(worker_settings, gateway)
    try:
        await wait_started(gateway.started)
        async with replacement.router.lifespan_context(replacement):
            assert not await process_next_submission(replacement.state.database, vault, gateway, UserLockPool())
            with database.session() as db:
                assert db.get(PlanSubmission, job_id).status == "processing"
    finally:
        gateway.release.set()
        await first
        replacement.state.database.engine.dispose()
        database.engine.dispose()
    assert gateway.client.creates == gateway.client.schedules == 1


@pytest.mark.anyio
async def test_cancellation_keeps_lock_until_thread_exits(worker_settings):
    database = Database(worker_settings.database_url)
    contender = Database(worker_settings.database_url)
    vault = GarminTokens(TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes)
    job_id, _, _ = seed(database, vault)
    gateway = BlockingGateway()
    first = asyncio.create_task(process_next_submission(database, vault, gateway, UserLockPool()))
    try:
        await wait_started(gateway.started)
        first.cancel()
        await asyncio.sleep(0.01)
        assert not first.done()
        assert not await process_next_submission(contender, vault, gateway, UserLockPool())
    finally:
        gateway.release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
    assert await process_next_submission(contender, vault, gateway, UserLockPool())
    with database.session() as db:
        assert db.get(PlanSubmission, job_id).status == "completed"
    assert gateway.client.creates == gateway.client.schedules == 1
    database.engine.dispose()
    contender.engine.dispose()


@pytest.mark.anyio
async def test_durable_recovery_after_worker_replacement(worker_settings):
    database = Database(worker_settings.database_url)
    vault = GarminTokens(TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes)
    job_id, user_id, plan = seed(database, vault)
    client = RecoveryGarmin("schedule")

    class Gateway(MockGarminGateway):
        def open_session(self, token_bundle):
            return LiveGarminSession(client)

    await process_next_submission(database, vault, Gateway(), UserLockPool())
    with database.session() as db:
        assert db.get(PlanSubmission, job_id).status == "failed"
        operation = db.scalar(select(WorkoutOperation))
        assert json.loads(operation.progress)["workout_id"] == "42"
        assert json.loads(operation.progress)["stage"] == "scheduling"
        # Crash recovery: a new process sees the durable operation, not memory.
        db.get(PlanSubmission, job_id).status = "processing"
        db.commit()
    database.engine.dispose()
    replacement = Database(worker_settings.database_url)
    await process_next_submission(replacement, vault, Gateway(), UserLockPool())
    with replacement.session() as db:
        assert db.get(PlanSubmission, job_id).status == "completed"
        assert db.scalar(select(WorkoutLink)).garmin_workout_id == "42"
        assert json.loads(db.scalar(select(WorkoutOperation)).progress)["stage"] == "completed"
    assert client.creates == client.schedules == 1
    replacement.engine.dispose()


@pytest.mark.anyio
async def test_unfinished_operation_cannot_be_overwritten_by_changed_plan(worker_settings):
    database = Database(worker_settings.database_url)
    vault = GarminTokens(TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes)
    _, user_id, plan = seed(database, vault)
    client = RecoveryGarmin("create", accepted=False)

    class Gateway(MockGarminGateway):
        def open_session(self, token_bundle):
            return LiveGarminSession(client)

    await process_next_submission(database, vault, Gateway(), UserLockPool())
    plan["workouts"][0]["date"] = "2026-10-05"
    with database.session() as db:
        job = PlanSubmission(user_id=user_id, plan_id=plan["plan_id"], title=plan["title"], content=json.dumps(plan), status="queued")
        db.add(job)
        db.commit()
        job_id = job.id
    await process_next_submission(database, vault, Gateway(), UserLockPool())
    with database.session() as db:
        assert json.loads(db.get(PlanSubmission, job_id).result)["code"] == "garmin_prior_upload_unresolved"
    assert client.creates == 1 and client.schedules == 0
    database.engine.dispose()


def test_owner_is_released_after_exception(worker_settings):
    first, second = Database(worker_settings.database_url), Database(worker_settings.database_url)
    with pytest.raises(RuntimeError):
        with first.upload_owner() as guard:
            assert guard is not None
            with second.upload_owner() as other:
                assert other is None
            raise RuntimeError("worker crashed")
    with second.upload_owner() as guard:
        assert guard is not None
        guard()
    first.engine.dispose()
    second.engine.dispose()


def test_postgres_connection_loss_revokes_ownership(worker_settings):
    if not worker_settings.database_url.startswith("postgresql"):
        pytest.skip("PostgreSQL-specific session failure")
    first, second = Database(worker_settings.database_url), Database(worker_settings.database_url)
    try:
        with first.upload_owner() as guard:
            assert guard is not None
            with second.engine.begin() as connection:
                pid = connection.exec_driver_sql(
                    "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND objid = 78234601 AND granted"
                ).scalar_one()
                connection.exec_driver_sql("SELECT pg_terminate_backend(%s)", (pid,))
            with pytest.raises(Exception):
                guard()
            with second.upload_owner() as replacement:
                assert replacement is not None
                replacement()
    finally:
        first.engine.dispose()
        second.engine.dispose()


def queue(database, user_id, plan):
    """Replace the seeded submission with this one, so it is processed next."""
    with database.session() as db:
        db.query(PlanSubmission).delete()
        job = PlanSubmission(user_id=user_id, plan_id=plan["plan_id"], title=plan["title"],
                             content=json.dumps(plan), status="queued")
        db.add(job)
        db.commit()
        return job.id


def journal(database, user_id, workout, progress, digest=None):
    with database.session() as db:
        db.add(WorkoutOperation(user_id=user_id, workout_key=workout["id"],
                                content_hash=digest or content_hash(workout),
                                progress=json.dumps(progress)))
        db.commit()


def gateway_for(client):
    class Gateway(MockGarminGateway):
        def open_session(self, token_bundle):
            return LiveGarminSession(client)

    return Gateway()


IDENTITY = {"workout_id": "42", "schedule_id": "84", "scheduled_date": "2026-10-01"}


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["updating", "updated", "unscheduling", "completed"])
async def test_changed_payload_replaces_an_interrupted_update(worker_settings, stage):
    database = Database(worker_settings.database_url)
    vault = GarminTokens(TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes)
    _, user_id, plan = seed(database, vault)
    journal(database, user_id, plan["workouts"][0], dict(IDENTITY, stage=stage))
    changed = copy.deepcopy(plan)
    changed["workouts"][0]["date"] = "2026-10-05"
    changed["workouts"][0]["title"] = "Reworked session"
    job_id = queue(database, user_id, changed)
    client = RecoveryGarmin()

    await process_next_submission(database, vault, gateway_for(client), UserLockPool())
    with database.session() as db:
        assert db.get(PlanSubmission, job_id).status == "completed"
        link = db.scalar(select(WorkoutLink))
        assert (link.garmin_workout_id, link.scheduled_date) == ("42", "2026-10-05")
    # The known identity is reused: no second workout, no duplicate calendar entry.
    assert client.creates == 0 and client.schedules == 1 and client.unscheduled == [84]
    database.engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("progress", [
    {"stage": "creating", "marker": "[Workout Relay:test]"},
    dict(IDENTITY, stage="scheduling"),
    {"stage": "updating"},  # an older journal without a recorded identity
])
async def test_changed_payload_is_blocked_while_the_outcome_is_unknown(worker_settings, progress):
    database = Database(worker_settings.database_url)
    vault = GarminTokens(TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes)
    _, user_id, plan = seed(database, vault)
    journal(database, user_id, plan["workouts"][0], progress)
    changed = copy.deepcopy(plan)
    changed["workouts"][0]["title"] = "Reworked session"
    job_id = queue(database, user_id, changed)
    client = RecoveryGarmin()

    await process_next_submission(database, vault, gateway_for(client), UserLockPool())
    with database.session() as db:
        result = json.loads(db.get(PlanSubmission, job_id).result)
        assert result["code"] == "garmin_prior_upload_unresolved"
    assert client.creates == 0 and client.schedules == 0
    database.engine.dispose()


@pytest.mark.anyio
async def test_marker_cleanup_runs_after_the_link_is_committed(worker_settings):
    database = Database(worker_settings.database_url)
    vault = GarminTokens(TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes)
    job_id, _, _ = seed(database, vault)
    client = RecoveryGarmin()

    await process_next_submission(database, vault, gateway_for(client), UserLockPool())
    with database.session() as db:
        assert db.get(PlanSubmission, job_id).status == "completed"
        assert json.loads(db.scalar(select(WorkoutOperation)).progress)["cleanup"] is None
    url, payload = client.client.puts[-1]
    assert url.endswith("/workout/42") and "Workout Relay:" not in payload.get("description", "")
    assert client.creates == 1 and client.schedules == 1
    database.engine.dispose()


@pytest.mark.anyio
async def test_failed_cleanup_keeps_the_upload_successful_and_retries_later(worker_settings):
    database = Database(worker_settings.database_url)
    vault = GarminTokens(TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes)
    job_id, user_id, plan = seed(database, vault)
    client = RecoveryGarmin()
    working_put = client.client.put
    client.client.put = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("cleanup lost"))

    await process_next_submission(database, vault, gateway_for(client), UserLockPool())
    with database.session() as db:
        assert db.get(PlanSubmission, job_id).status == "completed"
        assert json.loads(db.scalar(select(WorkoutOperation)).progress)["cleanup"] == "pending"

    client.client.put = working_put
    retry_id = queue(database, user_id, plan)  # the same, unchanged plan
    await process_next_submission(database, vault, gateway_for(client), UserLockPool())
    with database.session() as db:
        result = json.loads(db.get(PlanSubmission, retry_id).result)
        assert result["counts"] == {"created": 0, "updated": 0, "skipped": 1}
        assert json.loads(db.scalar(select(WorkoutOperation)).progress)["cleanup"] is None
    # Cleanup never repeats a creation or a scheduling request.
    assert client.creates == 1 and client.schedules == 1
    database.engine.dispose()


@pytest.mark.anyio
async def test_shutdown_drains_through_repeated_cancellation(worker_settings):
    database = Database(worker_settings.database_url)
    vault = GarminTokens(TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes)
    job_id, _, _ = seed(database, vault)
    gateway = BlockingGateway()
    app = create_app(worker_settings, gateway)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    await wait_started(gateway.started)

    shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
    contender = Database(worker_settings.database_url)
    for _ in range(3):
        await asyncio.sleep(0.01)
        shutdown.cancel()
        # Ownership is held until the Garmin thread actually exits.
        assert not await process_next_submission(contender, vault, gateway, UserLockPool())
    assert not shutdown.done()

    gateway.release.set()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    with database.session() as db:
        assert db.get(PlanSubmission, job_id).status == "completed"
    with contender.upload_owner() as guard:
        assert guard is not None
    app.state.database.engine.dispose()
    contender.engine.dispose()
    database.engine.dispose()


@pytest.mark.anyio
async def test_completed_remote_write_is_not_forgotten_before_link_commit(worker_settings):
    """The journal, not the link, is the record of where a workout went."""
    database = Database(worker_settings.database_url)
    vault = GarminTokens(TokenVault(worker_settings.master_encryption_key), worker_settings.garmin_visit_minutes)
    job_id, user_id, plan = seed(database, vault)
    journal(database, user_id, plan["workouts"][0], dict(IDENTITY, stage="completed"))
    changed = copy.deepcopy(plan)
    changed["workouts"][0]["date"] = "2026-10-05"
    with database.session() as db:
        db.get(PlanSubmission, job_id).content = json.dumps(changed)
        db.commit()
    client = RecoveryGarmin()

    await process_next_submission(database, vault, gateway_for(client), UserLockPool())
    with database.session() as db:
        assert db.get(PlanSubmission, job_id).status == "completed"
        assert db.scalar(select(WorkoutLink)).garmin_workout_id == "42"
    # Reuses the recorded identity instead of creating a second workout.
    assert client.creates == 0
    database.engine.dispose()
