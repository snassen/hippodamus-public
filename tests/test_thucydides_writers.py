"""Thucydides's writer protocol on real PostgreSQL connections: one writer of a feed at a time, so numbers become
visible in order and a cursor never skips an event. A blocked writer is proven blocked by pg_locks, not by waiting.
Invented items only."""

import threading
import time

import pytest

from hippodamus import thucydides as th


@pytest.fixture
def feed(db):
    db.execute("create table items (id bigserial primary key, root text, label text,"
               " gone boolean not null default false)")
    db.execute(th.ddl())
    db.execute("insert into items (root) select 'a' from generate_series(1, 6)")
    return db


def _pid(conn) -> int:
    return conn.execute("select pg_backend_pid() as p").fetchone()["p"]


def _until(check, what: str, deadline: float = 10.0):
    """Poll an observable condition (a lock row, an event set); the condition is the proof, not the wait."""
    end = time.monotonic() + deadline
    while not check():
        if time.monotonic() > end:
            raise AssertionError(f"never saw: {what}")
        time.sleep(0.01)


def _waiting_on_feed_lock(observer, pid: int, oid: int) -> bool:
    row = observer.execute(
        "select l.granted, a.wait_event_type from pg_locks l join pg_stat_activity a on a.pid = l.pid"
        " where l.pid = %s and l.locktype = 'advisory' and l.classid::bigint = %s and l.objid::bigint = %s"
        " and l.objsubid = 2",
        [pid, th.LOCK_NAMESPACE, oid]).fetchone()
    return bool(row) and row["granted"] is False and row["wait_event_type"] == "Lock"


def _holds_feed_lock(observer, pid: int, oid: int) -> bool:
    return bool(observer.execute(
        "select 1 from pg_locks where pid = %s and locktype = 'advisory' and classid::bigint = %s"
        " and objid::bigint = %s and objsubid = 2 and granted", [pid, th.LOCK_NAMESPACE, oid]).fetchone())


class Second:
    """A second writer on its own connection and thread: enters, records, then waits for leave() to commit."""

    def __init__(self, conn, item_id, *, table="change_event", mutate=None):
        self.conn, self.item_id, self.table, self.mutate = conn, item_id, table, mutate
        self.pid = _pid(conn)
        self.recorded, self.go, self.error = threading.Event(), threading.Event(), None
        self.id = None
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        try:
            with th.writer(self.conn, table=self.table) as w:
                if self.mutate:
                    self.mutate(self.conn)
                self.id = w.record("changed", self.item_id)
                self.recorded.set()
                self.go.wait(10)
        except Exception as e:   # surfaced by leave()
            self.error = e
            self.recorded.set()

    def leave(self):
        self.go.set()
        self.t.join(10)
        if self.error:
            raise self.error


def test_a_second_writer_waits_for_the_first_to_commit_so_a_cursor_never_skips(feed, connect):
    a_conn, b_conn, reader = connect(), connect(), connect()
    with th.writer(a_conn) as a:
        first = a.record("changed", 1)
        b = Second(b_conn, 2)
        _until(lambda: _waiting_on_feed_lock(reader, b.pid, a.oid), "the second writer waiting on the feed lock")
        assert _holds_feed_lock(reader, _pid(a_conn), a.oid) and not b.recorded.is_set()
        page = th.events(reader, 0)
        assert page == {"events": [], "cursor": 0, "more": False}        # nothing visible: the cursor stays put
    # A has committed; only now can B take a number, and it is higher than A's.
    _until(b.recorded.is_set, "the second writer recording")
    page = th.events(reader, 0)
    assert [e["id"] for e in page["events"]] == [first] and page["cursor"] == first
    b.leave()
    later = th.events(reader, page["cursor"])
    assert [e["id"] for e in later["events"]] == [b.id] and b.id > first


def test_the_lower_number_is_never_committed_after_a_reader_passed_a_higher_one(feed, connect):
    # The H-03 order: A takes a number and holds; B must not be able to take and commit a higher one meanwhile.
    a_conn, b_conn, reader = connect(), connect(), connect()
    seen: list[int] = []
    cursor = 0
    with th.writer(a_conn) as a:
        low = a.record("new", 1)
        b = Second(b_conn, 2)
        _until(lambda: _waiting_on_feed_lock(reader, b.pid, a.oid), "B blocked behind A")
        page = th.events(reader, cursor)
        seen += [e["id"] for e in page["events"]]
        cursor = page["cursor"]
    b.leave()
    page = th.events(reader, cursor)
    seen += [e["id"] for e in page["events"]]
    assert seen == [low, b.id] and low < b.id


