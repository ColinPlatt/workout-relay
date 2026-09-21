"""Where Garmin session tokens are allowed to live.

Two choices, made by the person when they connect Garmin:

``persistent``
    The encrypted tokens are written to the database and stay until the person
    disconnects. Uploads work while the browser is closed, and an assistant can
    send plans unattended.

``visit``
    The encrypted tokens never reach the database. They live in this process
    only, and expire at a fixed time that refreshing does not extend. A restart
    or a deploy drops them, which is the intended behaviour, not a fault.

The expiry is enforced here, on the server. Detecting a closed browser would
not be a guarantee: a tab can be closed without any request reaching us.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from .database import GarminConnection, now
from .garmin import GarminError
from .security import TokenVault

PERSISTENT = "persistent"
VISIT = "visit"
RETENTIONS = (PERSISTENT, VISIT)


class GarminTokens:
    """Reads and writes Garmin tokens according to the account's choice."""

    def __init__(self, vault: TokenVault, visit_minutes: int):
        self._vault = vault
        self._visit_minutes = visit_minutes
        # user_id -> (ciphertext, expires_at). Encrypted even in memory, so a
        # crash dump is no more revealing than the database would be.
        self._held: dict[str, tuple[str, datetime]] = {}
        self._lock = threading.Lock()

    @property
    def visit_minutes(self) -> int:
        return self._visit_minutes

    def start(self, user_id: str, tokens: str, retention: str) -> tuple[str, datetime | None]:
        """Begin a connection; returns what the database row should hold."""
        if retention not in RETENTIONS:
            raise ValueError("unknown_retention")
        if retention == PERSISTENT:
            self.forget(user_id)
            return self._vault.encrypt(tokens), None
        expires_at = now() + timedelta(minutes=self._visit_minutes)
        with self._lock:
            self._held[user_id] = (self._vault.encrypt(tokens), expires_at)
        return "", expires_at

    def read(self, connection: GarminConnection) -> str:
        """The tokens for this connection, or a refusal naming why not."""
        if _retention(connection) == PERSISTENT:
            return self._vault.decrypt(connection.encrypted_tokens)
        with self._lock:
            held = self._held.get(connection.user_id)
        if held is None or held[1] <= now():
            self.forget(connection.user_id)
            raise GarminError("garmin_visit_expired")
        return self._vault.decrypt(held[0])

    def remember(self, connection: GarminConnection, tokens: str) -> str | None:
        """Store refreshed tokens; returns ciphertext for the row, or None.

        A refresh never extends a visit's expiry: the person agreed to a
        window, not to a sliding one.
        """
        if _retention(connection) == PERSISTENT:
            return self._vault.encrypt(tokens)
        with self._lock:
            held = self._held.get(connection.user_id)
            if held is not None:
                self._held[connection.user_id] = (self._vault.encrypt(tokens), held[1])
        return None

    def forget(self, user_id: str) -> None:
        with self._lock:
            self._held.pop(user_id, None)

    def expires_at(self, connection: GarminConnection) -> datetime | None:
        if _retention(connection) == PERSISTENT:
            return None
        with self._lock:
            held = self._held.get(connection.user_id)
        return held[1] if held else connection.visit_expires_at

    def live(self, connection: GarminConnection) -> bool:
        """Whether this connection can still reach Garmin right now."""
        if _retention(connection) == PERSISTENT:
            return connection.status == "connected"
        with self._lock:
            held = self._held.get(connection.user_id)
        return connection.status == "connected" and held is not None and held[1] > now()

    def sweep(self) -> int:
        """Drop expired holdings; returns how many went."""
        moment = now()
        with self._lock:
            expired = [user for user, (_, until) in self._held.items() if until <= moment]
            for user in expired:
                del self._held[user]
        return len(expired)


def _retention(connection: GarminConnection) -> str:
    return connection.retention or PERSISTENT
