"""The note store: one schema for the owner's notes, whichever product keeps them.

The schema is talos's ``owner_note`` and ``note_action`` (talos migration 044), column for column. A product that
keeps notes on its own things creates the same two tables (``ddl``) with its own list of target kinds, and uses the
operations here; talos keeps its migration and is held to the same columns by ``check_schema``.

Notes on files live in urania's database, in these tables, and talos reads and writes them through urania: one store,
one schema, both sides (the bridge's one deliberate shared store).

What a note is on is resolved by the product (``Target``): xenophon never looks inside a product's tables. The
rules kept here are the ones talos shipped:

- one note per target (``target_kind`` + ``target_key``);
- an empty note is no note; emptied after actions were done on it, it is kept, withdrawn, with its history;
- a changed note is open again, so an agent acts on it anew;
- an agent may write the summary and the actions, never the text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .words import DEFAULT, NoteError, Vocabulary, clean

STATUSES = ("open", "done", "withdrawn")
SUMMARY_MAX = 2_000
_KIND = re.compile(r"^[a-z][a-z_]{0,30}$")

COLUMNS = {
    "owner_note": ("id", "target_kind", "target_id", "target_key", "stable", "body", "understood", "status",
                   "summary", "summary_by", "created_at", "updated_at", "done_at"),
    "note_action": ("id", "note_id", "kind", "scope", "payload", "made", "reason", "by", "created_at", "undone_at",
                    "undone_by"),
}


@dataclass(frozen=True)
class Target:
    """What a note is on, as the product resolved it: its kind, its row id where it has one, a stable key (one note
    per kind and key) and what finds it again after a rebuild (a hash, a Message-ID, an inode)."""
    kind: str
    key: str
    id: int | None = None
    stable: dict = field(default_factory=dict)


def ddl(kinds: tuple[str, ...]) -> str:
    """The two tables, for a product whose notes go on these kinds of things. Idempotent."""
    if not kinds or not all(_KIND.match(k) for k in kinds):
        raise NoteError("target kinds are short lower-case words")
    allowed = ", ".join(f"'{k}'" for k in kinds)
    return f"""
create table if not exists owner_note (
    id           bigserial primary key,
    target_kind  text not null check (target_kind in ({allowed})),
    target_id    bigint,
    target_key   text not null,
    stable       jsonb not null default '{{}}',
    body         text not null default '' check (length(body) <= 20000),
    understood   jsonb not null default '{{}}',
    status       text not null default 'open' check (status in ('open', 'done', 'withdrawn')),
    summary      text,
    summary_by   text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    done_at      timestamptz,
    unique (target_kind, target_key)
);
create index if not exists owner_note_open_idx on owner_note (status, updated_at desc);
create index if not exists owner_note_target_idx on owner_note (target_kind, target_id);

create table if not exists note_action (
    id          bigserial primary key,
    note_id     bigint not null references owner_note (id) on delete cascade,
    kind        text not null,
    scope       text,
    payload     jsonb not null,
    made        jsonb not null default '{{}}',
    reason      text,
    by          text not null,
    created_at  timestamptz not null default now(),
    undone_at   timestamptz,
    undone_by   text
);
create index if not exists note_action_note_idx on note_action (note_id, id);
"""


def check_schema(conn) -> list[str]:
    """What is missing from a database's note tables, compared with the one schema; empty when they match."""
    problems = []
    for table, cols in COLUMNS.items():
        have = {r[0] if not isinstance(r, dict) else r["column_name"] for r in conn.execute(
            "select column_name from information_schema.columns where table_name = %s and table_schema = current_schema()",
            (table,)).fetchall()}
        if not have:
            problems.append(f"no table {table}")
            continue
        problems += [f"{table}.{c} is missing" for c in cols if c not in have]
    return problems


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def _one(conn, sql: str, params=()) -> dict | None:
    return conn.execute(sql, params).fetchone()


def _row(conn, note_id: int, *, lock: bool = False) -> dict:
    n = _one(conn, "select * from owner_note where id = %s" + (" for update" if lock else ""), (note_id,))
    if not n:
        raise NoteError(f"no note {note_id}")
    return n


def save(conn, target: Target, body, *, vocabulary: Vocabulary = DEFAULT) -> dict | None:
    """The owner's note on a target, written or changed; None when it is emptied away. Only the owner's own
    surfaces (a signed-in web page) call this: an agent never writes a note's text."""
    text = clean(body)
    with conn.transaction():
        n = _one(conn, "select * from owner_note where target_kind = %s and target_key = %s for update",
                 (target.kind, target.key))
        if not text:
            if not n:
                return None
            if _one(conn, "select 1 from note_action where note_id = %s limit 1", (n["id"],)):
                conn.execute("update owner_note set body = '', understood = '{}', status = 'withdrawn',"
                             " updated_at = now() where id = %s", (n["id"],))
                return get(conn, n["id"], vocabulary=vocabulary)
            conn.execute("delete from owner_note where id = %s", (n["id"],))
            return None
        if n and n["body"] == text and n["status"] != "withdrawn":
            return get(conn, n["id"], vocabulary=vocabulary)
        understood = _j(vocabulary.understand(text))
        if n:
            conn.execute("update owner_note set body = %s, understood = %s::jsonb, status = 'open', done_at = null,"
                         " target_id = %s, stable = %s::jsonb, updated_at = now() where id = %s",
                         (text, understood, target.id, _j(target.stable), n["id"]))
            nid = n["id"]
        else:
            nid = _one(conn, "insert into owner_note (target_kind, target_id, target_key, stable, body, understood)"
                             " values (%s, %s, %s, %s::jsonb, %s, %s::jsonb) returning id",
                       (target.kind, target.id, target.key, _j(target.stable), text, understood))["id"]
    return get(conn, nid, vocabulary=vocabulary)


