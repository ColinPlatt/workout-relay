# Workout Relay

Mobile-first, bilingual workout-plan delivery for Garmin Connect. Users connect
their Garmin account once, then upload a strict JSON training plan from a phone
or submit it through a revocable API key.

> This project uses Garmin's undocumented Connect endpoints. It is not
> affiliated with or endorsed by Garmin. Garmin passwords and MFA codes are
> processed transiently and never stored; encrypted session tokens are retained
> so uploads can run without repeated logins.

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

See [SECURITY.md](docs/SECURITY.md), [PLAN_FORMAT.md](docs/PLAN_FORMAT.md), and
[DEPLOYMENT.md](docs/DEPLOYMENT.md) before enabling live Garmin authentication.
