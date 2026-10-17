# Neon → Supabase database migration

Status: prepared, not cut over. This moves the database only. The web host,
`MASTER_ENCRYPTION_KEY` and every other setting stay as they are. Keep the Neon
project intact until the checks at the end pass; it is the rollback.

**Do not rotate, regenerate or retype `MASTER_ENCRYPTION_KEY`.** Stored Garmin
tokens are encrypted with it. The dump carries ciphertext only, so a different
key on the new database makes every Garmin connection unreadable.

## Database requirements

The production database must be PostgreSQL-compatible and support
session-scoped PostgreSQL advisory locks (`pg_try_advisory_lock`,
`pg_advisory_unlock`, `pg_advisory_xact_lock`, `pg_backend_pid`). One dedicated
connection holds the upload lock for a whole submission, so the server session
behind it must not change. Use a direct PostgreSQL connection or a session-mode
pooler. Transaction-mode poolers are unsupported: they hand each transaction a
different server session, and two uploaders could run at once without any
error. Startup refuses the pooled URLs it recognises (Neon `-pooler` hosts,
PgBouncer hosts and Supabase's port 6543).

For Supabase:

| Connection | Host:port | Status |
| --- | --- | --- |
| Session pooler (Supavisor, session mode) | `aws-…​.pooler.supabase.com:5432` | **Recommended.** IPv4, so it works from Render and Northflank. |
| Direct connection | `db.<project-ref>.supabase.co:5432` | Supported where the host has IPv6 egress (Free projects have no IPv4 here). |
| Transaction pooler | `…​.pooler.supabase.com:6543` or `db.<project-ref>.supabase.co:6543` | **Unsupported.** Refused at startup. |

Supabase Free limits that matter here:

- A project with too little database activity over 7 days is paused. Supabase
  emails a warning first; opening the project dashboard or normal app use
  prevents it. A paused project can be resumed from the dashboard for 90 days,
  with data intact; after that it cannot. The always-on Northflank service's
  startup and worker queries help, but a week with no sign-ins or uploads may
  still count as low activity; watch for the warning email.
- 500 MB of database space, and at most two active Free projects.
- No downloadable backups on Free. Keep your own encrypted `pg_dump` if you
  want a restorable copy.

## 1. Project and hardening

The project `workout-relay` exists in eu-central-1 (Frankfurt), on the Free plan.

Supabase exposes the `public` schema through its Data API, and by default
grants the `anon` and `authenticated` API roles full access to every new table
there. This application never uses the Data API, so its tables must not be
reachable through it. The project already has this migration applied
(`close_data_api_to_relay_tables`); a new project needs it before the restore:

```sql
alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated, service_role;
alter default privileges for role postgres in schema public
  revoke all on sequences from anon, authenticated, service_role;
alter default privileges for role postgres in schema public
  revoke all on functions from anon, authenticated, service_role;
revoke all on all tables in schema public from anon, authenticated, service_role;
revoke all on all sequences in schema public from anon, authenticated, service_role;
```

This covers tables created later by the application's startup too, because it
connects as `postgres`. Optionally also turn off the Data API under
**Project Settings → Data API**.

Set a database password under **Project Settings → Database → Reset database
password**. Store it in a password manager only. Then open **Connect** in the
dashboard and copy the **Session pooler** URI. It looks like:

```text
postgresql://postgres.<project-ref>:<PASSWORD>@aws-<n>-eu-central-1.pooler.supabase.com:5432/postgres
```

Percent-encode any special characters in the password, and append
`?sslmode=require`.

## 2. Cutover

Pick a quiet window, then follow these steps in order. They stop writes before
the dump, so nothing written to Neon afterwards is lost.

1. Let active uploads finish, then **pause** the Northflank service (or scale
   it to 0 instances) so nothing writes to Neon. Do not delete it. Confirm the
   old Render service is still suspended too.
2. Dump and restore with a client whose major version is at least the Neon
   server's (check with `select version();` in Neon's SQL editor); `pg_dump`
   refuses an older client. The commands below use the `postgres:18` image,
   which also avoids a local install. Neon defaults to 18 while Supabase runs
   17; restoring this application's 18 dump into 17 was rehearsed and gives an
   identical schema, since it uses no 18-only features. Secrets are read from
   the terminal, not typed into the command line, so they stay out of shell
   history:

   ```bash
   read -rs -p 'Neon DIRECT (unpooled) URL: ' NEON_URL; echo
   read -rs -p 'Supabase session pooler URL: ' SUPABASE_URL; echo
   export NEON_URL SUPABASE_URL
   WORK=$(mktemp -d)   # outside the repository; the dump holds account data

   docker run --rm -e NEON_URL -e SUPABASE_URL -v "$WORK":/work -w /work \
     postgres:18 bash -euo pipefail -c '
       pg_dump --format=custom --no-owner --no-privileges --schema=public \
         --file=relay.dump "$NEON_URL"
       # Supabase already has the public schema, and postgres does not own it.
       pg_restore --list relay.dump \
         | grep -vE " SCHEMA - public | COMMENT - SCHEMA public " > relay.list
       pg_restore --no-owner --no-privileges --single-transaction \
         --exit-on-error --use-list=relay.list --dbname="$SUPABASE_URL" relay.dump
     '
   ```

   `--single-transaction --exit-on-error` makes the restore all or nothing. If
   it fails, the Supabase database is unchanged; fix the cause and rerun.
   Restore into an empty database only: do not start the application against
   Supabase first, or its startup creates empty tables that collide with the
   restore.

