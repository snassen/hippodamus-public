from datetime import datetime, timedelta, timezone

import pytest

from hippodamus import solon
from hippodamus.solon import Candidate

T = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_a_person_beats_a_rule_beats_an_accepted_model_beats_an_import():
    assert [solon.rank(o) for o in solon.ORIGINS] == [4, 3, 2, 1]


def test_an_older_human_value_still_beats_a_newer_rule():
    w = solon.effective([Candidate("h", "human", T), Candidate("r", "rule", T + timedelta(days=9))])
    assert w.value == "h"


def test_a_proposed_model_value_counts_for_nothing():
    assert solon.effective([Candidate("m", "model", T, accepted=False)]) is None
    w = solon.effective([Candidate("m", "model", T + timedelta(1), accepted=False), Candidate("i", "import", T)])
    assert w.value == "i"


def test_inside_one_origin_the_newest_wins():
    w = solon.effective([Candidate("old", "rule", T), Candidate("new", "rule", T + timedelta(hours=1))])
    assert w.value == "new"


def test_an_unknown_origin_is_refused_not_ranked_last():
    with pytest.raises(solon.SolonError):
        solon.rank("agent")


def test_the_sql_rank_orders_as_the_python_rank(db):
    db.execute("create temp table v (o text)")
    for o in solon.ORIGINS:
        db.execute("insert into v values (%s)", (o,))
    got = [r["o"] for r in db.execute(f"select o from v order by {solon.rank_sql('o')} desc")]
    assert got == list(solon.ORIGINS)


def test_the_conformance_rows_hold_for_the_python_rule():
    rows, answer = solon.conformance_rows()
    won = solon.effective_by([Candidate(r["value"], r["origin"], r["at"], r["accepted"], r["slot"]) for r in rows],
                             slot=lambda c: c.ref)
    assert {k: c.value for k, c in won.items()} == answer


def test_rank_sql_takes_only_a_column_name():
    with pytest.raises(solon.SolonError):
        solon.rank_sql("o; drop table x")