def test_the_feed_lock_comes_before_the_products_row_locks(feed, connect):
    # Both writers change the same item: B waits on the feed lock, never holding the row A needs.
    a_conn, b_conn, reader = connect(), connect(), connect()
    with th.writer(a_conn) as a:
        a_conn.execute("update items set label = 'x' where id = 3")
        b = Second(b_conn, 3, mutate=lambda c: c.execute("update items set label = 'y' where id = 3"))
        _until(lambda: _waiting_on_feed_lock(reader, b.pid, a.oid), "B waiting on the feed lock, not the row")
        a_conn.execute("update items set root = 'b' where id = 3")    # A can still take its own rows
        a.record("changed", 3)
    b.leave()
    assert reader.execute("select label, root from items where id = 3").fetchone() == {"label": "y", "root": "b"}


def test_a_rolled_back_writer_leaves_a_gap_and_takes_its_product_change_with_it(feed, connect):
    conn, reader = connect(), connect()
    with th.writer(conn) as w:
        one = w.record("new", 1)
    with pytest.raises(RuntimeError):
        with th.writer(conn) as w:
            conn.execute("update items set root = 'z' where id = 2")
            w.record("moved", 2)
            raise RuntimeError("the product changed its mind")
    with th.writer(conn) as w:
        three = w.record("new", 3)
    assert three > one + 1                                                # the gap
    assert reader.execute("select root from items where id = 2").fetchone()["root"] == "a"
    page = th.events(reader, one)
    assert [e["id"] for e in page["events"]] == [three] and page["cursor"] == three
    assert not _holds_feed_lock(reader, _pid(conn), w.oid)                 # released with the transaction


def test_two_feeds_do_not_wait_for_each_other(feed, connect):
    feed.execute(th.ddl("other_event"))
    a_conn, b_conn, reader = connect(), connect(), connect()
    with th.writer(a_conn) as a:
        a.record("new", 1)
        b = Second(b_conn, 2, table="other_event")
        _until(b.recorded.is_set, "the other feed's writer going ahead")
        assert b.error is None
        b.leave()


def test_the_lock_is_the_tables_own_whatever_the_search_path_or_name(feed, connect):
    feed.execute("create schema feedspace")
    feed.execute("create table feedspace.items (id bigserial primary key)")
    feed.execute("create table feedspace.change_event (id bigserial primary key, at timestamptz not null default now(),"
                 " kind text not null, item_id bigint not null references feedspace.items(id),"
                 " data jsonb not null default '{}')")
    feed.execute("insert into feedspace.items default values")
    a_conn, b_conn, reader = connect(), connect(), connect()
    b_conn.execute("set search_path = feedspace, public")
    with th.writer(a_conn, table="feedspace.change_event") as a:
        public_oid = reader.execute("select 'public.change_event'::regclass::oid::bigint as o").fetchone()["o"]
        assert a.oid != public_oid
        b = Second(b_conn, 1)                       # "change_event" here resolves to feedspace.change_event
        _until(lambda: _waiting_on_feed_lock(reader, b.pid, a.oid), "the same table under another name waits")
        a.record("new", 1)
    b.leave()
    assert [e["item_id"] for e in th.events(reader, 0, table="feedspace.change_event")["events"]] == [1, 1]
    assert th.events(reader, 0)["events"] == []                           # the public feed is another feed


def test_record_is_refused_outside_a_writer_even_with_the_lock_taken_by_hand(feed, connect):
    conn = connect()
    with pytest.raises(th.FeedError, match="inside thucydides.writer"):
        th.record(conn, "new", 1)
    oid = conn.execute("select 'change_event'::regclass::oid::bigint as o").fetchone()["o"]
    with conn.transaction():
        conn.execute("select pg_advisory_xact_lock(%s::int4, %s::int4)", [th.LOCK_NAMESPACE, oid])
        with pytest.raises(th.FeedError, match="inside thucydides.writer"):
            th.record(conn, "new", 1)
    with th.writer(conn) as w:
        pass
    with pytest.raises(th.FeedError):                                     # the block has ended
        w.record("new", 1)
    assert th.events(conn, 0)["events"] == []


