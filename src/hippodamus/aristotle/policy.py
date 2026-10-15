"""The routing policy: how a model's probabilities become decisions.

Lifted from talos.policy (its VERSION 2 rule), without talos's mail-specific gates. The model does not pick labels
itself: it gives a probability for every value of a ``choice`` question and a score from 0 to 1 for every ``noul``
statement. This deterministic, versioned code turns those into decisions, apart from the client, so an evaluation
can sweep the thresholds without asking the model again.

- **One-value field**: decided when the top probability is at least ``one_min`` (0.70) and it leads the runner-up by
  at least ``one_margin`` (0.15). Otherwise it is unresolved; the top value is still kept as the candidate.
- **Many-value field as statements**: every statement scored at least ``many_min`` (0.5) is selected. The field is
  always decided (an empty selection is the answer "none"). Its confidence is the least certain statement's
  certainty, max(s, 1 − s): the set is only as sure as its closest call.
- **Many-value field as one choice** (``route_choice_many``): one choice with a ``none`` option, plus side
  statements. Routed like a one-value field; selected = [top] when top is not ``none`` and it is decided, otherwise
  [] (none). A side statement is added when it scores at least ``many_min`` *and* the choice selected something.
  Talos learned this the hard way: asked one statement per value, Jev said yes to almost every value.
- **Cross-field gates** (``route_case(..., gates=…)``): a product's own rules over the routed decisions. A gate is a
  callable ``gate(decisions) -> {field: name}`` (or an iterable of (field, name) pairs) naming the fields to force to
  none; a gate only counts when it changed something (the field had a selection). Which gates fired is returned
  beside the decisions. Talos's gates (an ask only from a person, a route only for work) stay in talos.

Every probability and score the routes take is checked first, whoever passes it (``jev.choice_probabilities`` or a
product's own code): a number (int or float, not a bool or a string), finite, in 0..1, else PolicyError. Nothing is
clamped or coerced, so a malformed proposal can never become a decision. The routes do not check that a choice's
probabilities sum to 1: the parser does that for Jev's answers, and talos's own cases (and evaluation sweeps) route
partial or hand-made sets.

Change a threshold's default or the rule itself → bump VERSION.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Callable, Iterable, Mapping

VERSION = 1

NONE = "none"


class PolicyError(ValueError):
    """A probability or score that is not a number in 0..1: refused, never routed."""


def _checked(field: str, scores) -> dict:
    if not isinstance(scores, Mapping):
        raise PolicyError(f"the scores of {field} are not a mapping of value to number")
    for v, p in scores.items():
        # The bounds before isfinite: a comparison takes any int (10**10000 too), isfinite would overflow.
        if not isinstance(p, (int, float)) or isinstance(p, bool) or not 0 <= p <= 1 or not math.isfinite(p):
            raise PolicyError(f"the score of {field}={v!r} is not a number in 0..1")
    return dict(scores)


@dataclass(frozen=True)
class Policy:
    one_min: float = 0.70
    one_margin: float = 0.15
    many_min: float = 0.5
    version: int = VERSION

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def name(self) -> str:
        return f"p≥{self.one_min:.2f} m≥{self.one_margin:.2f} s≥{self.many_min:.2f} (v{self.version})"


DEFAULT = Policy()


@dataclass(frozen=True)
class Decision:
    """One field of one case after routing.

    top (also ``candidate``): the best value (one-value, or a choice's top, ``none`` included) or the best-scored
    statement (many-value as statements), None if none.
    selected: the decided values: [top] for a decided one-value field, [] when unresolved; the statements at or over
    the threshold for a many-value field; [top] (+ side statements) for a many-value choice whose top is decided and
    not none.
    scores (also ``probabilities``): what the model gave, unchanged.
    confidence: the top probability (one-value, choice) or the least certain statement's certainty.
    margin: top minus runner-up (one-value, choice); None for statements."""
    field: str
    top: str | None
    selected: tuple[str, ...]
    scores: dict
    confidence: float | None
    margin: float | None
    decided: bool

    @property
    def candidate(self) -> str | None:
        return self.top

    @property
    def probabilities(self) -> dict:
        return self.scores

    @property
    def value(self) -> str | None:
        """The one decided value of a one-value field, else None."""
        return self.selected[0] if self.decided and self.selected else None


def route_one(field: str, probabilities: dict[str, float], policy: Policy = DEFAULT) -> Decision:
    probabilities = _checked(field, probabilities)
    ranked = sorted(((float(p), v) for v, p in probabilities.items()), key=lambda x: (-x[0], x[1]))
    if not ranked:
        return Decision(field, None, (), {}, None, None, False)
    top_p, top = ranked[0]
    runner = ranked[1][0] if len(ranked) > 1 else 0.0
    margin = round(top_p - runner, 6)
    decided = top_p >= policy.one_min - 1e-9 and margin >= policy.one_margin - 1e-9
    return Decision(field, top, (top,) if decided else (), dict(probabilities), top_p, margin, decided)


def route_many(field: str, scores: dict[str, float], policy: Policy = DEFAULT) -> Decision:
    scores = _checked(field, scores)
    if not scores:
        return Decision(field, None, (), {}, None, None, True)
    items = [(v, float(s)) for v, s in scores.items()]
    selected = tuple(v for v, s in items if s >= policy.many_min - 1e-9)
    top = max(items, key=lambda x: (x[1], x[0]))[0]
    confidence = round(min(max(s, 1 - s) for _, s in items), 6)
    return Decision(field, top, selected, dict(scores), confidence, None, True)


def route_choice_many(field: str, scores: dict[str, float], policy: Policy = DEFAULT,
                      statements: tuple[str, ...] = ()) -> Decision:
    """A many-value field asked as one choice with ``none``. scores holds the choice's probabilities and, under
    their own names, the side statements' scores."""
    scores = _checked(field, scores)
    choice ={v: p for v, p in scores.items() if v not in statements}
    one = route_one(field, choice, policy)
    selected = (one.top,) if one.decided and one.top != NONE else ()
    if selected:
        selected += tuple(s for s in statements if float(scores.get(s, 0.0)) >= policy.many_min - 1e-9)
    return Decision(field, one.top, selected, dict(scores), one.confidence, one.margin, one.decided)


def route(field: str, many: bool, scores: dict[str, float], policy: Policy = DEFAULT) -> Decision:
    return route_many(field, scores, policy) if many else route_one(field, scores, policy)


Gate = Callable[[Mapping[str, Decision]], "Mapping[str, str] | Iterable[tuple[str, str]] | None"]


def gate(decisions: dict[str, Decision], gates: Iterable[Gate] = ()) -> tuple[dict[str, Decision],
                                                                              dict[str, list[str]]]:
    """Apply the product's cross-field gates to routed decisions: the gated decisions, and per field the names of
    the gates that changed it. Every gate sees the decisions as routed (not as an earlier gate left them), so the
    order of the gates does not matter; only a field that had a selection is changed."""
    out = dict(decisions)
    fired: dict[str, list[str]] = {}
    for g in gates:
        hits = g(decisions) or {}
        for f, name in (hits.items() if isinstance(hits, Mapping) else hits):
            d = decisions.get(f)
            if d is not None and d.selected:
                fired.setdefault(f, []).append(name)
    for f in fired:
        out[f] = replace(out[f], selected=())
    return out, fired


def route_case(scores: dict[str, dict[str, float]], policy: Policy = DEFAULT, *, many: Iterable[str] = (),
               choice_many: Mapping[str, tuple[str, ...]] | None = None,
               gates: Iterable[Gate] = ()) -> tuple[dict[str, Decision], dict[str, list[str]]]:
    """Every field of one case: {field: scores} → ({field: Decision}, {field: gates fired}).

    many: the fields routed as statements; choice_many: the many-value fields asked as one choice with none,
    each with its side statements ({field: (statement, …)}); every other field is one-value. Then the gates."""
    many, choice_many = set(many), dict(choice_many or {})
    out: dict[str, Decision] = {}
    for f, s in scores.items():
        if f in choice_many:
            out[f] = route_choice_many(f, s, policy, tuple(choice_many[f]))
        else:
            out[f] = route(f, f in many, s, policy)
    return gate(out, gates)
