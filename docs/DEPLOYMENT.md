# Deployment

## Development or private demonstration

Use `docker compose up --build`. It runs in `GARMIN_MODE=mock` and stores SQLite
data on a named volume. No Garmin requests are made.

## Live deployment checklist

1. Use managed Postgres and a single web process for the initial release.
2. Set `APP_ENV=production`, `GARMIN_MODE=live`, an HTTPS `BASE_URL`, and
   `COOKIE_SECURE=true`.
3. Generate a Fernet `MASTER_ENCRYPTION_KEY` and store it only in the hosting
   platform's secret manager.
4. Disable request-body capture for Garmin connection routes at every layer.
5. Put a shared rate limiter in front of login and Garmin connection routes.
6. Encrypt database backups and test account/token deletion and restore policy.
7. Keep concurrency low because cloud-origin Garmin requests may be rate-limited.
8. Monitor redacted error codes, never credentials, MFA codes, plan contents, or
   token ciphertext/plaintext.
9. Configure the platform health check to call `/readyz`.

The in-process five-minute MFA state requires one application process. Before
horizontal scaling, extract Garmin login into a dedicated sticky auth broker;
do not serialize unfinished HTTP sessions into Postgres or Redis.

Plan submissions are persisted as `queued` before the API responds. The single
application process runs a database-backed upload worker and lets mobile clients
poll job status. A dedicated PostgreSQL connection holds a session advisory lock
throughout each submission (SQLite development uses a file lock). Only its owner
can recover interrupted `processing` jobs. Overlapping Render releases therefore
cannot upload concurrently. Use a direct PostgreSQL connection, not a
transaction-pooling proxy. Shutdown drains the active thread before releasing
ownership; after a forced kill the next owner recovers from the operation journal.

The additive `workout_operations` table is created automatically. It stores
write intent, the Garmin IDs returned so far, and progress, separately from
expiring plan history. Remote identity (workout ID, calendar entry ID and
scheduled date) is journalled independently of the payload, so a changed plan
can resume an interrupted update: the update `PUT` and the unschedule `DELETE`
both repeat safely. Ambiguous creation and scheduling stay blocked until they
are reconciled, and journals from before this release, which carry no identity,
keep the conservative behaviour.

Retries reconcile an ambiguous creation using a unique marker placed in the
Garmin workout description, and an ambiguous scheduling using the Garmin
calendar. Reconciliation is capped at ten requests per attempt (two pages of 25
workouts and eight detail lookups) with a persisted 60-second cooldown between
attempts, and stops immediately on a rate limit or connection failure. Listings
ask Garmin for newest-first ordering; that ordering is unverified, and the code
falls back to the default order only when Garmin rejects the parameters. If the
result cannot be identified uniquely, the job fails with
`garmin_outcome_unknown`; it never repeats the POST. Resubmit the exact same
workout to check again, and keep workout IDs unchanged. There is no automatic
reset of uncertain operations: an operator must investigate persistent cases
before any repair. This is not an exactly-once guarantee from Garmin's
unofficial API.

Once a created workout and its link are committed, the marker is removed with
the same repeatable `PUT` the update path uses. Cleanup is recorded separately
from upload completion: a failure never turns a successful upload into a failed
one and never repeats a creation or scheduling request; the next submission for
that workout retries it. Users should not remove recovery markers from
unfinished workouts.

Shutdown drains the active Garmin thread before releasing ownership, bounded by
a 45-second deadline inside Render's 60-second window. Past the deadline a
forced kill is safe: the journal survives, and PostgreSQL releases the advisory
lock when the connection dies. Uploads need PostgreSQL or file-backed SQLite on
a Unix host; on other platforms, run the Docker image or point `DATABASE_URL` at
PostgreSQL.

For the first upgrade from a release without locking/journaling, stop new
submissions, let active uploads finish, and stop the old process before starting
this release. Already-failed uploads from the old release have no journal and
must be checked in Garmin manually before retrying. Later releases can overlap
normally. In-flight MFA attempts remain in memory and may need restarting after
a deployment.

Completed and failed submissions older than `PLAN_RETENTION_DAYS` and expired
browser sessions are removed at application startup. Backups may retain older
encrypted copies according to the hosting provider's backup policy.

Uvicorn accepts forwarded headers only from its default trusted proxy address.
If the hosting platform uses a different private proxy range, configure
`FORWARDED_ALLOW_IPS` explicitly; never use `*` on an internet-reachable
container.

The Docker image remains portable and accepts any PostgreSQL SQLAlchemy URL;
the prepared configuration below is the supported first deployment path.

## Prepared Render deployment

`render.yaml` defines a single Docker web process and a private paid Postgres
database in Frankfurt. Render supplies HTTPS and `RENDER_EXTERNAL_URL`, creates
the database connection string, and generates the encryption key without
committing it. The selected entry-level paid resources are intentional: the
free Postgres tier expires and can delete account and Garmin-token data.
The blueprint explicitly allocates 1 GB of database storage. Compute and storage
are billed separately; review the displayed total. Existing databases cannot be
shrunk: retain their current allocation in the blueprint if already above 1 GB.
See the [Render database field reference](https://render.com/docs/blueprint-spec#database-fields).

From a phone browser after this repository is on GitHub:

1. Sign in to Render with GitHub and open **New → Blueprint**.
2. Select the private `workout-relay` repository and approve the two declared
   resources and their displayed monthly price.
3. Wait for `/readyz` to pass, open the generated HTTPS URL, and create a
   disposable account first.
4. Test mock-independent account and JSON validation behavior, then connect a
   real Garmin account through the website and submit one harmless workout.
5. Delete the disposable account and confirm its Garmin tokens disappear.

Do not rotate or delete `MASTER_ENCRYPTION_KEY` while encrypted Garmin tokens
exist. Losing that key requires every user to reconnect Garmin.
