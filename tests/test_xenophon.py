import pytest

from hippodamus import xenophon
from hippodamus.xenophon import store
from hippodamus.xenophon.store import Target


def test_a_note_is_read_into_intents_and_reach_in_swedish_and_english():
    u = xenophon.understand("Släng allt från avsändaren, alltid. Not important.")
    assert u["intents"] == ["delete", "unimportant"]
    assert u["reach"] == ["sender", "always"]


def test_a_longer_phrase_claims_its_words_first():
    assert xenophon.understand("inte viktigt")["intents"] == ["unimportant"]
    assert xenophon.understand("not important")["intents"] == ["unimportant"]


def test_inflections_are_found():
    assert xenophon.understand("arkiveras")["intents"] == ["archive"]


def test_a_product_extends_the_vocabulary_without_changing_the_default():
    files = xenophon.DEFAULT.extended(intents={"rename": ("rename", "byt namn")}, intent_labels={"rename": "Rename"})
    assert files.understand("byt namn på den")["intents"] == ["rename"]
    assert xenophon.understand("byt namn på den")["intents"] == []
    assert files.labels({"intents": ["rename"], "reach": []}) == ["Rename"]


def test_a_note_with_control_characters_or_too_long_is_refused():
    with pytest.raises(xenophon.NoteError):
        xenophon.clean("a\x00b")
    with pytest.raises(xenophon.NoteError):
        xenophon.clean("x" * 20_001)
    assert xenophon.clean("  hej\r\n  ") == "hej"


@pytest.fixture
def notes(db):
    db.execute(store.ddl(("file", "session")))
    return db


def test_the_store_has_talos_s_columns_exactly(notes):
    assert store.check_schema(notes) == []


def test_one_note_per_target_and_saving_again_changes_it(notes):
    t = Target("file", "item:7", 7, {"sha256": "ab"})
    a = store.save(notes, t, "Arkivera den")
    b = store.save(notes, t, "Keep it, reference")
    assert a["id"] == b["id"]
    assert b["understood"]["intents"] == ["reference"]
    assert store.find(notes, "file", "item:7")["body"] == "Keep it, reference"


def test_a_target_kind_the_product_did_not_list_is_refused_by_the_database(notes):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        store.save(notes, Target("message", "id:1", 1), "hej")


def test_an_emptied_note_without_actions_is_gone_and_with_actions_is_withdrawn(notes):
    t = Target("file", "item:1", 1)
    store.save(notes, t, "move it")
    assert store.save(notes, t, "") is None
    assert store.find(notes, "file", "item:1") is None
    n = store.save(notes, t, "move it")
    store.record_action(notes, n["id"], kind="propose", payload={"kind": "move"}, by="claude")
    w = store.save(notes, t, "   ")
    assert w["status"] == "withdrawn" and w["body"] == "" and len(w["actions"]) == 1


def test_a_changed_note_is_open_again(notes):
    t = Target("file", "item:2", 2)
    n = store.save(notes, t, "archive")
    store.answer(notes, n["id"], "Proposed archiving it (waiting for approval).", by="claude", done=True)
    assert store.find(notes, "file", "item:2")["status"] == "done"
    assert store.save(notes, t, "archive, and tag it")["status"] == "open"


def test_marks_say_which_targets_have_a_live_note(notes):
    store.save(notes, Target("file", "item:3", 3), "keep")
    n = store.save(notes, Target("file", "item:4", 4), "keep")
    store.set_status(notes, n["id"], "done")
    assert store.marks(notes, "file", ["item:3", "item:4", "item:5"]) == {"item:3": "open", "item:4": "done"}


def test_an_agent_cannot_answer_a_withdrawn_note(notes):
    t = Target("file", "item:6", 6)
    n = store.save(notes, t, "x")
    store.record_action(notes, n["id"], kind="k", payload={}, by="claude")
    store.save(notes, t, "")
    with pytest.raises(xenophon.NoteError):
        store.answer(notes, n["id"], "did it", by="claude")
