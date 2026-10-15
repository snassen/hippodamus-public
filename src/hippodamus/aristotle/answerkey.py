"""The answer key: a frozen, stratified sample the owner labels blind, and predictions scored against it.

The ideas of talos.gold, without its database: a sample drawn by strata so the rare kinds are in it, the same seed
giving the same items, the order shuffled so strata never come in blocks; and a score per field that keeps coverage
(how much was predicted) apart from accuracy (how much of that was right), with the wrong pairs counted, since
"predicted invoice, was receipt" says what to fix.

A product keeps where the sample and the labels are stored, and what a case is.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from typing import Callable, Hashable, Iterable


def _split(total: int, weights: list[int]) -> list[int]:
    """total split in proportion to weights, largest remainder first; the parts sum to total (talos.gold._split)."""
    s = sum(weights) or 1
    raw = [total * w / s for w in weights]
    parts = [int(r) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: (-(raw[i] - parts[i]), i))[:total - sum(parts)]:
        parts[i] += 1
    return parts


def allocate(sizes: dict[Hashable, int], n: int) -> dict[Hashable, int]:
    """How many items each stratum gets: every stratum at least one, the rest in proportion to its size, never more
    than it has. With fewer items than strata, the largest strata get one each (ties by name)."""
    n = max(0, min(n, sum(sizes.values())))
    keys = sorted(sizes, key=str)
    if n < len(keys):
        chosen = sorted(keys, key=lambda k: (-sizes[k], str(k)))[:n]
        return {k: int(k in chosen) for k in keys}
    want = dict(zip(keys, _split(n, [sizes[k] for k in keys])))
    for k in keys:
        while want[k] < 1:   # a small stratum is represented; the largest share gives up one
            donor = max((d for d in keys if want[d] > 1), key=lambda d: (want[d], str(d)))
            want[donor] -= 1
            want[k] += 1
    return want


def stratified_sample(cases: Iterable, key: Callable | str, n: int, seed: int | None = None) -> list:
    """n cases drawn from cases by stratum: deterministic (the same cases in the same order and the same seed give
    the same sample), every stratum represented, proportional otherwise (allocate), and returned in an order
    shuffled by the seed. key is a callable giving a case's stratum, or the name of a key of a dict case."""
    cases = list(cases)
    of = key if callable(key) else (lambda c: c[key])
    strata: dict[Hashable, list] = defaultdict(list)
    for c in cases:
        strata[of(c)].append(c)
    rng = random.Random(seed)
    want = allocate({k: len(v) for k, v in strata.items()}, n)
    out = []
    for k in sorted(strata, key=str):
        out.extend(rng.sample(strata[k], want[k]))
    rng.shuffle(out)
    return out


def _values(v) -> tuple | None:
    """A prediction or an answer as a set of values: None when there is none (not predicted, not labelled). A
    Decision (aristotle.policy) counts as its selection when decided, else as not predicted."""
    if v is None:
        return None
    if hasattr(v, "selected") and hasattr(v, "decided"):
        return tuple(v.selected) if v.decided else None
    if isinstance(v, (list, tuple, set, frozenset)):
        return tuple(v)
    return (v,)


def _show(values: tuple) -> str:
    return ",".join(sorted(map(str, values))) or "none"


def score(predictions: dict, truth: dict, fields: Iterable[str] | None = None) -> dict[str, dict]:
    """Score predictions against the owner's answers, per field.

    predictions and truth: {case: {field: value}}, a value being one value, a list of values (a many-value field;
    [] is the answer "none") or None; a prediction may also be a policy.Decision. A case or field left out of truth
    (or None there) is not labelled and not counted; one left out of predictions was not predicted.

    Per field: n (labelled), predicted, correct (the whole set equal), accuracy (correct / predicted), coverage
    (predicted / n), and wrong: {"predicted→truth": count}, most common first. fields defaults to every field in
    truth."""
    if fields is None:
        fields = sorted({f for labels in truth.values() for f in labels})
    out = {}
    for f in fields:
        n = predicted = correct = 0
        wrong: Counter = Counter()
        for case, labels in truth.items():
            t = _values((labels or {}).get(f))
            if t is None:
                continue
            n += 1
            p = _values((predictions.get(case) or {}).get(f))
            if p is None:
                continue
            predicted += 1
            if set(p) == set(t):
                correct += 1
            else:
                wrong[f"{_show(p)}→{_show(t)}"] += 1
        out[f] = {"n": n, "predicted": predicted, "correct": correct,
                  "accuracy": round(correct / predicted, 4) if predicted else None,
                  "coverage": round(predicted / n, 4) if n else None,
                  "wrong": dict(sorted(wrong.items(), key=lambda kv: (-kv[1], kv[0])))}
    return out
