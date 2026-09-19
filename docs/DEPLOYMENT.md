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
application process runs a database-backed upload worker, recovers interrupted
`processing` jobs on restart, and lets mobile clients poll job status. Before
horizontal scaling, replace the single-process claim with a transactional
`FOR UPDATE SKIP LOCKED` worker or a managed job queue.

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
