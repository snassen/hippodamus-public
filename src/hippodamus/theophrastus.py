"""Theophrastus: rules as data, compiled to SQL over a product's own fields.

Theophrastus sorted the plants of Greece by what could be seen of them. A rule here sorts a product's things the same
way: a list of conditions over what can be seen of them (a sender, a folder, a file type, a label).

Lifted from talos.rules (``compile_conditions``), proven there on message rules and binders' stored queries since
talos 0.1. What was talos's is now the general part; a product brings its own ``Fields``:

    conditions = [{"field": "folder", "op": "starts_with", "value": "Kunder/Acme"},
                  {"any": [{"field": "ext", "op": "in", "value": [".pdf", ".docx"]},
                           {"field": "label", "op": "is", "value": "doctype=contract"}]}]

The list is ANDed. A condition ``{"any": [alternative, …]}`` is an OR group: each alternative is one condition or a
list of conditions (ANDed), and may hold an ``any`` of its own. Values are always passed as parameters, never put in
the SQL. An empty list matches nothing.

**Bounds**, checked by one iterative walk before anything is counted or compiled: groups nest at most MAX_DEPTH deep
(the top list is depth 0, each ``any`` adds one), a rule has at most MAX_LEAVES plain conditions, and the walk looks
at no more than MAX_NODES lists, groups and conditions in all (a list's length is counted before its items are
taken). Anything malformed, too deep, too wide or cyclic is a RuleError, never a RecursionError or TypeError, and
never quietly skipped: ``leaves`` and ``compile_conditions`` both refuse it. Only a list is a rule (``[]`` matches
nothing); ``leaves`` also takes a single condition object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

TEXT_OPS = {"is", "is_not", "contains", "not_contains", "starts_with", "ends_with", "matches", "in"}
OPS = TEXT_OPS | {"is_set", "is_true", "is_false", "before", "after", "gt", "lt", "search"}
MAX_DEPTH = 8
MAX_LEAVES = 200
MAX_NODES = 2000


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Column:
    """A field that is an expression over the product's row (``m.subject``, ``i.ext``)."""
    expr: str


@dataclass(frozen=True)
class Exists:
    """A field that lives in another table: true when one row there matches. ``where`` holds ``{cmp}``."""
    table: str
    where: str
    expr: str


Custom = Callable[[str, object, list], str]
"""A field the product compiles itself: (op, value, params) -> SQL, appending its values to params."""

Fields = dict[str, "Column | Exists | Custom"]


def like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def cmp(expr: str, op: str, value, params: list) -> str:
    """A text comparison, case-insensitive."""
    if op == "is":
        params.append(str(value).lower())
        return f"lower({expr}) = %s"
    if op == "is_not":
        params.append(str(value).lower())
        return f"lower({expr}) <> %s"
    if op in ("contains", "not_contains"):
        params.append(f"%{like(str(value))}%")
        return f"{expr} {'not ' if op == 'not_contains' else ''}ilike %s"
    if op == "starts_with":
        params.append(f"{like(str(value))}%")
        return f"{expr} ilike %s"
    if op == "ends_with":
        params.append(f"%{like(str(value))}")
        return f"{expr} ilike %s"
    if op == "matches":
        params.append(str(value))
        return f"{expr} ~* %s"
    if op == "in":
        if not isinstance(value, list) or not value:
            raise RuleError("'in' needs a list")
        if len(value) > MAX_NODES:
            raise RuleError(f"'in' takes at most {MAX_NODES} values")
        params.append([str(v).lower() for v in value])
        return f"lower({expr}) = any(%s)"
    raise RuleError(f"operator {op!r} does not apply to text")


def compile_conditions(conditions: list, fields: Fields) -> tuple[str, list]:
    """(where-clause, params) for a condition list over the product's fields. Only a list is a rule: [] matches
    nothing; None, {}, a string or anything else is a RuleError."""
    if not isinstance(conditions, list):
        raise RuleError("a rule is a list of conditions")
    if not conditions:
        return "false", []
    _walk(conditions)   # bounded: the compiler below only ever sees a checked, shallow rule
    params: list = []
    return _all(conditions, fields, params, 0), params


def _all(conditions, fields: Fields, params: list, depth: int) -> str:
    if not isinstance(conditions, list) or not conditions:
        raise RuleError("a group of conditions needs at least one condition")
    if depth > MAX_DEPTH:
        raise RuleError("conditions are nested too deep")
    return " and ".join(f"({_one(c, fields, params, depth)})" for c in conditions)


