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
  "id": "semaine40-endurance",
  "date": "2026-10-01",
  "sport": "running",
  "title": "Endurance fondamentale",
  "description": "Description Garmin facultative",
  "estimated_duration_sec": 2700,
  "steps": []
}
```

L'identifiant est unique pour votre compte et reste inchangé si la date change.
Utilisez 2 à 160 lettres minuscules, chiffres, soulignés ou tirets, en commençant
par une lettre ou un chiffre. Les anciens identifiants préfixés par une date
restent valides : ne les renommez pas pour déplacer une séance. Un nouvel
identifiant crée une autre séance. Le titre est
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

## Séances de musculation

Mettez `sport` à `strength`. Les champs de la séance sont identiques ; seules
les étapes changent. Les types d'étape sont `warmup`, `interval`, `cooldown` et
`rest`, les durées sont en `reps` ou en `time`, et il n'y a ni cible d'allure ni
cible cardiaque.

```json
{
  "id": "semaine40-bas",
  "date": "2026-10-02",
  "sport": "strength",
  "title": "Bas du corps",
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
          "description": "Charnière de hanche, genoux souples."
        },
        { "type": "rest", "duration": { "type": "time", "value": 60 } }
      ]
    }
  ]
}
```

Les séries s'expriment par un `repeat` contenant l'exercice puis une étape
`rest` ; ici une répétition accepte jusqu'à 12 étapes. `weight_kg` est en
kilogrammes. Une étape `rest` ne nomme aucun exercice, ne porte aucune charge et
doit être mesurée en temps.

### Nommer un exercice

`exercise` doit être un nom connu de Garmin : il y en a 1 531 répartis en 47
catégories. Cherchez-le avec l'outil `find_exercises` ou via
`GET /api/v1/exercises` ; ne l'inventez jamais.

La catégorie est déduite, le plan ne l'indique donc pas. C'est volontaire : elle
ne se devine pas à partir du nom (`KETTLEBELL_SWING` est classé sous
`HIP_RAISE`, `DEAD_BUG` sous `HIP_STABILITY`), et c'est précisément cette
supposition qui classait les exercices au mauvais endroit.

N'indiquez `category` que dans deux cas :

- Pour choisir une variante avec matériel. `SIDE_PLANK` seul désigne la version
  ordinaire ; `"category": "SUSPENSION"` demande la version en sangles.
- Lorsque la validation signale un nom ambigu. Seuls `CHEST_PRESS` et
  `GLUTE_BRIDGE` le sont, et l'erreur énumère les catégories possibles.

Les noms peuvent s'écrire naturellement : `romanian deadlift` comme
`Romanian-Deadlift` aboutissent à `ROMANIAN_DEADLIFT`. Un nom inconnu est refusé
avec les exercices réels les plus proches et leur catégorie ; reprenez l'un
d'eux plutôt que d'inventer un autre nom.

### Quand rien ne convient

Garmin autorise aussi une étape qui ne nomme qu'une catégorie, affichée telle
quelle sur la montre. Elle est acceptée ici, mais elle en dit moins à la
personne qu'un mouvement nommé : elle exige donc un champ `reason` écrit et
aucun `exercise` :

```json
{
  "type": "interval",
  "category": "CARRY",
  "reason": "Port valise avec une seule kettlebell ; aucun port listé ne correspond",
  "duration": { "type": "time", "value": 40 }
}
```

Le plan est alors validé avec un **avertissement** plutôt qu'une erreur, en
citant les exercices précis qui ont été écartés. Demandez d'abord à la personne.

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
