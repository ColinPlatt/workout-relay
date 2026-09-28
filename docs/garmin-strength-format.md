# Garmin's strength workout format, as observed

Every constant here was read back from a real strength workout created by hand in
Garmin Connect (workout `1712798046`, 28 September 2026) via
`GET /workout-service/workout/{id}`. The unredacted response is vendored at
`tests/fixtures/garmin_strength_workout.json` (account identifiers scrubbed; the
step shape is untouched). Nothing in this document was inferred from the
library, the community spreadsheet, or a guess — if a value is not listed here,
it has not been seen, and code must not assume it.

## Sport

```json
"sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 4},
"subSportType": "GENERIC"
```

`garminconnect`'s `SportType` enum stops at `OTHER = 8` and has no strength
entry, so this id is supplied directly. The same `sportType` object appears on
the workout and on each `workoutSegments` entry.

## Step types

| key | `stepTypeId` | used for |
|---|---|---|
| `warmup` | 1 | opening step |
| `interval` | 3 | every working exercise |
| `rest` | 5 | rest between exercises and between sets |
| `repeat` | 6 | `RepeatGroupDTO`, i.e. sets |

## End conditions

| key | `conditionTypeId` | value |
|---|---|---|
| `time` | 2 | seconds, as a float |
| `iterations` | 7 | repeat count, on the `RepeatGroupDTO` |
| `reps` | 10 | repetitions, as a float |

`displayable` is `true` for `time` and `reps`, `false` for `iterations`.

`preferredEndConditionUnit` is noise on strength steps: the UI wrote
`{"unitId": 2, "unitKey": "kilometer", "factor": 100000.0}` on some rep steps and
`null` on others, with no behavioural difference. We always send `null`.

## The exercise, on the step itself

`category` and `exerciseName` are plain top-level fields on `ExecutableStepDTO`
(which is why `ExecutableStep` needing `extra="allow"` mattered):

```json
"category": "DEADLIFT", "exerciseName": "ROMANIAN_DEADLIFT"
```

Three observed forms, all valid:

- **Specific** — `category` plus a matching `exerciseName`
  (`HIP_RAISE`/`KETTLEBELL_SWING`, `HIP_STABILITY`/`DEAD_BUG`).
- **Generic** — `category` with `exerciseName: ""` (empty string, *not* null):
  `WARM_UP`, `LEG_RAISE`, `SHOULDER_PRESS`, `CARRY`, `TOTAL_BODY` all appeared
  this way. This is Garmin's own "somewhere in this category" step, and it is
  exactly the fallback the plan calls allowed-but-discouraged.
- **Neither** — `rest` steps carry `category: null` and `exerciseName: null`.

Names that begin with a digit are prefixed with an underscore:
`_90_DEGREE_STATIC_HOLD` under `PLANK`.

## Weight

```json
"weightValue": 6.0,
"weightUnit": {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}
```

`weightValue` is **kilograms directly**, not grams — the 6 kg kettlebell reads
`6.0` and the 8 kg Romanian deadlift reads `8.0`. The `factor: 1000.0` is unit
metadata, not a multiplier to apply. Bodyweight and banded steps carried either
`null` or `0.0`. `weightUnit` was present on every executable step including
rests, so it is always sent.

## Repeat groups

```json
{"type": "RepeatGroupDTO", "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat"},
 "numberOfIterations": 3, "endConditionValue": 3.0,
 "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations"},
 "skipLastRestStep": false, "smartRepeat": false, "childStepId": 1,
 "workoutSteps": [...]}
```

`childStepId` numbers the groups from 1, and every child step repeats its
parent's `childStepId`. `stepOrder` is a single flat counter across the whole
workout, children included. `numberOfIterations` and `endConditionValue` carry
the same number.

## Targets

Strength steps use no target:
`{"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}`.

## The exercise catalogue

`workout_relay/schemas/exercises.json` (78 KB, 47 categories, 1,531 exercises) is
Garmin's own `exercises.json`, compacted to `category → {primary muscles,
exercises → {muscles, bodyweight flag}}`. It supersedes the community
spreadsheet, which had 1,207 entries and disagreed with Garmin in both
directions.

Two properties drive the schema design:

- **Names are nearly, but not quite, unique.** 1,495 distinct names; only **26
  appear in more than one category** — `ROW` in five (`ROW`, `SLED`, `SANDBAG`,
  `SUSPENSION`, `BANDED_EXERCISES`), `CURL` and `LUNGE` in four, `PLANK` and
  `SQUAT` in three. So a plan names the exercise alone and we derive the
  category; `category` is required only to disambiguate those 26, and validation
  names the candidates when it is missing.
- **30 categories contain an exercise with the category's own name** (`PLANK`
  under `PLANK`, and so on). That is a real specific exercise, distinct from the
  generic `exerciseName: ""` form above — the observed workout used both.

Every `(category, exerciseName)` pair in the observed workout validates against
this catalogue, which is the evidence that the two agree.

Category names are not guessable from exercise names — `KETTLEBELL_SWING` lives
under `HIP_RAISE`, `DEAD_BUG` under `HIP_STABILITY`, `ROMANIAN_DEADLIFT` under
`DEADLIFT`. Deriving the category from the catalogue rather than from the name
is the fix for the mis-categorisation that prompted this work.