def _one(c, fields: Fields, params: list, depth: int) -> str:
    if not isinstance(c, dict):
        raise RuleError("a condition is a {field, op, value} object or an {any: [...]} group")
    if "any" in c:
        alts = c["any"]
        if not isinstance(alts, list) or not alts:
            raise RuleError("'any' needs a list of alternatives")
        return " or ".join(f"({_all(a if isinstance(a, list) else [a], fields, params, depth + 1)})" for a in alts)
    field, op, value = c.get("field"), c.get("op"), c.get("value")
    if not isinstance(op, str) or op not in OPS:
        raise RuleError(f"unknown operator {op!r}")
    spec = fields.get(field) if isinstance(field, str) else None
    if spec is None:
        raise RuleError(f"unknown field {field!r}; known: {', '.join(sorted(fields))}")
    if isinstance(spec, Exists):
        inner = "true" if op == "is_set" else cmp(spec.expr, op, value, params)
        return f"exists (select 1 from {spec.table} where {spec.where.format(cmp=inner)})"
    if isinstance(spec, Column):
        expr = spec.expr
        if op == "is_true":
            return f"{expr} is true"
        if op == "is_false":
            return f"{expr} is not true"
        if op == "is_set":
            return f"nullif({expr}::text, '') is not null"
        if op in ("before", "after", "gt", "lt"):
            if isinstance(value, (list, tuple, set, dict)):
                raise RuleError(f"{op!r} compares with one value, not a collection")
            params.append(value)
            return f"{expr} {'<' if op in ('before', 'lt') else '>'} %s"
        if op == "search":
            raise RuleError(f"{field} does not support 'search'")
        return cmp(expr, op, value, params)
    return spec(op, value, params)


def leaves(conditions) -> list[dict]:
    """Every plain {field, op, value} condition, however deep in "any" groups, in order. Takes a rule (a list; []
    gives []) or a single condition object. The same checked, bounded walk the compiler uses: anything malformed
    (a bare list in a list, a number, an ``any`` that is not a non-empty list, a condition without a text field or
    a known operator) or past MAX_DEPTH, MAX_LEAVES or MAX_NODES is a RuleError, never skipped."""
    if isinstance(conditions, dict):
        return _walk([conditions])
    if not isinstance(conditions, list):
        raise RuleError("a rule is a list of conditions")
    return _walk(conditions) if conditions else []


def _walk(conditions: list) -> list[dict]:
    """The plain conditions of a non-empty rule, in order, by an iterative walk of at most MAX_NODES steps. Only the
    shape the compiler takes: a non-empty list of condition objects; ``any`` a non-empty list whose alternatives are
    a condition or a non-empty list of them; a plain condition has a text ``field`` and a known ``op``."""
    if not isinstance(conditions, list) or not conditions:
        raise RuleError("a rule is a non-empty list of conditions")
    shape = "a condition is a {field, op, value} object or an {any: [...]} group"
    out: list[dict] = []
    seen = 0
    # (thing, depth, is_group): a group is a list of conditions at a depth; anything else is one condition.
    stack: list[tuple[object, int, bool]] = [(conditions, 0, True)]

    def take(n: int) -> None:
        nonlocal seen
        seen += n
        if seen > MAX_NODES:
            raise RuleError(f"a rule is too large (more than {MAX_NODES} parts)")

    while stack:
        c, depth, group = stack.pop()
        if depth > MAX_DEPTH:
            raise RuleError("conditions are nested too deep")
        if group:
            if not c:
                raise RuleError("a group of conditions needs at least one condition")
            take(len(c))   # counted before a single item is pushed: a huge list stops here
            for x in reversed(c):
                if not isinstance(x, dict):
                    raise RuleError(shape)
                stack.append((x, depth, False))
            continue
        if "any" in c:
            alts = c["any"]
            if not isinstance(alts, list) or not alts:
                raise RuleError("'any' needs a list of alternatives")
            take(len(alts))
            for a in reversed(alts):
                stack.append((a if isinstance(a, list) else [a], depth + 1, True))
            continue
        if not isinstance(c.get("field"), str) or not isinstance(c.get("op"), str) or c["op"] not in OPS:
            raise RuleError("a condition needs a text field and a known operator")
        out.append(c)
        if len(out) > MAX_LEAVES:
            raise RuleError(f"a rule has at most {MAX_LEAVES} conditions")
    return out


def check(conditions: list, fields: Fields) -> list[dict]:
    """Validate a rule without a database: raises RuleError with the reason, else returns the leaves."""
    if not isinstance(conditions, list) or not conditions:
        raise RuleError("a rule is a non-empty list of conditions")
    compile_conditions(conditions, fields)
    return leaves(conditions)
