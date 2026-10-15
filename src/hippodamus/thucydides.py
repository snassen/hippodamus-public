"""Thucydides: what happened, in order, since you last looked.

Thucydides wrote the war down as it happened, season by season. A product that owns things (urania owns files) keeps
a feed of what happened to them: one row per event, numbered in order. Another product (talos's watchers) remembers
the number it last read, its cursor, and asks for what came after.

The feed is append-only. An event says what it is about and what kind it is; the product adds what it needs in
``data``.

**Writing: one writer at a time (0.3.0).** A bigserial number orders allocation, not commit: two writers at once
could commit 2 before 1, and a reader that had moved its cursor to 2 would never see 1. So events are written only
inside ``writer``, which serializes every writer of one feed table:

    with thucydides.writer(conn, table="change_event") as w:
        conn.execute("update items set path = %s where id = %s", ...)   # the product's own change
        w.record("moved", item_id, {"from": old, "to": new})            # and its event, in the same transaction

``writer`` must be entered on a connection with no transaction open (psycopg in autocommit, or idle): it begins the
transaction itself, at READ COMMITTED, and its first statement takes a transaction-scoped PostgreSQL advisory lock
keyed on the feed table's own OID (``to_regclass``, so the same table under any search_path or schema-qualified name
is the same lock, and two feed tables never share one). Only then does the product change its rows and record its
events; the block commits on a clean exit and rolls back on an exception, and the lock is held until that commit.
The lock is always taken first, before any product row lock, so writers cannot deadlock on each other through it.

``record`` refuses (FeedError) outside a writer of the same connection and table, in a transaction other than the
writer's own (after a raw COMMIT inside the block, say), after the block has ended, and even when the caller took
the advisory lock by hand: the protocol is the context, not the lock alone. A writer object whose block has ended
is never used again, even while a newer writer runs on the same connection. A writer inside an open transaction or
inside another writer on the same connection is refused too.

The writer pins the table it locked: every insert goes to that table by its resolved, quoted schema and name, and a
``record`` whose table name now resolves to another table (a ``set local search_path`` inside the block, say) is a
FeedError rather than an event in the wrong feed. Renaming or dropping the feed table inside the block makes
``record`` fail; the block then rolls back.

Since a writer can only take a number once every earlier writer has committed or rolled back, numbers become
visible in order. A rolled-back writer leaves a gap, never a late arrival, so a cursor never skips an event. This
holds only if every producer of a feed table writes through ``writer``: a raw insert breaks it.

**Reading.** ``events`` is the feed itself, unfiltered and without a join: every event after the cursor, including
those about a thing that no longer matches anything. ``since`` is the older filtered read, unchanged: events joined
to the thing *as it is now*, filtered by ``where``. It answers "what happened to things that match now"; it is not
a membership feed (a thing that moved out of a filter disappears from it, its move included). With the reference
table's ``on delete cascade`` (``ddl``), a thing's events go when the thing is deleted from the product's table.

**Watching membership** (a binder's files, say) is the product's own, with its own durable state: the set of
things currently in, beside the cursor, in its own tables. One poll:

1. Read ``events(conn, cursor)`` (unfiltered).
2. The touched things are the page's distinct item ids. Ask the owning product which of them match the rule *now*
   (a gone thing or a deleted row does not match); drop the human exclusions.
3. Entered = matching touched things not in the stored set; left = stored members among the touched things that
   no longer match. Preserve all untouched members. Store the updated set, the transitions (as proposals or
   notifications) and the page's cursor in one transaction, so a restart replays the page and finds nothing new to
   say (dedup is by state, not by event id).
4. When the rule itself changes, or the exclusions do: read the feed's head first, then recompute the whole set,
   store the differences and that head as the cursor. Events after it are re-evaluated by later polls.

The producer records an event (``changed`` at least) whenever a field a rule can see changes, labels included; a
watcher learns of nothing else. Transitions are net between polls: a thing that went in and out again between two
polls is no transition; the events still say what happened.
"""

