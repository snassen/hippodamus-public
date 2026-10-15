"""How sure a value is, in words: seven named spans of confidence.

Lifted from talos.sureness (the Python side; talos keeps level_sql and the pane's wording). A value's confidence is a
number between 0 and 1 (the model's probability, or a calibrated one). Named spans make it readable and filterable:

| Level     | From  | What it means                                                     |
|-----------|-------|-------------------------------------------------------------------|
| Yours     | 1.00  | The owner decided it: a decision, not a measured 100%.            |
| Certain   | 0.95  | The model is at least 95% sure; a rule's values pass here.        |
| Confident | 0.85  | Right almost every time.                                          |
| Likely    | 0.70  | Right most of the time: the policy accepts exact values from here.|
| Maybe     | 0.50  | As often right as not.                                            |
| Doubtful  | 0.30  | More often wrong than right.                                      |
| Guess     | 0.00  | A shot in the dark.                                               |

The source decides where a value starts: the owner's own (human) is always Yours (key 'fact'); a rule's passes as
Certain; an import as Likely; a model value is placed by its confidence (a model value with none counts as Likely).
"""

from __future__ import annotations

# (key, label, lower bound, meaning), surest first.
LEVELS = (
    ("fact", "Yours", 1.00, "You decided it: a decision, not a measured probability."),
    ("certain", "Certain", 0.95, "The model is at least 95% sure (a rule's values pass here too)."),
    ("confident", "Confident", 0.85, "Right almost every time."),
    ("likely", "Likely", 0.70, "Right most of the time: exact values are accepted from here."),
    ("maybe", "Maybe", 0.50, "As often right as not."),
    ("doubtful", "Doubtful", 0.30, "More often wrong than right."),
    ("guess", "Guess", 0.00, "A shot in the dark."),
)
KEYS = tuple(k for k, *_ in LEVELS)
FLOOR = {k: lo for k, _, lo, _ in LEVELS}
LABEL = {k: label for k, label, _, _ in LEVELS}
# What a source counts as, when its own confidence does not say.
SOURCE_CONFIDENCE = {"human": 1.0, "rule": 0.97, "import": 0.75}
MODEL_WITHOUT_CONFIDENCE = 0.75


class SurenessError(ValueError):
    pass


def check(level: str | None) -> str | None:
    if level in (None, ""):
        return None
    if level not in FLOOR:
        raise SurenessError(f"a level is one of {', '.join(KEYS)}")
    return level


def confidence_of(source_kind: str | None, confidence: float | None) -> float:
    """The number a value is placed by: the owner's are 1.0, a rule's 0.97, an import's 0.75, a model's its own."""
    if source_kind in SOURCE_CONFIDENCE:
        return SOURCE_CONFIDENCE[source_kind]
    return MODEL_WITHOUT_CONFIDENCE if confidence is None else float(confidence)


def level_of(source_kind: str | None, confidence: float | None) -> str:
    """The level of a value: 'fact' (Yours) only for the owner's own, then by its confidence."""
    if source_kind == "human":
        return "fact"
    c = confidence_of(source_kind, confidence)
    for k, _, lo, _ in LEVELS[1:]:
        if c >= lo - 1e-9:
            return k
    return "guess"


def at_least(level: str, floor: str) -> bool:
    """Is level as sure as floor, or surer?"""
    return KEYS.index(level) <= KEYS.index(floor)


def levels() -> list[dict]:
    """The levels for a UI: key, label, lower bound, meaning."""
    return [{"key": k, "label": label, "from": lo, "meaning": text} for k, label, lo, text in LEVELS]
