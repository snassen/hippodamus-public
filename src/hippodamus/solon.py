"""Solon: whose value counts.

Solon ranked the citizens of Athens into four classes. Here the classes are the four places a value can come from,
and the rule is the one talos and urania already both keep, each in its own SQL view (talos's
``effective_assignment``, urania's ``effective_labels``):

    human  >  rule  >  accepted model  >  import

Inside a class the newest value wins. A model's value counts only once a person has accepted it; until then it is a
proposal and counts for nothing.

Both products wrote this rule by hand, and urania's schema says "same as talos" in a comment. Solon is the one place
it is written down, in two forms: ``rank`` for Python and ``rank_sql`` for a view. A product keeps its own view (its
data, its migrations) and holds it to this rule with ``conformance_rows``: a small set of rows whose winner every
correct view must pick.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

ORIGINS = ("human", "rule", "model", "import")
"""Where a value came from, strongest first."""

_RANK = {o: len(ORIGINS) - i for i, o in enumerate(ORIGINS)}   # human 4, rule 3, model 2, import 1


class SolonError(ValueError):
    pass


def rank(origin: str) -> int:
    """How strong a value from this origin is: human 4, rule 3, model 2, import 1."""
    try:
        return _RANK[origin]
    except KeyError:
        raise SolonError(f"an origin is one of {', '.join(ORIGINS)}, not {origin!r}") from None


def rank_sql(column: str) -> str:
    """The same rank as a SQL expression over a column, for an ``order by … desc`` in a product's own view.

    ``column`` is an identifier the product writes itself (never user input)."""
    if not column.replace("_", "").replace(".", "").isalnum():
        raise SolonError("rank_sql takes a column name")
    whens = " ".join(f"when '{o}' then {_RANK[o]}" for o in ORIGINS)
    return f"(case {column} {whens} else 0 end)"


def counts(origin: str, accepted: bool) -> bool:
    """Whether a value counts at all: a model's only when accepted; a person's, a rule's or an import's always
    (a product that keeps a status still drops rejected and withdrawn values before asking)."""
    rank(origin)
    return accepted if origin == "model" else True


@dataclass(frozen=True)
class Candidate:
    """One value for one slot (an item's field, a message's dimension …)."""
    value: Any
    origin: str
    at: datetime
    accepted: bool = True
    ref: Any = None        # the product's own id for it, returned as is


def effective(candidates: Iterable[Candidate]) -> Candidate | None:
    """The value that counts for one slot, or None when nothing counts."""
    alive = [c for c in candidates if counts(c.origin, c.accepted)]
    if not alive:
        return None
    return max(alive, key=lambda c: (rank(c.origin), c.at))


def effective_by(candidates: Iterable[Candidate], slot: Callable[[Candidate], Any]) -> dict[Any, Candidate]:
    """``effective`` for many slots at once: {slot: winner}."""
    groups: dict[Any, list[Candidate]] = {}
    for c in candidates:
        groups.setdefault(slot(c), []).append(c)
    out = {}
    for k, cs in groups.items():
        w = effective(cs)
        if w is not None:
            out[k] = w
    return out


def conformance_rows() -> tuple[list[dict], dict[str, str]]:
    """Rows a product loads into its own table to prove its view follows Solon, and the winner per slot.

    Each row: slot, value, origin, accepted, at. The answer maps slot -> the value that must win. The cases are the
    ones that are easy to get wrong: an older human against a newer rule, an unaccepted model against an import,
    two values of the same origin."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later = t0 + timedelta(days=1)
    rows = [
        # an old human decision beats a newer rule
        dict(slot="a", value="human-old", origin="human", accepted=True, at=t0),
        dict(slot="a", value="rule-new", origin="rule", accepted=True, at=later),
        # a proposed model value counts for nothing: the import wins
        dict(slot="b", value="model-proposed", origin="model", accepted=False, at=later),
        dict(slot="b", value="import", origin="import", accepted=True, at=t0),
        # an accepted model value beats an import
        dict(slot="c", value="model-accepted", origin="model", accepted=True, at=t0),
        dict(slot="c", value="import-new", origin="import", accepted=True, at=later),
        # inside one origin the newest wins
        dict(slot="d", value="rule-old", origin="rule", accepted=True, at=t0),
        dict(slot="d", value="rule-new", origin="rule", accepted=True, at=later),
        # a rule beats an accepted model value
        dict(slot="e", value="model", origin="model", accepted=True, at=later),
        dict(slot="e", value="rule", origin="rule", accepted=True, at=t0),
    ]
    answer = {"a": "human-old", "b": "import", "c": "model-accepted", "d": "rule-new", "e": "rule"}
    return rows, answer
