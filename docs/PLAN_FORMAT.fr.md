# Format des plans Workout Relay — Français

Workout Relay accepte un JSON strict. Aucune IA n'interprète le plan pendant
l'import : un même plan valide produit toujours la même structure Garmin.

## Structure principale

```json
{
  "schema_version": 1,
  "plan_id": "2026-W40-5k",
  "title": "Semaine de développement 5 km",
  "description": "Notes facultatives",
  "workouts": []
}
```

- `schema_version` vaut toujours `1`.
- `plan_id` est un identifiant stable de 3 à 100 caractères composé de lettres,
  chiffres, `_` et `-`.
- Un plan contient de 1 à 31 séances.
- Les champs inconnus sont refusés.

## Séance

```json
{
  "id": "2026-10-01_endurance",
  "date": "2026-10-01",
  "sport": "running",
  "title": "Endurance fondamentale",
  "description": "Description Garmin facultative",
  "estimated_duration_sec": 2700,
  "steps": []
}
```

L'identifiant est unique et commence par la date de la séance. Le titre est
limité à 50 caractères et la description à 500 caractères.

## Étapes simples

Les types sont `warmup`, `interval`, `recovery` et `cooldown`.

```json
{
  "type": "interval",
  "name": "Facile",
  "duration": { "type": "time", "value": 600 },
  "target": { "type": "heart_rate", "low": 135, "high": 150 }
}
```

- Le temps est exprimé en secondes.
- La distance est exprimée en mètres.
- La fréquence cardiaque est en bpm avec `low < high`.
- L'allure est en `M:SS` par kilomètre : `{ "type": "pace", "slow": "5:30", "fast": "5:10" }`.
- Utilisez `{ "type": "no_target" }` lorsqu'aucune alerte Garmin n'est souhaitée.

## Répétitions

Une répétition possède un `count` et contient de 1 à 5 étapes simples. Elles ne
peuvent pas être imbriquées. Une séance développée ne peut dépasser 50 étapes.

Validez toujours le JSON généré avant de le soumettre. Chaque erreur contient un
code stable, un chemin JSON et des paramètres afin qu'un assistant puisse
corriger le plan.

## Flux API pour un assistant

Utilisez `Authorization: Bearer <clé API Workout Relay>` sur chaque endpoint
privé.

1. Récupérez `GET /api/v1/plan-format?language=fr`, `GET /api/v1/plan-schema`
   et `GET /api/v1/plan-example`.
2. Envoyez le JSON brut à `POST /api/v1/plans/validate` et corrigez toutes les
   erreurs signalées.
3. Envoyez l'objet valide à `POST /api/v1/plans`. Une réponse HTTP `202`
   contient un `id` de soumission ; elle ne signifie pas que Garmin a terminé.
4. Interrogez `GET /api/v1/plans/{id}` jusqu'au statut `completed` ou `failed`.
   En cas de succès, consultez `result.counts` et le résultat de chaque séance.

La réutilisation d'un `id` de séance est volontaire : les séances inchangées
sont ignorées et les séances modifiées sont mises à jour sans doublon.
