"""The closed value lists a product classifies into, and the choice question for a field.

Lifted from talos.taxonomy (``read``, its checks) and talos.jev (``_criterion``, the choice question). The shape is
talos's rules/taxonomy.json:

    {"version": 1,
     "dimensions": {
        "doctype": {"label": "Document type", "cardinality": "one", "description": "What the file is.",
                    "values": [{"value": "invoice", "family": "Money", "label": "Invoice",
                                "description": "A bill to pay."}, …]},
        "money":   {"label": "Money or not", "cardinality": "one", "description": "…",
                    "derived_from": {"dimension": "doctype", "families": {"Money": "money", "Other": "other"}},
                    "values": […]}}}

Each value needs ``value`` and its definition (``description``); ``family`` and ``label`` may be left out. A value
list may also be written as a plain map ``{value: definition}``. A dimension may be **derived** from a one-value
dimension (``derived_from``: the source and either ``families``, every family of the source mapped to one of its
values, or ``values``, each of its values with the source values under it); every family or value of the source
must map to exactly one, so nothing slips through unmapped. ``Taxonomy.side_of(name)`` gives that mapping for
``boundary.side_probabilities``.

A key given twice in one object is refused when the file is read with ``Taxonomy.from_json`` or ``load`` (JSON
would quietly keep the last).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

CARDINALITIES = ("one", "many")


class TaxonomyError(ValueError):
    pass


def _plain(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.casefold())


@dataclass(frozen=True)
class Dimension:
    """One field: its closed list of values, each with its definition (the criterion Jev is given)."""
    name: str
    label: str
    cardinality: str
    values: dict[str, str]
    families: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    description: str = ""
    derived_from: dict | None = None

    @property
    def many(self) -> bool:
        return self.cardinality == "many"

    def criterion(self, value: str) -> str:
        """A value's definition, led by its label when the label says more than the id (talos.jev._criterion)."""
        desc = (self.values.get(value) or "").strip()
        label = (self.labels.get(value) or "").strip()
        if label and _plain(label) != _plain(value) and _plain(label) not in _plain(value):
            return f"{label}: {desc}" if desc else label
        return desc or label or value


@dataclass(frozen=True)
class Taxonomy:
    dimensions: dict[str, Dimension]
    version: object = None

    @classmethod
    def from_dict(cls, d: dict) -> "Taxonomy":
        dims = d.get("dimensions") if isinstance(d, dict) else None
        if not isinstance(dims, dict) or not dims:
            raise TaxonomyError("a taxonomy needs a non-empty map of dimensions")
        out: dict[str, Dimension] = {}
        for name, dd in dims.items():
            out[name] = _dimension(name, dd)
        for name, dim in out.items():
            if dim.derived_from is not None:
                _check_derived(dim, out)
        return cls(out, d.get("version"))

    @classmethod
    def from_json(cls, text: str) -> "Taxonomy":
        try:
            return cls.from_dict(json.loads(text, object_pairs_hook=_no_duplicates))
        except json.JSONDecodeError as exc:
            raise TaxonomyError(f"not JSON: {exc}") from None

    @classmethod
    def load(cls, path: Path | str) -> "Taxonomy":
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise TaxonomyError(f"cannot read {path}: {exc}") from None
        return cls.from_json(text)

    def __contains__(self, name: str) -> bool:
        return name in self.dimensions

    def __iter__(self):
        return iter(self.dimensions)

    def dimension(self, name: str) -> Dimension:
        try:
            return self.dimensions[name]
        except KeyError:
            raise TaxonomyError(f"no dimension {name!r}; the taxonomy has {', '.join(self.dimensions)}") from None

    def check(self, field: str, value):
        """The value (or, for a many-value field, each of a list of values) if the field's list has it; else
        TaxonomyError. Returns what it was given."""
        d = self.dimension(field)
        values = list(value) if isinstance(value, (list, tuple, set, frozenset)) else [value]
        if len(values) != 1 and not d.many:
            raise TaxonomyError(f"{field} takes one value")
        bad = [v for v in values if v not in d.values]
        if bad:
            raise TaxonomyError(f"{field} has no value {bad[0]!r}")
        return value

    def choice_question(self, field: str, instructions: str | None = None, *, none: str | None = None) -> dict:
        """Jev's choice question for a field: its values as criteria, in the taxonomy's order, each with its
        definition (led by its label when that says more). instructions default to the field's description.
        none: when given, a ``none`` option with that criterion leads the list (a many-value field asked as one
        choice, routed by ``policy.route_choice_many``)."""
        d = self.dimension(field)
        criteria = {"none": none} if none is not None else {}
        criteria.update({v: d.criterion(v) for v in d.values})
        return {"type": "choice", "instructions": (instructions if instructions is not None else d.description),
                "criteria": criteria}

    def side_of(self, name: str) -> dict[str, str]:
        """{source value: derived value} for a derived dimension (talos.taxonomy.mapping)."""
        d = self.dimension(name)
        if d.derived_from is None:
            raise TaxonomyError(f"{name} is not derived from another dimension")
        src = self.dimension(d.derived_from["dimension"])
        if isinstance(d.derived_from.get("values"), dict):
            inv = {x: k for k, g in d.derived_from["values"].items() for x in g}
            return {v: inv[v] for v in src.values}
        fam = d.derived_from["families"]
        return {v: fam[src.families[v]] for v in src.values}


