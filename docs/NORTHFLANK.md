# Render → Northflank migration

Status: prepared, not deployed. Keep Render available for rollback. This moves
the application only; **keep the existing PostgreSQL database and encryption
key**. No account/database export, new database, or key rotation is required.

## 1. Account and free-plan gate

Create/sign in to Northflank, select **Developer Sandbox**, and connect GitHub
with access to `ColinPlatt/workout-relay`. Northflank requires a payment method
for verification even on the free plan. Confirm the selected service and any
extras show no charge before creating them. Do not select a paid compute size,
BYOC, extra volumes, or additional resources to get past a free-tier limit.

Northflank advertises no inactivity sleep for Sandbox, but describes it as a
hobby/testing tier rather than production hosting. This is not an uptime SLA.

## 2. Build configuration

Use the managed Northflank cloud, preferably an available EU region near the
existing database. Create a **Combined service** named `workout-relay`:

| Setting | Value |
| --- | --- |
| Repository | `ColinPlatt/workout-relay` |
| Branch/revision | The agreed, committed release; check CI before deployment |
| Build | Dockerfile `/Dockerfile`, context `/` |
| Runtime command | Keep the image default (one Uvicorn worker) |
| Instances | 1; no autoscaling or scale-to-zero |
| Compute | The included free Sandbox allocation; verify in the dashboard |
| Public port | HTTP, internal port `8000`, name `web` |
| Persistent volumes | None; application data stays in existing PostgreSQL |
| Continuous delivery | Disable during migration; deploy an explicit tested revision |

The dashboard creates a public HTTPS `code.run` hostname for the port. Set
`BASE_URL` to that complete origin (or the custom domain you intend to retain).
Do not use the old Render URL as the new issuer. A first boot without a correct
HTTPS `BASE_URL` deliberately fails production validation; set it and redeploy.
Do not weaken that check or temporarily use production credentials in mock mode.

Northflank normally starts combined services immediately. Configure runtime
secrets/settings before letting the application connect to the live database.
If the UI cannot provide the final hostname until creation, create without the
production secrets first: the application must stay unready until configuration
is complete. Do not point a throwaway preview at the live database.

## 3. Runtime variables (not build arguments)

Copy these two secret values **privately from the current Render environment**
into Northflank's runtime secret settings:

- `DATABASE_URL`: the exact existing **direct/unpooled** PostgreSQL URL.
- `MASTER_ENCRYPTION_KEY`: the exact existing key. Never generate a replacement;
  existing Garmin connections are encrypted with it.

Do not paste secrets into chat, a JSON template, Git, build arguments or logs.

Set the following non-secret runtime variables:

```dotenv
APP_ENV=production
GARMIN_MODE=live
COOKIE_SECURE=true
CONNECTOR_ENABLED=true
MCP_PROBE_ENABLED=false
PORT=8000
BASE_URL=https://YOUR-ACTUAL-NORTHFLANK-HOSTNAME
```

Also copy the existing `SESSION_DAYS`, `PLAN_RETENTION_DAYS`,
`GARMIN_VISIT_MINUTES`, and `MAX_PLAN_BYTES` values, including defaults in use.
Keep the user's current retention choices unchanged. Do not copy
`RENDER_EXTERNAL_URL`.

The application honours `X-Forwarded-Proto: https` itself, upgrading the scheme
it believes it is serving but never downgrading, so redirects stay on HTTPS
without trusting a proxy address. That is independent of the note below.

Do not blindly set `FORWARDED_ALLOW_IPS=*`. Verify Northflank's proxy network
before trusting forwarded client-IP headers. Confirm login rate limits see
distinct client addresses; proxy misconfiguration may group all users together.

## 4. Health and shutdown

Configure HTTP readiness on port `8000`, path `/readyz`, initial delay 10s,
interval 15s, timeout 5s, failure threshold 3, success threshold 1. This checks
database connectivity. Use `/healthz` for liveness (initial delay 30s, interval
30s, timeout 5s, failure threshold 3), not `/readyz`: a database outage should
remove traffic without constantly restarting the app and losing temporary logins.

If shutdown grace is configurable, allow at least 30 seconds: the application
drains Garmin work for up to 25 seconds. Confirm the platform's actual behaviour.
Forced interruption recovery is journalled, but unfinished MFA and visit-only
Garmin sessions are memory-only and cannot be migrated or survive restarts.

## 5. Validate and switch

1. Confirm the deployed revision. Local uncommitted UI changes do not appear in
   Git-based deployments; do not silently mix them into a migration release.
2. Record the old deployment settings and confirm the database's backup policy.
3. Choose a quiet cutover window; let active uploads finish and ask people to
   finish MFA. Startup can resume queued uploads as soon as the new app connects
   to the database. The shared advisory lock prevents concurrent upload workers,
   but does not make browser/MFA memory portable between the two hosts.
4. Deploy Northflank using the retained database/key. During the brief overlap,
   avoid changing Garmin connections or starting new sessions on the old host.
5. Run the read-only smoke check from the repository's virtual environment:

   ```bash
   python -m workout_relay.deployment_check https://YOUR-ACTUAL-NORTHFLANK-HOSTNAME
   ```

   This checks readiness, live-mode configuration, homepage, HTTPS OAuth issuer,
   supported scopes and protected-resource discovery. It sends no credentials,
   does not register an OAuth client, and makes no Garmin calls. It cannot prove
   that the correct database or encryption key was installed.
6. Sign in through the new website with an existing account. Confirm history,
   connection status, mobile EN/FR UI, and the chosen persistence setting. If
   migrating to a new hostname, browser sessions do not transfer; users sign in
   again. Re-add/re-authorise assistant connectors at the new `/mcp/` URL, with
   fresh activity consent where needed. Do not redirect token POST requests to
   another origin as a shortcut.
7. Separately approve a real test-account exercise before reading real activities
   or sending a test workout. Readiness alone does not validate Garmin access
   from Northflank's outbound IPs.
8. Once checks pass, switch the public/custom-domain entry point, disable Render
   auto-deploy and suspend its service. Keep its configuration for rollback;
   **do not delete its environment secrets or the database**. Do not leave two
   hosts serving normal traffic indefinitely.

An always-on application does not imply a database can never idle or restart.
The current database provider's free-tier limits still apply.

## Rollback

Stop routing to Northflank and pause its service before resuming normal traffic
on Render. Resume the old service with its original URL and the same database
and key. Changes made after cutover remain in that shared database. Users may
need another login/connector setup if the hostname changes back. Do not restore
an old database snapshot merely to roll back the web host.

## References (checked 2026-09-21)

- [Free tier and payment verification](https://northflank.com/docs/v1/application/billing/pricing-on-northflank)
- [Always-on Sandbox offering](https://northflank.com/pricing)
- [Combined Docker services and deployment controls](https://northflank.com/docs/v1/application/getting-started/build-and-deploy-your-code)
- [Health checks](https://northflank.com/docs/v1/application/observe/configure-health-checks)
