# Workout Relay plan format — English

Workout Relay accepts strict JSON. There is no AI interpretation during upload:
the same valid plan always produces the same Garmin workout structure.

## Top level

```json
{
  "schema_version": 1,
  "plan_id": "2026-W40-5k",
  "title": "5K development week",
  "description": "Optional notes",
  "workouts": []
}
```

- `schema_version` is always `1`.
- `plan_id` is a stable 3–100 character identifier containing letters, digits,
  `_`, and `-`.
- A plan contains 1–31 workouts.
- Unknown fields are rejected.

## Workout

```json
{
  "id": "2026-10-01_easy",
  "date": "2026-10-01",
  "sport": "running",
  "title": "Easy aerobic run",
  "description": "Optional Garmin description",
  "estimated_duration_sec": 2700,
  "steps": []
}
```

The ID must be unique and start with the workout's date. Titles are limited to
50 characters and descriptions to 500 characters.

## Simple steps

Step types are `warmup`, `interval`, `recovery`, and `cooldown`.

```json
{
  "type": "interval",
  "name": "Easy",
  "duration": { "type": "time", "value": 600 },
  "target": { "type": "heart_rate", "low": 135, "high": 150 }
}
```

- Time is expressed in seconds.
- Distance is expressed in metres.
- Heart-rate values are bpm and `low < high`.
- Pace is `M:SS` per kilometre: `{ "type": "pace", "slow": "5:30", "fast": "5:10" }`.
- Use `{ "type": "no_target" }` when Garmin should not alert for a target.

## Repeats

```json
{
  "type": "repeat",
  "count": 6,
  "steps": [
    {
      "type": "interval",
      "duration": { "type": "distance", "value": 400 },
      "target": { "type": "pace", "slow": "4:30", "fast": "4:15" }
    },
    {
      "type": "recovery",
      "duration": { "type": "time", "value": 90 },
      "target": { "type": "no_target" }
    }
  ]
}
```

Repeats contain 1–5 simple steps and cannot be nested. A workout may contain no
more than 50 steps after all repeats are expanded.

Always validate generated JSON before submission. Validation errors include a
stable code, JSON path, and parameters so an assistant can repair the plan.