def get(conn, note_id: int, *, vocabulary: Vocabulary = DEFAULT) -> dict:
    n = dict(_row(conn, note_id))
    n["chips"] = vocabulary.labels(n["understood"])
    n["actions"] = conn.execute("select id, kind, scope, payload, made, reason, by, created_at, undone_at, undone_by"
                                " from note_action where note_id = %s order by id", (note_id,)).fetchall()
    return n


def find(conn, kind: str, key: str, *, vocabulary: Vocabulary = DEFAULT) -> dict | None:
    """The note on one target, or None."""
    n = _one(conn, "select id from owner_note where target_kind = %s and target_key = %s", (kind, key))
    return get(conn, n["id"], vocabulary=vocabulary) if n else None


def marks(conn, kind: str, keys: list[str]) -> dict[str, str]:
    """Which of the shown targets have a live note, and its status: {key: 'open' | 'done'}."""
    keys = [str(k) for k in list(keys)[:500]]
    if not keys:
        return {}
    rows = conn.execute("select target_key, status from owner_note where target_kind = %s and target_key = any(%s)"
                        " and status in ('open', 'done')", (kind, keys)).fetchall()
    return {r["target_key"]: r["status"] for r in rows}


def counts(conn) -> dict:
    rows = conn.execute("select status, count(*) as n from owner_note group by status").fetchall()
    return {s: 0 for s in STATUSES} | {r["status"]: r["n"] for r in rows}


def listing(conn, *, status: str = "open", kind: str | None = None, limit: int = 100,
            vocabulary: Vocabulary = DEFAULT) -> list[dict]:
    """Notes, newest change first, each with its actions."""
    if status not in (*STATUSES, "all"):
        raise NoteError("status is open, done, withdrawn or all")
    where, params = ["true"], []
    if status != "all":
        where.append("status = %s")
        params.append(status)
    if kind:
        where.append("target_kind = %s")
        params.append(kind)
    rows = conn.execute("select id from owner_note where " + " and ".join(where)
                        + " order by updated_at desc, id desc limit %s", [*params, max(1, min(limit, 500))]).fetchall()
    return [get(conn, r["id"], vocabulary=vocabulary) for r in rows]


def set_status(conn, note_id: int, status: str, *, vocabulary: Vocabulary = DEFAULT) -> dict:
    """The owner marks a note done, or opens it again."""
    if status not in ("open", "done"):
        raise NoteError("a note is open or done")
    with conn.transaction():
        n = _row(conn, note_id, lock=True)
        if n["status"] == "withdrawn":
            raise NoteError("an emptied note has nothing to do; write it again")
        conn.execute("update owner_note set status = %s, done_at = case when %s = 'done' then now() end,"
                     " updated_at = now() where id = %s", (status, status, note_id))
    return get(conn, note_id, vocabulary=vocabulary)


def answer(conn, note_id: int, summary: str, *, by: str, done: bool = False,
           vocabulary: Vocabulary = DEFAULT) -> dict:
    """An agent's answer on a note: what it did, or a question back. ``done`` takes it off the open list."""
    summary = clean(summary, SUMMARY_MAX)
    if not summary:
        raise NoteError("an answer says what was done, or asks something")
    with conn.transaction():
        n = _row(conn, note_id, lock=True)
        if n["status"] == "withdrawn":
            raise NoteError("this note was emptied by the owner")
        conn.execute("update owner_note set summary = %s, summary_by = %s,"
                     " status = case when %s then 'done' else status end,"
                     " done_at = case when %s then now() else done_at end where id = %s",
                     (summary, by, done, done, note_id))
    return get(conn, note_id, vocabulary=vocabulary)


def record_action(conn, note_id: int, *, kind: str, payload: dict, by: str, scope: str | None = None,
                  made: dict | None = None, reason: str | None = None) -> int:
    """One thing done because of a note, with what it made (and so how to take it back)."""
    _row(conn, note_id)
    return _one(conn, "insert into note_action (note_id, kind, scope, payload, made, reason, by)"
                      " values (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s) returning id",
                (note_id, kind, scope, _j(payload), _j(made or {}), reason, by))["id"]


def mark_undone(conn, action_id: int, *, by: str) -> bool:
    """Record that an action was taken back (the product does the taking back)."""
    r = conn.execute("update note_action set undone_at = now(), undone_by = %s where id = %s and undone_at is null",
                     (by, action_id))
    return r.rowcount == 1