def test_a_writer_is_entered_with_no_transaction_open_and_never_twice(feed, connect):
    conn = connect()
    with conn.transaction():
        with pytest.raises(th.FeedError, match="none open"):
            th.writer(conn).__enter__()
    with th.writer(conn):
        with pytest.raises(th.FeedError, match="already open"):
            th.writer(conn).__enter__()
    plain = connect(autocommit=False)
    plain.execute("select 1")                                             # an implicit transaction is open
    with pytest.raises(th.FeedError, match="none open"):
        th.writer(plain).__enter__()
    plain.rollback()
    with th.writer(plain) as w:                                           # idle again: the writer begins and commits
        w.record("new", 1)
    assert len(th.events(conn, 0)["events"]) == 1


def test_a_writer_does_not_survive_a_raw_commit_inside_it(feed, connect):
    conn = connect()
    with pytest.raises(Exception):
        with th.writer(conn) as w:
            conn.execute("commit")
            with pytest.raises(th.FeedError, match="transaction has ended"):
                w.record("new", 1)
            raise RuntimeError("leave")
    assert th.events(conn, 0)["events"] == []


def test_the_wrong_table_is_refused(feed, connect):
    conn = connect()
    for table in ("items", "no_such_feed", "change_event; drop table items", ""):
        with pytest.raises(th.FeedError):
            th.writer(conn, table=table).__enter__()
        assert conn.info.transaction_status.name == "IDLE"                # nothing left open
    feed.execute(th.ddl("other_event"))
    with th.writer(conn) as w:
        with pytest.raises(th.FeedError, match="not the feed table this writer holds"):
            th.record(conn, "new", 1, table="other_event")
        w.record("new", 1)
        th.record(conn, "new", 1, table="public.change_event")             # the same table, qualified: fine
    assert len(th.events(conn, 0)["events"]) == 2 and th.events(conn, 0, table="other_event")["events"] == []


def test_a_writer_whose_block_ended_is_never_used_again_even_inside_a_newer_one(feed, connect):
    conn = connect()
    with th.writer(conn) as old:
        old.record("new", 1)
    with th.writer(conn) as current:
        with pytest.raises(th.FeedError, match="block has ended"):
            old.record("new", 2)
        current.record("new", 3)
    with pytest.raises(th.FeedError, match="block has ended"):
        current.record("new", 4)
    with th.writer(connect()) as elsewhere:                            # another connection's writer: not this one
        with pytest.raises(th.FeedError, match="block has ended"):
            old.record("new", 5)
        elsewhere.record("new", 6)
    assert [e["item_id"] for e in th.events(conn, 0)["events"]] == [1, 3, 6]


def test_a_changed_search_path_inside_the_block_cannot_move_the_events_to_another_feed(feed, connect):
    feed.execute("create schema feedspace")
    feed.execute("create table feedspace.change_event (like public.change_event including defaults)")
    conn = connect()
    with th.writer(conn) as w:                                         # holds public.change_event
        conn.execute("set local search_path = feedspace, public")
        with pytest.raises(th.FeedError, match="not the feed table this writer holds"):
            w.record("new", 1)
        with pytest.raises(th.FeedError, match="not the feed table"):
            th.record(conn, "new", 1)
        assert th.record(conn, "new", 1, table="public.change_event")  # named exactly: the held table
    assert conn.execute("select count(*) as n from feedspace.change_event").fetchone()["n"] == 0
    assert [e["item_id"] for e in th.events(conn, 0, table="public.change_event")["events"]] == [1]


def test_a_feed_table_renamed_inside_the_block_takes_no_event_and_the_block_rolls_back(feed, connect):
    conn = connect()
    with pytest.raises(Exception):
        with th.writer(conn) as w:
            conn.execute("alter table change_event rename to change_event_old")
            w.record("new", 1)
    assert conn.info.transaction_status.name == "IDLE"
    assert conn.execute("select to_regclass('change_event') is not null as there").fetchone()["there"]
    assert th.events(conn, 0)["events"] == []


def test_a_writer_reads_at_read_committed_whatever_the_connection_asks(feed, connect):
    from psycopg import IsolationLevel
    conn = connect()
    conn.isolation_level = IsolationLevel.SERIALIZABLE
    with th.writer(conn):
        assert conn.execute("show transaction_isolation").fetchone()["transaction_isolation"] == "read committed"


# ---------------------------------------------------------------- a product's watcher (the documented algorithm)