from __future__ import annotations

import json

KINDS = ("new", "changed", "moved", "gone", "back")
"""new: first seen · changed: new content (a new version) · moved: new path, same thing · gone: no longer there ·
back: there again."""
MAX_PAGE = 1000
LOCK_NAMESPACE = 0x54687563
"""The first key of every feed's advisory lock ("Thuc"); the second is the feed table's OID."""


class FeedError(ValueError):
    pass


def _name(table: str) -> str:
    """A table name: a plain word, or schema.word."""
    parts = table.split(".") if isinstance(table, str) else []
    if not 1 <= len(parts) <= 2 or not all(p and p.replace("_", "").isalnum() for p in parts):
        raise FeedError("table names are plain words")
    return table


def ddl(table: str = "change_event", ref_table: str = "items") -> str:
    """The feed table, keyed to the product's own table of things. Idempotent."""
    for name in (table, ref_table):
        if not name.replace("_", "").isalnum():
            raise FeedError("table names are plain words")
    return f"""
create table if not exists {table} (
    id         bigserial primary key,
    at         timestamptz not null default now(),
    kind       text not null check (kind in ({", ".join(f"'{k}'" for k in KINDS)})),
    item_id    bigint not null references {ref_table}(id) on delete cascade,
    data       jsonb not null default '{{}}'
);
create index if not exists {table}_item_idx on {table} (item_id, id);
"""


_writers: dict[int, "Writer"] = {}   # id(conn) -> its writer, while the block runs


class Writer:
    """The one writer of a feed table, for one transaction. Made by ``writer``; see the module."""

    def __init__(self, conn, table: str):
        self.conn, self.table = conn, _name(table)
        self.oid: int | None = None
        self.target: str | None = None
        self._xid: str | None = None
        self._tx = None

    def __enter__(self) -> "Writer":
        conn = self.conn
        if id(conn) in _writers:
            raise FeedError("a feed writer is already open on this connection")
        if conn.info.transaction_status.name != "IDLE":
            raise FeedError("a feed writer begins its own transaction: enter it with none open")
        self._tx = conn.transaction()
        self._tx.__enter__()
        try:
            conn.execute("set transaction isolation level read committed")
            row = conn.execute(
                "select c.oid::bigint as oid, c.relkind::text as relkind, n.nspname as schema, c.relname as name,"
                " (select count(*) from pg_attribute a where a.attrelid = c.oid and not a.attisdropped"
                "  and a.attname in ('id', 'kind', 'item_id', 'data')) as cols"
                " from pg_class c join pg_namespace n on n.oid = c.relnamespace"
                " where c.oid = to_regclass(%s)", [self.table]).fetchone()
            if row is None or row["relkind"] not in ("r", "p") or row["cols"] != 4:
                raise FeedError(f"{self.table} is not a feed table")
            self.oid = int(row["oid"])
            # Every insert goes to this exact table, schema-qualified and quoted: a later search_path cannot move it.
            self.target = f"{_quote(row['schema'])}.{_quote(row['name'])}"
            conn.execute("select pg_advisory_xact_lock(%s::int4, %s::int4)", [LOCK_NAMESPACE, _int4(self.oid)])
            self._xid = conn.execute("select txid_current()::text as x").fetchone()["x"]
        except BaseException as e:
            self._tx.__exit__(type(e), e, e.__traceback__)
            raise
        _writers[id(conn)] = self
        return self

    def __exit__(self, *exc):
        try:
            return self._tx.__exit__(*exc)
        finally:
            _writers.pop(id(self.conn), None)
            self._xid = None

    def record(self, kind: str, item_id: int, data: dict | None = None) -> int:
        if _writers.get(id(self.conn)) is not self:   # an ended (or other) writer object is never used again
            raise FeedError("this writer's block has ended: record inside thucydides.writer(conn) only")
        return record(self.conn, kind, item_id, data, table=self.table)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _int4(oid: int) -> int:
    """An OID (unsigned 32 bits) as the signed int4 the two-key advisory lock takes."""
    return oid - (1 << 32) if oid >= (1 << 31) else oid


