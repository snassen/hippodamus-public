import os

import pytest

DSN = os.environ.get("HIPPODAMUS_TEST_DSN", "host=/tmp port=5441 dbname=hippodamus_test")


def scratch_only(dsn: str) -> None:
    """Fail closed unless the DSN names a scratch database: the scratch cluster's port 5441, on this machine (a
    socket directory or loopback), a database whose name begins hippodamus_test. Never a product's own database on
    another port (5432, 5433, 5434 and the rest), whatever the environment says."""
    from psycopg.conninfo import conninfo_to_dict
    try:
        d = conninfo_to_dict(dsn)
    except Exception:
        pytest.exit("HIPPODAMUS_TEST_DSN is not a readable connection string", returncode=3)
    host, port, name = str(d.get("host") or ""), str(d.get("port") or ""), str(d.get("dbname") or "")
    local = host.startswith("/") or host in ("127.0.0.1", "localhost", "::1")
    if port != "5441" or not local or "," in host or not name.startswith("hippodamus_test") or "hostaddr" in d:
        pytest.exit("the tests run on a hippodamus_test* database on the scratch cluster (local, port 5441) only",
                    returncode=3)


def _connect(**kw):
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row
    scratch_only(DSN)
    try:
        return psycopg.connect(DSN, row_factory=dict_row, **{"autocommit": True, **kw})
    except psycopg.OperationalError as e:
        pytest.skip(f"no scratch database: {e}")


@pytest.fixture
def db():
    """A clean scratch database (never a product's live one): each test starts with no tables of ours."""
    c = _connect()
    c.execute("drop table if exists note_action, owner_note, change_event, other_event, watch_member, watch_note,"
              " watch_state, watch_exclusion, items, things cascade")
    c.execute("drop schema if exists feedspace cascade")
    yield c
    c.close()


@pytest.fixture
def connect(db):
    """More connections to the same scratch database (a second writer, a reader), closed after the test."""
    opened = []

    def make(**kw):
        c = _connect(**kw)
        opened.append(c)
        return c
    yield make
    for c in opened:
        c.close()
