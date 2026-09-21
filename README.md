# Workout Relay

Mobile-first, bilingual workout-plan delivery for Garmin Connect. Users connect
their Garmin account once, then upload a strict JSON training plan from a phone
or submit it through a revocable API key.

> This project uses Garmin's undocumented Connect endpoints. It is not
> affiliated with or endorsed by Garmin. Garmin passwords and MFA codes are
> processed transiently and never stored; encrypted session tokens are retained
> so uploads can run without repeated logins.

## Phone-only user path

1. Open the HTTPS website in Safari or Chrome and create a Workout Relay
   account. The interface follows the browser's French/English preference and
   also has a permanent language selector.
2. Tap the Garmin status card, read the trust notice, and enter the Garmin
   login once. If Garmin asks for MFA, enter the code on the same page.
3. In **Upload**, choose a JSON file from the phone or paste its contents. Tap
   **Validate**, correct any field-specific errors, then tap **Send to Garmin**.
4. The request is safely queued. The page may be closed; **Plan history** shows
   whether it completed or failed when the user returns.
5. For ChatGPT, Claude, or another automation, create a revocable key under
   **Automation**, copy the bilingual AI instructions, and configure the key in
   the assistant's protected API-auth/secret setting—not in an ordinary chat.
   The Garmin password is never shared with the assistant.

The **Plan format** tab includes a complete example, JSON Schema, units, target
rules, repeat limits, and copy-ready assistant instructions.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn workout_relay.app:app --reload
```

Open <http://localhost:8000>. The app selects French or English from the browser
language and provides a persistent language override.

Use `GARMIN_MODE=mock` for local development. Real Garmin authentication is only
enabled with `GARMIN_MODE=live`.

## Tests

```bash
pytest -q
```

## Deployment

```bash
docker compose up --build
```

Assistants connect through the MCP connector at `/mcp/`: the person adds that
address in Claude or ChatGPT, signs in once and approves, and the assistant can
then validate and send plans. No key is shared, and Garmin credentials stay on
the server. Connected assistants are listed, with one-tap revocation, under
**Automation**.

The optional MCP file-delivery probe is documented in
[MCP_PROBE.md](docs/MCP_PROBE.md). It is off by default, serves synthetic
sample files only, and exists to test whether assistants can read delivered
files before the activity-retrieval feature is built.

See [SECURITY.md](docs/SECURITY.md), [PLAN_FORMAT.md](docs/PLAN_FORMAT.md), and
[DEPLOYMENT.md](docs/DEPLOYMENT.md) before enabling live Garmin authentication.
The included `render.yaml` provides a phone-friendly Blueprint deployment on
Render's free tier, with the database hosted separately; reviewing it does not
provision or bill any resources.