3. Compare exact row counts on both sides. Every line must match:

   ```bash
   cat > "$WORK/counts.sql" <<'SQL'
   select table_name, (xpath('/row/c/text()', query_to_xml(
            format('select count(*) as c from public.%I', table_name),
            false, true, '')))[1]::text
   from information_schema.tables
   where table_schema = 'public' and table_type = 'BASE TABLE'
   order by 1;
   SQL
   for DB in NEON_URL SUPABASE_URL; do
     docker run --rm -e "$DB" -v "$WORK":/work postgres:18 \
       sh -c "psql -qAt \"\$$DB\" -f /work/counts.sql" > "$WORK/$DB.txt"
   done
   diff "$WORK/NEON_URL.txt" "$WORK/SUPABASE_URL.txt" && echo counts match
   ```

4. Check that the API roles cannot read the restored tables. This must print
   nothing:

   ```bash
   cat > "$WORK/api_grants.sql" <<'SQL'
   select c.relname, r.rolname
   from pg_class c cross join (values ('anon'), ('authenticated')) r(rolname)
   where c.relnamespace = 'public'::regnamespace and c.relkind = 'r'
     and has_table_privilege(r.rolname, c.oid, 'SELECT');
   SQL
   docker run --rm -e SUPABASE_URL -v "$WORK":/work postgres:18 \
     sh -c 'psql -qAt "$SUPABASE_URL" -f /work/api_grants.sql'
   ```

5. In the Northflank service's runtime secrets, replace `DATABASE_URL` with
   the Supabase session pooler URL. **Leave `MASTER_ENCRYPTION_KEY`
   untouched.** Resume the service (or scale it back to 1 instance).
6. Delete the working copy: `rm -rf "$WORK"; unset NEON_URL SUPABASE_URL`.

## 3. Verify

1. `/readyz` passes, then run the read-only smoke check:

   ```bash
   python -m workout_relay.deployment_check https://YOUR-SERVICE-HOSTNAME
   ```

2. Sign in with an existing account. Browser sessions moved with the data, so
   one still within `SESSION_DAYS` stays signed in.
3. The Garmin connection shows as connected. This proves the key is unchanged:
   with a different key the stored tokens cannot be decrypted.
4. Workout history and stored submissions are present.
5. Assistant connectors still work without reconnecting (OAuth clients and
   token hashes are in the database), including an MCP `get_garmin_status`
   call.
6. Submit one harmless workout and confirm it reaches Garmin.
7. Upload locking: with the service running, this should show one advisory
   lock held by the application while an upload is in progress:

   ```sql
   select pid, granted from pg_locks
   where locktype = 'advisory' and objid = 78234601;
   ```

When all of this passes, keep Neon for a week or so as a rollback, then delete
it. Delete any local dump at the same time.

## Rollback

Pause the Northflank service, set `DATABASE_URL` back to the Neon direct URL,
and resume. Anything written to Supabase after cutover is not in Neon; dump and
restore in the opposite direction if that matters.

## References (checked 2026-10-17)

- [Connect to your database](https://supabase.com/docs/guides/database/connecting-to-postgres)
- [Free project pausing](https://supabase.com/docs/guides/platform/free-project-pausing)
