"""Boundaries: the coarse lines that are costly to get wrong, from an exact field's scores.

Lifted from talos.boundary (the pure part; talos keeps the SQL and its four mail boundaries). Not every mistake costs
the same: taking an alert for a notification is fine, taking a person who asks the owner something for a machine is
not. So beside an exact field a product keeps a coarse dimension derived from it (``Taxonomy.side_of``: each source
value's side, by family or by value), and decides it on its own.

**A side's probability** is the sum of the model's probabilities of the source values on that side, from the full
scores (not the top three). A side is decided at BOUNDARY_MIN (0.90).
"""

from __future__ import annotations

BOUNDARY_MIN = 0.90


def side_probabilities(scores: dict[str, float], side_of: dict[str, str], all_sides=()) -> dict[str, float]:
    """The sum of the probabilities of each side's values. A value with no side is left out."""
    out = {s: 0.0 for s in all_sides}
    for v, p in (scores or {}).items():
        s = side_of.get(v)
        if s is not None:
            out[s] = out.get(s, 0.0) + float(p)
    return {s: round(p, 6) for s, p in out.items()}


def decide(probs: dict[str, float], threshold: float = BOUNDARY_MIN) -> tuple[str | None, float, float, bool]:
    """(top side, its probability, margin over the next side, decided at threshold)."""
    if not probs:
        return None, 0.0, 0.0, False
    ranked = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))
    top, p = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    return top, p, round(p - runner, 6), p >= threshold - 1e-9
