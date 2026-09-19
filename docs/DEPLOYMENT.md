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

The in-process five-minute MFA state requires one application process. Before
horizontal scaling, extract Garmin login into a dedicated sticky auth broker;
do not serialize unfinished HTTP sessions into Postgres or Redis.

Plan submissions are persisted as `queued` before the API responds. The single
application process runs a database-backed upload worker, recovers interrupted
`processing` jobs on restart, and lets mobile clients poll job status. Before
horizontal scaling, replace the single-process claim with a transactional
`FOR UPDATE SKIP LOCKED` worker or a managed job queue.

This repository does not claim compatibility with a particular public hosting
vendor. The included image accepts any PostgreSQL SQLAlchemy URL.
