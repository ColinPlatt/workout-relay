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
  "id": "week40-easy",
  "date": "2026-10-01",
  "sport": "running",
  "title": "Easy aerobic run",
  "description": "Optional Garmin description",
  "estimated_duration_sec": 2700,
  "steps": []
}
```

The ID must be unique across your account and stable when the date changes.
Use 2–160 lowercase letters, digits, underscores or hyphens, starting with a
letter or digit. Existing date-prefixed IDs remain valid: do not rename them
when rescheduling. A new ID creates a different workout. Titles are limited to
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

## Strength workouts

Set `sport` to `strength`. The workout fields are the same; only the steps
differ. Step types are `warmup`, `interval`, `cooldown` and `rest`, durations
are `reps` or `time`, and there are no pace or heart-rate targets.

```json
{
  "id": "week40-lower",
  "date": "2026-10-02",
  "sport": "strength",
  "title": "Lower body",
  "steps": [
    {
      "type": "warmup",
      "exercise": "PLANK",
      "duration": { "type": "time", "value": 60 }
    },
    {
      "type": "repeat",
      "count": 3,
      "steps": [
        {
          "type": "interval",
          "exercise": "ROMANIAN_DEADLIFT",
          "duration": { "type": "reps", "value": 10 },
          "weight_kg": 8,
          "description": "Hinge at the hip, soft knees."
        },
        { "type": "rest", "duration": { "type": "time", "value": 60 } }
      ]
    }
  ]
}
```

Sets are a `repeat` containing the exercise followed by a `rest` step, and a
repeat may hold up to 12 steps here. `weight_kg` is kilograms. A `rest` step
names no exercise, carries no weight and must be timed.

### Naming an exercise

`exercise` must be a name Garmin knows — there are 1,531 of them in 47
categories. Search with the `find_exercises` tool, or `GET /api/v1/exercises`;
never invent a name.

The category is derived, so a plan does not give one. That is deliberate: the
category cannot be guessed from the name (`KETTLEBELL_SWING` is filed under
`HIP_RAISE`, `DEAD_BUG` under `HIP_STABILITY`), and guessing it is what
previously filed exercises wrongly.

Give `category` in two cases only:

- To pick an equipment variant. A bare `SIDE_PLANK` means the ordinary one;
  `"category": "SUSPENSION"` asks for the suspension-trainer version.
- When validation says a name is ambiguous. Only `CHEST_PRESS` and
  `GLUTE_BRIDGE` are, and the error lists the categories to choose from.

Names may be written naturally: `romanian deadlift` and `Romanian-Deadlift`
both reach `ROMANIAN_DEADLIFT`. An unrecognised name is refused with the
closest real exercises and their categories, which should be used rather than
another invented name.

### When nothing fits

Garmin also allows a step that names only a category, which the watch shows as
the category itself. It is accepted here, but it tells the person less than a
named movement, so it needs a written `reason` and no `exercise`:

```json
{
  "type": "interval",
  "category": "CARRY",
  "reason": "Suitcase carry with one kettlebell; no listed carry matches",
  "duration": { "type": "time", "value": 40 }
}
```

The plan then validates with a **warning** rather than an error, naming the
specific exercises it passed over. Ask the person before using it.

## Assistant API workflow

Use `Authorization: Bearer <Workout Relay API key>` on every private endpoint.

1. Fetch `GET /api/v1/plan-format?language=en`, `GET /api/v1/plan-schema`, and
   `GET /api/v1/plan-example`.
2. Send raw JSON to `POST /api/v1/plans/validate` and repair every reported
   error.
3. Send the valid object to `POST /api/v1/plans`. A successful request returns
   HTTP `202` with a submission `id`; it does not mean Garmin has finished.
4. Poll `GET /api/v1/plans/{id}` until `status` is `completed` or `failed`.
   On completion, inspect `result.counts` and the per-workout actions.

Reusing a workout `id` is intentional: unchanged workouts are skipped and
changed workouts are updated rather than duplicated.
