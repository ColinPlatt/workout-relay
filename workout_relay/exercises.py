"""Garmin's strength exercise catalogue, and how a plan names one.

The catalogue is Garmin's own `exercises.json`, vendored in `schemas/` and
described in `docs/garmin-strength-format.md`. A plan names an exercise alone
and the category is derived here, because the category is not guessable from
the name — `KETTLEBELL_SWING` is filed under `HIP_RAISE`, `DEAD_BUG` under
`HIP_STABILITY` — and guessing it is what previously filed exercises wrongly.

Twenty-six names sit in more than one category (`ROW` in five). For those, and
only those, a plan has to say which category it means, and the refusal lists
the candidates.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

_CATALOGUE_PATH = Path(__file__).parent / "schemas" / "exercises.json"

#: A step may name a category with no exercise, which Garmin sends as
#: ``exerciseName: ""`` and the watch shows as the category itself. It is a
#: real Garmin feature, not a workaround, but it tells the person less than a
#: named movement does, so validation discourages it.
GENERIC = ""


@lru_cache(maxsize=1)
def _catalogue() -> dict:
    return json.loads(_CATALOGUE_PATH.read_text())


@lru_cache(maxsize=1)
def _index() -> dict[str, tuple[str, ...]]:
    """Exercise name -> the categories holding it, in catalogue order."""
    found: dict[str, list[str]] = {}
    for category, body in sorted(_catalogue().items()):
        for name in body["e"]:
            found.setdefault(name, []).append(category)
    return {name: tuple(categories) for name, categories in found.items()}


def categories() -> list[str]:
    return sorted(_catalogue())


def exercises_in(category: str) -> list[str]:
    body = _catalogue().get(category)
    return sorted(body["e"]) if body else []


def is_category(name: str) -> bool:
    return name in _catalogue()


def is_bodyweight(category: str, exercise: str) -> bool:
    entry = _catalogue().get(category, {}).get("e", {}).get(exercise)
    return bool(entry and entry.get("bw"))


def muscles(category: str, exercise: str | None = None) -> list[str]:
    body = _catalogue().get(category)
    if not body:
        return []
    if exercise:
        return list(body["e"].get(exercise, {}).get("m", []))
    return list(body.get("m", []))


class Unknown(Exception):
    """No such exercise; `suggestions` are the closest real ones."""

    def __init__(self, name: str, suggestions: list[dict]):
        super().__init__(name)
        self.name = name
        self.suggestions = suggestions


class Ambiguous(Exception):
    """A real name, but in several categories; the plan must pick one."""

    def __init__(self, name: str, candidates: tuple[str, ...]):
        super().__init__(name)
        self.name = name
        self.candidates = list(candidates)


class WrongCategory(Exception):
    """A real name and a real category, but the two do not go together."""

    def __init__(self, name: str, category: str, candidates: tuple[str, ...]):
        super().__init__(name)
        self.name = name
        self.category = category
        self.candidates = list(candidates)


def resolve(exercise: str, category: str | None = None) -> tuple[str, str]:
    """The `(category, exerciseName)` Garmin expects for this step.

    Raises `Unknown`, `Ambiguous` or `WrongCategory`, each carrying what the
    caller needs to say something useful rather than just "invalid".
    """
    name = normalise(exercise)
    known = _index().get(name)
    if known is None:
        raise Unknown(exercise, suggest(exercise))
    if category is None:
        if len(known) > 1:
            settled = _canonical(name, known)
            if settled is None:
                raise Ambiguous(name, known)
            return settled, name
        return known[0], name
    wanted = normalise(category)
    if wanted not in known:
        raise WrongCategory(name, category, known)
    return wanted, name


#: Categories that name a piece of equipment rather than a movement. This
#: partition of Garmin's 47 categories is our judgement, not Garmin's: the
#: catalogue does not label them. It exists because a bare `SIDE_PLANK` means
#: the ordinary one, not the suspension-trainer variant, and forcing a category
#: on every such name would make the common case the awkward one.
EQUIPMENT = frozenset({
    "BANDED_EXERCISES", "BATTLE_ROPE", "LADDER", "SANDBAG", "SLED",
    "SLEDGE_HAMMER", "SUSPENSION", "TIRE",
})


def _canonical(name: str, known: tuple[str, ...]) -> str | None:
    """The category a bare name means, or None if it is a real choice.

    Two rules, in order. A category with the exercise's own name is that
    movement's home (`PLANK` in `PLANK`), which is exact equality rather than
    inference. Otherwise, if exactly one category is not equipment, the bare
    name means the plain version (`RUSSIAN_TWIST` is `CORE`, not `SANDBAG`).
    Anything still undecided is left to the caller.
    """
    if name in known:
        return name
    movements = [item for item in known if item not in EQUIPMENT]
    return movements[0] if len(movements) == 1 else None


def resolve_generic(category: str) -> tuple[str, str]:
    """A category-only step, for when nothing specific fits."""
    wanted = normalise(category)
    if wanted not in _catalogue():
        raise Unknown(category, [{"category": item} for item in _close_categories(category)])
    return wanted, GENERIC


def normalise(value: str) -> str:
    """Accept what a person or an assistant would plausibly type.

    Garmin's own names are upper snake case, with a leading underscore where
    the name starts with a digit (`_90_DEGREE_STATIC_HOLD`), so "side plank",
    "Side-Plank" and "SIDE_PLANK" all have to arrive at the same entry.
    """
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "")).strip("_").upper()
    if text and text[0].isdigit():
        text = "_" + text
    return text


def suggest(query: str, limit: int = 5) -> list[dict]:
    """The closest real exercises, each with the category it belongs to."""
    wanted = normalise(query)
    if not wanted:
        return []
    scored = []
    for name, found in _index().items():
        score = _similarity(wanted, name)
        if score > 0:
            scored.append((score, name, found[0]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"exercise": name, "category": category}
        for _, name, category in scored[:limit]
    ]


def search(query: str = "", category: str | None = None, limit: int = 20) -> list[dict]:
    """Browse the catalogue: by text, by category, or by muscle."""
    wanted = normalise(query)
    picked = normalise(category) if category else None
    results = []
    for name, found in _index().items():
        for owner in found:
            if picked and owner != picked:
                continue
            worked = _catalogue()[owner]["e"][name].get("m", [])
            if not wanted:
                score = 1.0
            elif wanted in worked or wanted in owner:
                score = 0.9
            else:
                score = _similarity(wanted, name)
            if score > 0:
                results.append((score, name, owner, worked))
    results.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"exercise": name, "category": owner, "muscles": worked,
         "bodyweight": is_bodyweight(owner, name)}
        for _, name, owner, worked in results[:limit]
    ]


def _similarity(wanted: str, name: str) -> float:
    """Edit distance, lifted when the two share whole words.

    Edit distance alone ranks `FARMERS_CARRY` nowhere near `FARMERS_WALK`,
    which is exactly the substitution someone means.
    """
    ratio = SequenceMatcher(None, wanted, name).ratio()
    mine, theirs = set(wanted.split("_")), set(name.split("_"))
    shared = mine & theirs
    if shared:
        ratio = max(ratio, len(shared) / max(len(mine), len(theirs)))
    return ratio if ratio >= 0.5 else 0.0


def _close_categories(query: str, limit: int = 5) -> list[str]:
    wanted = normalise(query)
    scored = [
        (_similarity(wanted, category), category)
        for category in _catalogue()
    ]
    return [name for score, name in sorted(scored, key=lambda i: (-i[0], i[1])) if score > 0][:limit]