def _no_duplicates(pairs: list) -> dict:
    keys = [k for k, _ in pairs]
    twice = sorted({k for k in keys if keys.count(k) > 1})
    if twice:
        raise TaxonomyError(f"a key is given twice in one object: {', '.join(twice)}")
    return dict(pairs)


def _dimension(name: str, d) -> Dimension:
    if not isinstance(d, dict):
        raise TaxonomyError(f"{name}: a dimension is an object")
    if d.get("cardinality") not in CARDINALITIES:
        raise TaxonomyError(f"{name}: cardinality is one of {', '.join(CARDINALITIES)}")
    raw = d.get("values")
    if isinstance(raw, dict):
        raw = [{"value": k, "description": v} for k, v in raw.items()]
    if not isinstance(raw, list) or not raw:
        raise TaxonomyError(f"{name}: needs a non-empty list of values")
    values: dict[str, str] = {}
    families: dict[str, str] = {}
    labels: dict[str, str] = {}
    for v in raw:
        if not isinstance(v, dict) or not isinstance(v.get("value"), str) or not v["value"].strip() \
                or not isinstance(v.get("description"), str):
            raise TaxonomyError(f"{name}: every value needs a value and a description ({v!r})")
        for k in ("family", "label"):
            if v.get(k) is not None and not isinstance(v[k], str):
                raise TaxonomyError(f"{name}: a value's {k} is text ({v!r})")
        if v["value"] in values:
            raise TaxonomyError(f"{name}: {v['value']!r} is listed twice")
        values[v["value"]] = v["description"]
        if v.get("family"):
            families[v["value"]] = v["family"]
        if v.get("label"):
            labels[v["value"]] = v["label"]
    return Dimension(name, d.get("label") or name, d["cardinality"], values, families, labels,
                     d.get("description") or "", d.get("derived_from"))


def _check_derived(dim: Dimension, dims: dict[str, Dimension]) -> None:
    """As talos.taxonomy._check_derived: one value per thing, its source a one-value dimension, and every family
    (families) or every value (values) of the source mapped to exactly one of its values."""
    src = dim.derived_from
    if not isinstance(src, dict) or (isinstance(src.get("families"), dict) == isinstance(src.get("values"), dict)):
        raise TaxonomyError(f"{dim.name}: derived_from needs a dimension and either a families or a values map")
    source = dims.get(src.get("dimension"))
    if source is None or source.cardinality != "one" or dim.cardinality != "one":
        raise TaxonomyError(f"{dim.name}: derived_from names a one-value dimension of the file, and {dim.name} is "
                            "one-value")
    if isinstance(src.get("values"), dict):
        _check_value_map(dim, src, source)
        return
    unfamilied = [v for v in source.values if v not in source.families]
    if unfamilied:
        raise TaxonomyError(f"{dim.name}: these {source.name} values have no family: {', '.join(unfamilied)}")
    families = set(source.families.values())
    unmapped = sorted(families - set(src["families"]))
    if unmapped:
        raise TaxonomyError(f"{dim.name}: these families of {source.name} map to nothing: {', '.join(unmapped)}")
    bad = sorted(f for f, side in src["families"].items() if f not in families or side not in dim.values)
    if bad:
        raise TaxonomyError(f"{dim.name}: derived_from maps an unknown family or to an unknown value: {', '.join(bad)}")


def _check_value_map(dim: Dimension, src: dict, source: Dimension) -> None:
    groups = src["values"]
    bad = sorted(k for k, g in groups.items() if k not in dim.values or not isinstance(g, list)
                 or not all(isinstance(x, str) for x in g))
    if bad:
        raise TaxonomyError(f"{dim.name}: derived_from.values has an unknown value or a group that is not a list:"
                            f" {', '.join(bad)}")
    seen: dict[str, list[str]] = {}
    for k, g in groups.items():
        for x in g:
            seen.setdefault(x, []).append(k)
    twice = sorted(f"{x} ({', '.join(ks)})" for x, ks in seen.items() if len(ks) > 1)
    if twice:
        raise TaxonomyError(f"{dim.name}: these {source.name} values are under more than one {dim.name}:"
                            f" {'; '.join(twice)}")
    unknown = sorted(x for x in seen if x not in source.values)
    if unknown:
        raise TaxonomyError(f"{dim.name}: derived_from.values lists {source.name} values the file does not have:"
                            f" {', '.join(unknown)}")
    unmapped = [x for x in source.values if x not in seen]
    if unmapped:
        raise TaxonomyError(f"{dim.name}: these {source.name} values map to no {dim.name}: {', '.join(unmapped)}")
