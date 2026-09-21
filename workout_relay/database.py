"""SQLAlchemy models and repository operations."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, create_engine, delete, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .security import hash_token


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    preferred_language: Mapped[str | None] = mapped_column(String(2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BrowserSession(Base):
    __tablename__ = "browser_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    user: Mapped[User] = relationship()


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes: Mapped[str] = mapped_column(String(200), default="plans:read plans:write")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GarminConnection(Base):
    __tablename__ = "garmin_connections"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    encrypted_tokens: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="connected")
    last_validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class PlanSubmission(Base):
    __tablename__ = "plan_submissions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), index=True)
    result: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkoutLink(Base):
    __tablename__ = "workout_links"
    __table_args__ = (UniqueConstraint("user_id", "workout_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workout_key: Mapped[str] = mapped_column(String(160))
    content_hash: Mapped[str] = mapped_column(String(71))
    garmin_workout_id: Mapped[str] = mapped_column(String(80))
    garmin_schedule_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    scheduled_date: Mapped[str] = mapped_column(String(10))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class WorkoutOperation(Base):
    """Durable progress, retained even when a submission fails or expires."""

    __tablename__ = "workout_operations"
    __table_args__ = (UniqueConstraint("user_id", "workout_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workout_key: Mapped[str] = mapped_column(String(160))
    content_hash: Mapped[str] = mapped_column(String(71))
    progress: Mapped[str] = mapped_column(Text, default="{}")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    event: Mapped[str] = mapped_column(String(80), index=True)
    detail: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Database:
    def __init__(self, url: str):
        kwargs = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, pool_pre_ping=True, connect_args=kwargs)
        self._owner_mutex = threading.Lock()
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        with self.engine.begin() as connection:
            if self.engine.dialect.name == "postgresql":
                connection.exec_driver_sql("SELECT pg_advisory_xact_lock(78234602)")
            Base.metadata.create_all(connection)

    @contextmanager
    def upload_owner(self):
        """One uploader across processes, including overlapping deployments.

        The dedicated connection owns the PostgreSQL session lock. Never return
        it to the pool while locked. SQLite development uses an OS file lock.

        The ownership check runs both on the event loop and, through a
        checkpoint, on the Garmin worker thread. Those never overlap while the
        loop awaits that thread, but the mutex makes the invariant explicit and
        keeps a check from racing the release.
        """
        if self.engine.dialect.name == "postgresql":
            with self.engine.connect() as connection:
                if not connection.exec_driver_sql("SELECT pg_try_advisory_lock(78234601)").scalar():
                    yield None
                    return
                backend = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar()

                def check():
                    with self._owner_mutex:
                        if connection.invalidated or connection.exec_driver_sql("SELECT pg_backend_pid()").scalar() != backend:
                            raise RuntimeError("upload_ownership_lost")

                try:
                    yield check
                finally:
                    with self._owner_mutex:
                        try:
                            connection.exec_driver_sql("SELECT pg_advisory_unlock(78234601)")
                        except Exception:
                            connection.invalidate()
        elif self.engine.dialect.name == "sqlite" and self.engine.url.database not in (None, "", ":memory:"):
            try:
                import fcntl
            except ImportError:  # pragma: no cover - platform specific
                raise ValueError(
                    "File-locked SQLite uploads need a Unix host. Run the Docker "
                    "image, or point DATABASE_URL at PostgreSQL."
                ) from None
            lock_path = Path(self.engine.url.database).resolve().with_suffix(".upload.lock")
            with lock_path.open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    yield None
                    return
                try:
                    yield lambda: None
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        else:
            raise ValueError("Uploads require PostgreSQL or file-backed SQLite")

    def session(self) -> Session:
        return self.sessions()

    def create_browser_session(self, db: Session, user_id: str, token: str, csrf: str, days: int) -> None:
        db.add(
            BrowserSession(
                token_hash=hash_token(token),
                user_id=user_id,
                csrf_hash=hash_token(csrf),
                expires_at=now() + timedelta(days=days),
            )
        )

    def actor_from_session(self, db: Session, token: str) -> tuple[User, str] | None:
        item = db.get(BrowserSession, hash_token(token))
        if not item or _as_utc(item.expires_at) <= now():
            return None
        return item.user, item.csrf_hash

    def actor_from_api_key(self, db: Session, token: str) -> tuple[User, set[str]] | None:
        key = db.scalar(select(ApiKey).where(ApiKey.token_hash == hash_token(token)))
        if not key:
            return None
        key.last_used_at = now()
        user = db.get(User, key.user_id)
        return (user, set(key.scopes.split())) if user else None

    def audit(self, db: Session, event: str, user_id: str | None, detail: dict | None = None) -> None:
        db.add(AuditEvent(event=event, user_id=user_id, detail=json.dumps(detail or {})))

    def purge_session(self, db: Session, token: str) -> None:
        db.execute(delete(BrowserSession).where(BrowserSession.token_hash == hash_token(token)))


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _enable_sqlite_foreign_keys(connection, _record) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