def writer(conn, *, table: str = "change_event") -> Writer:
    """The context every event of ``table`` is written in: ``with writer(conn) as w: …; w.record(…)``."""
    return Writer(conn, table)


def record(conn, kind: str, item_id: int, data: dict | None = None, *, table: str = "change_event") -> int:
    """Append one event, inside ``writer(conn, table=table)`` only (FeedError otherwise)."""
    if kind not in KINDS:
        raise FeedError(f"an event is one of {', '.join(KINDS)}")
    w = _writers.get(id(conn))
    if w is None or w.conn is not conn or w._xid is None:
        raise FeedError("an event is recorded inside thucydides.writer(conn) only")
    table = _name(table)
    # One statement: the same transaction as the writer's, the name given still meaning the table it locked (not
    # another one a changed search_path, a rename or a drop now points at), and the insert into that locked table.
    row = conn.execute(
        f"insert into {w.target} (kind, item_id, data) select %s, %s, %s::jsonb"
        " where txid_current()::text = %s and to_regclass(%s) = %s::bigint::oid returning id",
        (kind, item_id, json.dumps(data or {}, default=str), w._xid, table, w.oid)).fetchone()
    if row is None:
        now = conn.execute("select txid_current()::text as x, to_regclass(%s)::oid::bigint as oid",
                           [table]).fetchone()
        if now["x"] != w._xid:
            raise FeedError("the writer's transaction has ended: record inside the same transaction")
        raise FeedError(f"{table} is not the feed table this writer holds ({w.target}); record into that one only")
    return row["id"]


def _page(conn, cursor: int, limit: int, table: str, select) -> dict:
    limit = max(1, min(int(limit), MAX_PAGE))
    # The head is read first and bounds the page, so an event written meanwhile is left for the next call. Under the
    # writer protocol every number up to a visible head is committed or abandoned for good.
    head = conn.execute(f"select coalesce(max(id), 0) as n from {table}").fetchone()["n"]
    rows = select(head, limit)
    # With a filter, events that did not match still move the cursor on, up to the head looked at.
    nxt = rows[-1]["id"] if len(rows) == limit else max(int(cursor), head)
    return {"events": rows, "cursor": nxt, "more": len(rows) == limit}


def events(conn, cursor: int, *, limit: int = 200, table: str = "change_event") -> dict:
    """Every event after ``cursor``, oldest first, unfiltered and without a join to the things: {events, cursor,
    more}. The read for a watcher that keeps its own membership (see the module)."""
    table = _name(table)
    return _page(conn, cursor, limit, table, lambda head, limit: conn.execute(
        f"select e.id, e.at, e.kind, e.item_id, e.data from {table} e where e.id > %s and e.id <= %s"
        " order by e.id limit %s", [int(cursor), head, limit]).fetchall())


def since(conn, cursor: int, *, limit: int = 200, where: str = "true", params: list | None = None,
          table: str = "change_event", item_alias: str = "i", item_table: str = "items") -> dict:
    """Events after ``cursor``, oldest first, joined to the thing as ``item_alias`` so ``where`` can filter on it.
    Returns {events, cursor}: the new cursor is the last event read, or the old one when nothing came. The filter
    sees the thing as it is now (see the module): a current-match read, not a membership feed."""
    return _page(conn, cursor, limit, table, lambda head, limit: conn.execute(
        f"select e.id, e.at, e.kind, e.item_id, e.data from {table} e join {item_table} {item_alias}"
        f" on {item_alias}.id = e.item_id where e.id > %s and e.id <= %s and ({where}) order by e.id limit %s",
        [int(cursor), head, *(params or []), limit]).fetchall())
