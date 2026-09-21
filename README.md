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
3. In **Plans**, choose a JSON file from the phone or expand **Or paste a plan**.
   Tap **Send to Garmin**; validation runs before anything is uploaded. Use
   **Check plan** for an optional preview of any errors.
4. The request is safely queued. The page may be closed; **Plan history** shows
   whether it completed or failed when the user returns.
5. Under **Assistants**, expand **Connection details** to copy the connector
   address and approve access. For a manual chat, expand **Help my assistant
   create a plan** on the Plans screen and copy the instructions into the chat.
   API keys remain available under **Advanced: API access**; never paste a key
   into a chat. The Garmin password is never shared with the assistant.

Format rules, the example and JSON Schema remain available through the API
and MCP tool metadata. The website links to these machine-readable descriptions
in its head rather than displaying an entire technical manual to users.

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

## Getting set up

Until Garmin is connected, the dashboard shows a guided panel: which sign-in
method works (an account using "Sign in with Apple" or Google needs a password
set first, the most common reason connecting fails), what happens with an MFA
code, the retention choice, and where the workout appears afterwards. The
Assistants tab does the same for connectors, marking Claude or ChatGPT as
connected once one of them is, and hiding the walkthrough once anything is.

## Privacy

Garmin credentials are never stored. When connecting Garmin the person chooses
whether the session may be kept: **this visit only**, held in memory with a
server-enforced expiry and never written to the database, or **keep connected**,
encrypted and stored until they disconnect. Cookies are limited to a session and
a CSRF cookie, both strictly necessary, so there is no consent banner to click
through. See [PRIVACY.md](docs/PRIVACY.md).

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
then validate and send plans, or read completed activities with separately
approved `activities:read` permission. No key is shared, and Garmin credentials stay on
the server. Connected assistants are listed, with one-tap revocation, under
**Assistants**.

The optional MCP file-delivery probe is documented in
[MCP_PROBE.md](docs/MCP_PROBE.md). It is off by default, serves synthetic
sample files only, and exists to test whether assistants can read delivered
files; completed-activity reading currently returns JSON metrics, not files.

### Completed activities / Activités terminées

- MCP: `list_activities(limit=5, start=0, sport="running")`, then
  `get_activity(activity_id="…")` using an ID from that account's list.
- REST: `GET /api/v1/activities?limit=5&sport=running` and
  `GET /api/v1/activities/{activity_id}`. OAuth tokens and explicitly scoped
  API keys require `activities:read`; existing keys/grants are not upgraded.
- Results include distance, duration, pace, heart rate, elevation, power and
  training effect where available. Units are in field names; missing values
  are `null`. A detailed read also returns lap splits and, when `samples` is
  given, the sample-by-sample series Garmin records (heart rate, pace,
  elevation, cadence, power, temperature) up to 1000 points. No GPS tracks and
  no FIT/TCX files are served: the route is never fetched, and parsed JSON is
  the only delivery that works on every assistant.
- Existing assistant connections need fresh consent: remove and re-add the
  connector, sign in, and approve activity access. A token refresh cannot add
  permission. Revoke the old connection under **Assistants** if still listed.

Les connexions existantes ne reçoivent aucune autorisation supplémentaire.
Supprimez puis ajoutez à nouveau le connecteur, connectez-vous et approuvez
`activities:read`. L'assistant peut ensuite lire vos activités terminées et vos
mesures de course/santé, dont la fréquence cardiaque, sans traces GPS. Les
valeurs absentes sont `null` ; les unités figurent dans les noms des champs.
Les clés API nécessitent également un accord explicite lors de leur création.

See [SECURITY.md](docs/SECURITY.md), [PLAN_FORMAT.md](docs/PLAN_FORMAT.md), and
[DEPLOYMENT.md](docs/DEPLOYMENT.md) before enabling live Garmin authentication.
The included `render.yaml` provides a phone-friendly Blueprint deployment on
Render's free tier, with the database hosted separately; reviewing it does not
provision or bill any resources.