class Watcher:
    """The reference watcher of the module's docstring, with invented durable state in the test's own tables:
    the set of items in, the cursor, and the transitions it reported. Rule: root = the watched root, not gone."""

    def __init__(self, conn, root: str, name: str = "w", label: str | None = None):
        self.conn, self.root, self.name = conn, root, name
        conn.execute("create table if not exists watch_state (name text primary key, cursor bigint not null,"
                     " root text not null, label text)")
        conn.execute("create table if not exists watch_member (name text, item_id bigint, primary key (name, item_id))")
        conn.execute("create table if not exists watch_note (id bigserial primary key, name text, item_id bigint,"
                     " what text)")
        conn.execute("create table if not exists watch_exclusion (name text, item_id bigint)")
        conn.execute("insert into watch_state values (%s, 0, %s, %s) on conflict do nothing", [name, root, label])

    def _matching(self, ids=None) -> set[int]:
        # The rule: the item's root, and its label when the rule names one; never a gone item or an exclusion.
        rows = self.conn.execute(
            "select i.id from items i join watch_state s on s.name = %s"
            " where i.root = s.root and (s.label is null or i.label is not distinct from s.label)"
            " and not i.gone and (%s::bigint[] is null or i.id = any(%s::bigint[]))"
            " and i.id not in (select item_id from watch_exclusion where name = %s)",
            [self.name, ids, ids, self.name]).fetchall()
        return {r["id"] for r in rows}

    def _members(self) -> set[int]:
        return {r["item_id"] for r in self.conn.execute(
            "select item_id from watch_member where name = %s", [self.name]).fetchall()}

    def _apply(self, touched: set[int] | None, now: set[int], cursor: int):
        members = self._members()
        scope = members if touched is None else members & touched
        entered, left = now - members, scope - now
        for i in sorted(entered):
            self.conn.execute("insert into watch_member values (%s, %s)", [self.name, i])
            self.conn.execute("insert into watch_note (name, item_id, what) values (%s, %s, 'in')", [self.name, i])
        for i in sorted(left):
            self.conn.execute("delete from watch_member where name = %s and item_id = %s", [self.name, i])
            self.conn.execute("insert into watch_note (name, item_id, what) values (%s, %s, 'out')", [self.name, i])
        self.conn.execute("update watch_state set cursor = %s where name = %s", [cursor, self.name])

    def poll(self, *, limit=200, crash_before_commit=False):
        with self.conn.transaction():
            cursor = self.conn.execute("select cursor from watch_state where name = %s", [self.name]).fetchone()
            page = th.events(self.conn, cursor["cursor"], limit=limit)
            touched = {e["item_id"] for e in page["events"]}
            self._apply(touched, self._matching(sorted(touched)), page["cursor"])
            if crash_before_commit:
                raise RuntimeError("the watcher stopped before it committed")
        return page["more"]

    def change_rule(self, root: str, label: str | None = None):
        with self.conn.transaction():
            head = self.conn.execute("select coalesce(max(id), 0) as n from change_event").fetchone()["n"]
            self.conn.execute("update watch_state set root = %s, label = %s where name = %s", [root, label, self.name])
            self._apply(None, self._matching(), head)

    def exclude(self, item_id: int):
        with self.conn.transaction():
            self.conn.execute("insert into watch_exclusion values (%s, %s)", [self.name, item_id])
            self._apply({item_id}, self._matching([item_id]), self.cursor())

    def cursor(self) -> int:
        return self.conn.execute("select cursor from watch_state where name = %s", [self.name]).fetchone()["cursor"]

    def notes(self) -> list[tuple[int, str]]:
        return [(r["item_id"], r["what"]) for r in self.conn.execute(
            "select item_id, what from watch_note where name = %s order by id", [self.name]).fetchall()]


def _do(conn, kind, item_id, sql=None, params=()):
    with th.writer(conn) as w:
        if sql:
            conn.execute(sql, params)
        w.record(kind, item_id)


def test_a_watcher_with_its_own_membership_sees_moves_in_and_out_gone_and_back(feed, connect):
    producer, watcher_conn = connect(), connect()
    for i in (1, 2, 3):
        _do(producer, "new", i)
    w = Watcher(watcher_conn, "a")
    w.poll()
    assert w.notes() == [(1, "in"), (2, "in"), (3, "in")]
    _do(producer, "moved", 2, "update items set root = 'b' where id = 2")       # out
    _do(producer, "moved", 4, "update items set root = 'b' where id = 4")       # never in: nothing to say
    _do(producer, "gone", 3, "update items set gone = true where id = 3")       # gone is out
    w.poll()
    assert w.notes()[3:] == [(2, "out"), (3, "out")]
    _do(producer, "back", 3, "update items set gone = false where id = 3")
    _do(producer, "moved", 2, "update items set root = 'a' where id = 2")
    _do(producer, "changed", 1, "update items set label = 'invoice' where id = 1")   # still in: nothing
    w.poll()
    assert w.notes()[5:] == [(2, "in"), (3, "in")]
    assert th.since(producer, 0, where="i.root = %s", params=["b"])["events"][-1]["item_id"] == 4


def test_a_label_change_moves_an_item_out_of_a_label_rule_and_back_in(feed, connect):
    producer, watcher_conn = connect(), connect()
    for i, label in ((1, "invoice"), (2, "invoice"), (3, "receipt")):
        _do(producer, "changed", i, "update items set label = %s where id = %s", (label, i))
    w = Watcher(watcher_conn, "a", label="invoice")
    w.poll()
    assert w.notes() == [(1, "in"), (2, "in")]
    _do(producer, "changed", 1, "update items set label = 'receipt' where id = 1")        # label out
    _do(producer, "changed", 3, "update items set label = 'invoice' where id = 3")        # label in
    w.poll()
    assert w.notes()[2:] == [(3, "in"), (1, "out")]
    restarted = Watcher(connect(), "a", label="invoice")                                  # a restart in between
    _do(producer, "changed", 1, "update items set label = 'invoice' where id = 1")        # back in
    restarted.poll()
    restarted.poll()
    assert restarted.notes()[4:] == [(1, "in")]
    restarted.exclude(3)                                                                  # the owner's no wins
    _do(producer, "changed", 3, "update items set label = 'invoice', root = 'a' where id = 3")
    restarted.poll()
    assert restarted.notes()[5:] == [(3, "out")]
    restarted.change_rule("a", label="receipt")                                           # a new rule: recompute
    assert restarted.notes()[6:] == [(1, "out"), (2, "out")]
    _do(producer, "changed", 2, "update items set label = 'receipt' where id = 2")
    _do(producer, "changed", 3, "update items set label = 'receipt' where id = 3")        # excluded: stays out
    restarted.poll()
    assert restarted.notes()[8:] == [(2, "in")]
    assert sorted(restarted._members()) == [2]


def test_a_watcher_that_stops_mid_poll_replays_the_page_and_says_nothing_twice(feed, connect):
    producer, watcher_conn = connect(), connect()
    _do(producer, "new", 1)
    w = Watcher(watcher_conn, "a")
    with pytest.raises(RuntimeError):
        w.poll(crash_before_commit=True)
    assert w.notes() == [] and w.cursor() == 0                             # nothing stuck halfway
    w.poll()
    w.poll()
    restarted = Watcher(connect(), "a")                                    # a new process, the same durable state
    restarted.poll()
    assert restarted.notes() == [(1, "in")]
    with th.writer(producer) as p:                                         # the same item touched twice in one page
        p.record("changed", 1)
        p.record("changed", 1)
    restarted.poll()
    assert restarted.notes() == [(1, "in")]


def test_a_watcher_pages_through_a_long_feed_and_keeps_its_place(feed, connect):
    producer, watcher_conn = connect(), connect()
    with th.writer(producer) as p:
        for i in (1, 2, 3, 4, 5, 6):
            p.record("new", i)
    w = Watcher(watcher_conn, "a")
    pages = 0
    while w.poll(limit=2):
        pages += 1
    assert pages == 3 and [n for n, _ in w.notes()] == [1, 2, 3, 4, 5, 6]


def test_an_exclusion_wins_and_a_rule_change_is_a_full_recompute(feed, connect):
    producer, watcher_conn = connect(), connect()
    for i in (1, 2):
        _do(producer, "new", i)
    producer.execute("update items set root = 'b' where id in (5, 6)")   # set before the watcher: no events needed
    w = Watcher(watcher_conn, "a")
    w.poll()
    w.exclude(2)
    _do(producer, "changed", 2, "update items set label = 'contract' where id = 2")
    w.poll()
    assert w.notes() == [(1, "in"), (2, "in"), (2, "out")]                 # excluded stays out
    w.change_rule("b")
    assert w.notes()[3:] == [(5, "in"), (6, "in"), (1, "out")]
    _do(producer, "moved", 1, "update items set root = 'b' where id = 1")
    w.poll()
    w.poll()
    assert w.notes()[6:] == [(1, "in")]
