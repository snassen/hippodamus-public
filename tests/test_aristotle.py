"""Aristotle: the classification decision core lifted from talos. The policy, sureness, boundary and masking cases are
talos's own (tests/test_jev.py, test_studio.py, test_accept.py), so the behaviour is the same. Never the real Jev:
every request goes to a fake transport. Invented data only."""

import json
import time

import pytest

from hippodamus.aristotle import answerkey, boundary, jev, mask, policy, sureness
from hippodamus.aristotle.policy import Policy
from hippodamus.aristotle.taxonomy import Taxonomy, TaxonomyError

# ---------------------------------------------------------------- a small taxonomy in talos's shape

TAXONOMY = {
    "version": 3,
    "note": "invented, in the shape of talos's rules/taxonomy.json",
    "dimensions": {
        "origin": {"label": "Origin", "cardinality": "one", "description": "Who or what produced it.",
                   "values": [
                       {"value": "person", "family": "People", "label": "Person",
                        "description": "A human wrote it to you, directly.", "common": 2},
                       {"value": "list", "family": "People", "label": "Mailing list", "description": "People on a list."},
                       {"value": "notification", "family": "Machine", "label": "Notification",
                        "description": "A system tells you something."},
                       {"value": "spam", "family": "Machine", "label": "Spam", "description": "Unwanted."}]},
        "doctype": {"label": "Document type", "cardinality": "one", "description": "What the file is.",
                    "values": [
                        {"value": "invoice", "family": "Money", "label": "Invoice", "description": "A bill to pay."},
                        {"value": "receipt", "family": "Money", "label": "Receipt", "description": "Proof of payment."},
                        {"value": "contract", "family": "Legal", "label": "Signed agreement",
                         "description": "Terms both sides signed."}]},
        "ask": {"label": "Ask", "cardinality": "many", "description": "What it expects of you.",
                "values": [{"value": "question", "family": "Asked", "label": "Question to you",
                            "description": "Someone asks you something."},
                           {"value": "deadline", "family": "Asked", "label": "Deadline",
                            "description": "A date by which you must act."}]},
        "sender_kind": {"label": "Sender", "cardinality": "one", "description": "Human or machine.",
                        "derived_from": {"dimension": "origin", "families": {"People": "people", "Machine": "machine"}},
                        "values": [{"value": "people", "family": "Sender", "label": "People", "description": "A human."},
                                   {"value": "machine", "family": "Sender", "label": "Automated",
                                    "description": "A machine."}]},
        "money": {"label": "Money", "cardinality": "one", "description": "About money or not.",
                  "derived_from": {"dimension": "doctype", "values": {"money": ["invoice", "receipt"],
                                                                      "other": ["contract"]}},
                  "values": {"money": "About money.", "other": "Anything else."}},
    },
}


def _tax(**changes) -> dict:
    d = json.loads(json.dumps(TAXONOMY))
    for name, dim in changes.items():
        d["dimensions"][name] = dim
    return d


def test_a_taxonomy_in_talos_shape_is_read_into_dimensions_with_values_families_and_labels():
    t = Taxonomy.from_dict(TAXONOMY)
    assert t.version == 3 and list(t) == ["origin", "doctype", "ask", "sender_kind", "money"]
    o = t.dimension("origin")
    assert (o.name, o.label, o.cardinality, o.many) == ("origin", "Origin", "one", False)
    assert list(o.values) == ["person", "list", "notification", "spam"]           # the file's order
    assert o.values["person"] == "A human wrote it to you, directly."
    assert o.families == {"person": "People", "list": "People", "notification": "Machine", "spam": "Machine"}
    assert t.dimension("ask").many and t.dimension("money").values == {"money": "About money.", "other": "Anything else."}
    assert t.dimension("money").families == {}                                    # a plain map has no families


def test_a_value_outside_the_list_or_an_unknown_field_is_refused():
    t = Taxonomy.from_dict(TAXONOMY)
    assert t.check("doctype", "invoice") == "invoice"
    assert t.check("ask", ["question", "deadline"]) == ["question", "deadline"] and t.check("ask", []) == []
    with pytest.raises(TaxonomyError, match="no value 'memo'"):
        t.check("doctype", "memo")
    with pytest.raises(TaxonomyError, match="takes one value"):
        t.check("doctype", ["invoice", "receipt"])
    with pytest.raises(TaxonomyError, match="no dimension 'colour'"):
        t.dimension("colour")


@pytest.mark.parametrize("bad, why", [
    ({"dimensions": {}}, "non-empty map"),
    (_tax(doctype={"cardinality": "some", "values": [{"value": "x", "description": "y"}]}), "cardinality"),
    (_tax(doctype={"cardinality": "one", "values": []}), "non-empty list"),
    (_tax(doctype={"cardinality": "one", "values": [{"value": "x"}]}), "a value and a description"),
    (_tax(doctype={"cardinality": "one", "values": [{"value": "x", "description": "a"},
                                                    {"value": "x", "description": "b"}]}), "listed twice"),
    (_tax(sender_kind={**TAXONOMY["dimensions"]["sender_kind"],
                       "derived_from": {"dimension": "origin", "families": {"People": "people"}}}),
     "families of origin map to nothing: Machine"),
    (_tax(money={**TAXONOMY["dimensions"]["money"],
                 "derived_from": {"dimension": "doctype", "values": {"money": ["invoice", "receipt", "contract"],
                                                                     "other": ["contract"]}}}),
     "under more than one money"),
    (_tax(money={**TAXONOMY["dimensions"]["money"],
                 "derived_from": {"dimension": "doctype", "values": {"money": ["invoice"], "other": ["contract"]}}}),
     "map to no money: receipt"),
    (_tax(money={**TAXONOMY["dimensions"]["money"], "derived_from": {"dimension": "ask", "values": {}}}),
     "one-value dimension"),
])
def test_a_taxonomy_that_does_not_hold_together_is_refused_as_talos_refuses_it(bad, why):
    with pytest.raises(TaxonomyError, match=why):
        Taxonomy.from_dict(bad)


def test_a_key_given_twice_in_the_file_is_refused_rather_than_quietly_kept_last(tmp_path):
    text = '{"dimensions": {"a": {"cardinality": "one", "values": {"x": "1"}}, "a": {"cardinality": "one",' \
           ' "values": {"y": "2"}}}}'
    with pytest.raises(TaxonomyError, match="twice in one object: a"):
        Taxonomy.from_json(text)
    p = tmp_path / "taxonomy.json"
    p.write_text(json.dumps(TAXONOMY), encoding="utf-8")
    assert Taxonomy.load(p).dimension("doctype").values["receipt"] == "Proof of payment."


def test_the_choice_question_is_jevs_shape_with_each_value_defined_and_led_by_a_label_that_says_more():
    t = Taxonomy.from_dict(TAXONOMY)
    q = t.choice_question("doctype")
    assert q == {"type": "choice", "instructions": "What the file is.",
                 "criteria": {"invoice": "A bill to pay.", "receipt": "Proof of payment.",
                              "contract": "Signed agreement: Terms both sides signed."}}
    assert t.choice_question("origin", "Who made it?")["instructions"] == "Who made it?"
    assert t.choice_question("origin")["criteria"]["list"] == "Mailing list: People on a list."
    many = t.choice_question("ask", none="Nothing is asked of you.")
    assert list(many["criteria"]) == ["none", "question", "deadline"]


def test_a_derived_dimension_maps_every_source_value_to_one_side_by_family_or_by_value():
    t = Taxonomy.from_dict(TAXONOMY)
    assert t.side_of("sender_kind") == {"person": "people", "list": "people", "notification": "machine",
                                        "spam": "machine"}
    assert t.side_of("money") == {"invoice": "money", "receipt": "money", "contract": "other"}
    with pytest.raises(TaxonomyError, match="not derived"):
        t.side_of("doctype")


# ---------------------------------------------------------------- the routing policy (talos's cases)

def test_a_one_value_field_is_decided_only_over_the_probability_and_the_margin():
    d = policy.route_one("origin", {"person": 0.80, "marketing": 0.15, "list": 0.05})
    assert (d.decided, d.top, d.selected, d.confidence, d.margin) == (True, "person", ("person",), 0.80, 0.65)
    assert (d.value, d.candidate, d.probabilities) == ("person", "person", {"person": 0.80, "marketing": 0.15,
                                                                            "list": 0.05})
    low = policy.route_one("origin", {"person": 0.65, "marketing": 0.35})
    assert (low.decided, low.top, low.selected, low.value) == (False, "person", (), None)   # the candidate is kept
    close = policy.route_one("origin", {"person": 0.72, "marketing": 0.60})
    assert not close.decided and round(close.margin, 2) == 0.12
    edge = policy.route_one("origin", {"person": 0.70, "marketing": 0.55})
    assert edge.decided  # exactly on both thresholds
    assert policy.route_one("origin", {"person": 0.65, "marketing": 0.35}, Policy(one_min=0.6)).decided
    assert policy.route_one("origin", {}) == policy.Decision("origin", None, (), {}, None, None, False)


def test_a_many_value_field_selects_every_statement_at_or_over_the_threshold():
    d = policy.route_many("ask", {"question": 0.9, "action": 0.5, "decision": 0.3, "deadline": 0.05})
    assert d.decided and d.selected == ("question", "action") and d.top == "question"
    assert d.confidence == 0.5  # the closest call
    assert policy.route_many("ask", {"question": 0.2, "action": 0.1}).selected == ()   # none
    assert policy.route_many("ask", {"question": 0.9, "action": 0.5}, Policy(many_min=0.6)).selected == ("question",)
    assert policy.DEFAULT == Policy(0.70, 0.15, 0.5) and policy.DEFAULT.version == policy.VERSION
    assert policy.route("ask", True, {"question": 0.9}) == policy.route_many("ask", {"question": 0.9})
    assert policy.route("origin", False, {"person": 0.9}) == policy.route_one("origin", {"person": 0.9})


def _ch(top, p, other="none", **side):
    return {top: p, other: round(1 - p, 6), **side}


def test_a_many_value_choice_selects_its_top_only_when_decided_and_not_none():
    r = policy.route_choice_many
    d = r("route", _ch("Security", 0.8))
    assert (d.top, d.selected, d.decided, d.confidence, round(d.margin, 2)) == ("Security", ("Security",), True, 0.8, 0.6)
    assert r("route", _ch("none", 0.9, "Security")).selected == ()                     # none is no selection
    low = r("route", _ch("Security", 0.6))
    assert (low.top, low.selected, low.decided) == ("Security", (), False)             # undecided: none
    assert r("route", _ch("Security", 0.6), Policy(one_min=0.5)).selected == ("Security",)
    assert r("route", {"none": 0.05, "Security": 0.48, "Costs": 0.47}, Policy(one_min=0.0, one_margin=0.0)
             ).selected == ("Security",)


def test_a_side_statement_is_selected_only_beside_a_choice():
    r = lambda scores, pol=policy.DEFAULT: policy.route_choice_many("ask", scores, pol, ("deadline",))  # noqa: E731
    assert r(_ch("action", 0.8, deadline=0.7)).selected == ("action", "deadline")
    assert r(_ch("action", 0.8, deadline=0.4)).selected == ("action",)
    assert r(_ch("none", 0.9, "action", deadline=0.95)).selected == ()             # a deadline without an ask
    assert r(_ch("action", 0.6, deadline=0.95)).selected == ()                     # an undecided ask: none
    assert r(_ch("action", 0.8, deadline=0.55), Policy(many_min=0.6)).selected == ("action",)
    d = r(_ch("action", 0.8, deadline=0.99))
    assert d.top == "action" and d.confidence == 0.8   # the choice's, not the statement's


def test_a_products_gate_forces_a_field_to_none_and_only_counts_when_it_changed_something():
    def private_never_shared(d):   # a product's own rule: a private file is never routed to a shared binder
        return {"share": "share:private"} if d["privacy"].top == "private" else {}
    scores = {"privacy": _ch("private", 0.9, "work"), "share": _ch("team", 0.9), "doctype": {"invoice": 0.95}}
    d, fired = policy.route_case(scores, choice_many={"share": ()}, gates=[private_never_shared])
    assert d["share"].selected == () and d["share"].top == "team" and fired == {"share": ["share:private"]}
    assert d["doctype"].value == "invoice"
    scores["share"] = _ch("none", 0.9, "team")
    d, fired = policy.route_case(scores, choice_many={"share": ()}, gates=[private_never_shared])
    assert fired == {}                                                     # nothing to change: the gate did not fire
    d, fired = policy.route_case({"ask": {"question": 0.9, "deadline": 0.2}}, many=["ask"],
                                 gates=[lambda d: [("ask", "a"), ("ask", "b")]])
    assert d["ask"].selected == () and fired == {"ask": ["a", "b"]}
    assert policy.route_case({"ask": {"question": 0.9}}, many=["ask"])[0]["ask"].selected == ("question",)


# ---------------------------------------------------------------- sureness (talos's cases)

@pytest.mark.parametrize("source, conf, level", [
    ("human", None, "fact"), ("human", 0.2, "fact"), ("rule", None, "certain"), ("model", 0.97, "certain"),
    ("model", 0.9, "confident"), ("model", 0.85, "confident"), ("model", 0.72, "likely"), ("model", 0.5, "maybe"),
    ("model", 0.31, "doubtful"), ("model", 0.1, "guess"), ("model", None, "likely"), ("import", None, "likely")])
def test_a_values_level_names_its_span_of_confidence_and_the_owners_own_is_always_fact(source, conf, level):
    assert sureness.level_of(source, conf) == level


def test_the_levels_are_ordered_from_fact_down_and_a_floor_says_what_is_as_sure_or_surer():
    assert [x["key"] for x in sureness.levels()] == ["fact", "certain", "confident", "likely", "maybe", "doubtful",
                                                     "guess"]
    assert sureness.KEYS == tuple(x["key"] for x in sureness.levels())
    assert sureness.FLOOR == {"fact": 1.0, "certain": 0.95, "confident": 0.85, "likely": 0.70, "maybe": 0.50,
                              "doubtful": 0.30, "guess": 0.0}
    assert sureness.SOURCE_CONFIDENCE == {"human": 1.0, "rule": 0.97, "import": 0.75}
    assert sureness.confidence_of("model", None) == 0.75 and sureness.confidence_of("model", 0.4) == 0.4
    assert sureness.at_least("certain", "likely") and not sureness.at_least("maybe", "likely")
    assert sureness.LABEL["fact"] == "Yours" and "not a measured" in sureness.LEVELS[0][3]
    with pytest.raises(sureness.SurenessError):
        sureness.check("sure-ish")


# ---------------------------------------------------------------- boundaries (talos's case)

def test_a_boundary_is_the_sum_of_the_full_probabilities_on_each_side():
    side_of = Taxonomy.from_dict(TAXONOMY).side_of("sender_kind")
    probs = boundary.side_probabilities({"person": 0.4, "list": 0.2, "notification": 0.3, "spam": 0.1},
                                        side_of, ("people", "machine"))
    assert probs == {"people": 0.6, "machine": 0.4} and boundary.decide(probs)[:3] == ("people", 0.6, 0.2)
    assert boundary.decide(probs) == ("people", 0.6, 0.2, False)                      # 0.90 is the line
    assert boundary.decide({"people": 0.9, "machine": 0.1})[3] and boundary.BOUNDARY_MIN == 0.90
    assert boundary.side_probabilities({"unknown": 0.5, "spam": 0.5}, side_of) == {"machine": 0.5}
    assert boundary.side_probabilities({}, side_of, ("people", "machine")) == {"people": 0.0, "machine": 0.0}
    assert boundary.decide({}) == (None, 0.0, 0.0, False)


# ---------------------------------------------------------------- masking (talos's cases)

@pytest.mark.parametrize("text, want", [
    ("Personnummer 19811218-9876.", "Personnummer [personnummer]."),
    ("pnr 811218-9876 och 811218+9876", "pnr [personnummer] och [personnummer]"),
    ("utan streck 8112189876", "utan streck [personnummer]"),
    ("samordningsnummer 811278-1234", "samordningsnummer [personnummer]"),
    ("kort 4111 1111 1111 1111 eller 4111-1111-1111-1111", "kort [card] eller [card]"),
    ("IBAN SE45 5000 0000 0583 9825 7466", "IBAN [iban]"),
    ("iban SE4550000000058398257466.", "iban [iban]."),
    ("ring 070-123 45 67 eller +46 70 123 45 67", "ring [phone] eller [phone]"),
    ("växel 031-12 34 56, 08-123 456 78, 0046701234567", "växel [phone], [phone], [phone]"),
    ("se https://portal.example.se/case/123?token=abc#top nu", "se https://portal.example.se/… nu"),
    ("www.blocket.se/annons/goteborg/cykel/1234", "www.blocket.se/…"),
    ("Lönespec 19811218-9876 mars.pdf", "Lönespec [personnummer] mars.pdf"),           # a file name
])
def test_masking_replaces_ids_cards_ibans_phones_and_url_paths(text, want):
    assert mask.mask(text) == want


@pytest.mark.parametrize("text", [
    "Möte 2026-09-25 kl 08:30, 0,50 kr, version 1.2.3",
    "Order 1234567890 och ärende REQ0012345",
    "Kort 1234 5678 9012 3456",
    "Konto AB12 CDEF GHIJ KLMN",
    "Utan fel 8112189875",
    "https://example.org och https://example.org/",
    "Kunder/Acme/2026-03-14 Avtal v2.docx",
])
def test_masking_leaves_dates_amounts_order_numbers_and_bare_hosts_alone(text):
    assert mask.mask(text) == text
    assert mask.mask(None) == "" and mask.VERSION == 1


# ---------------------------------------------------------------- the Jev client

FAKE_KEY = "test-key-not-real-0123"
QUESTIONS = {
    "doctype": Taxonomy.from_dict(TAXONOMY).choice_question("doctype"),
    "ask:deadline": {"type": "noul", "instructions": "It carries a date by which you must act."}}


class FakeJev:
    """A TypeSafe endpoint in the real response shape; statuses are answered first, then 200s."""

    def __init__(self, statuses=(), retry_after=None, fail=0, body=None):
        self.statuses, self.retry_after, self.fail, self.body = list(statuses), retry_after, fail, body
        self.requests: list[dict] = []

    def __call__(self, url, body, headers, timeout):
        self.requests.append({"url": url, "body": json.loads(body), "headers": dict(headers), "timeout": timeout})
        if self.fail:
            self.fail -= 1
            raise ConnectionResetError("reset")
        if self.statuses:
            s = self.statuses.pop(0)
            return s, ({"Retry-After": self.retry_after} if self.retry_after else {}), b"{}"
        if self.body is not None:
            return 200, {}, self.body
        answers = {"doctype": {"type": "choice", "choice": "invoice", "confidence": 0.9,
                               "probabilities": {"invoice": 0.9, "receipt": 0.07, "contract": 0.03}},
                   "ask:deadline": {"type": "noul", "noul": 0.2}}
        return 200, {}, json.dumps({"model": "jev-1.13.0", "answers": answers,
                                    "usage": {"input_tokens": 5000, "output_tokens": 400}}).encode()


class Key:
    def __init__(self, value=FAKE_KEY):
        self.value, self.calls = value, 0

    def __call__(self):
        self.calls += 1
        return self.value


def _client(fake, key=None, **kw):
    kw.setdefault("sleep", lambda s: None)
    return jev.Jev(key=key or Key(), transport=fake, **kw)


def test_a_request_is_the_state_the_model_and_the_questions_posted_with_the_key_as_bearer():
    fake, key = FakeJev(), Key()
    c = _client(fake, key)
    assert key.calls == 0                                                   # built, nothing read
    res = c.ask({"name": "Faktura 2026-03.pdf", "folder": "Ekonomi"}, QUESTIONS)
    req = fake.requests[0]
    assert req["url"] == "https://api.typesafe.ai/v1/systemone" and req["timeout"] == jev.TIMEOUT
    assert req["body"] == {"state": {"name": "Faktura 2026-03.pdf", "folder": "Ekonomi"}, "model": "jev-1.13.0",
                           "questions": QUESTIONS}
    assert req["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert req["headers"]["Content-Type"] == "application/json"
    assert set(res) == {"answers", "usage", "model"} and res["model"] == "jev-1.13.0"
    assert res["usage"] == {"input_tokens": 5000, "output_tokens": 400}
    assert jev.choice_probabilities(res["answers"], "doctype", {"invoice", "receipt", "contract"})["invoice"] == 0.9
    assert jev.statement_score(res["answers"], "ask:deadline") == 0.2
    c.ask({"name": "x"}, QUESTIONS)
    assert key.calls == 1                                                   # read once, on the first request
    assert c.usage.as_dict() == {"requests": 2, "retries": 0, "input_tokens": 10000, "output_tokens": 800}
    assert FAKE_KEY not in repr(c)


def test_input_costs_four_point_two_cents_per_million_tokens_and_output_is_free():
    assert jev.cost_usd({"input_tokens": 1_000_000, "output_tokens": 5_000_000}) == pytest.approx(0.042)
    assert jev.cost_usd({"input_tokens": 5000, "output_tokens": 400}) == pytest.approx(0.00021)
    assert jev.cost_usd(2_000_000) == pytest.approx(0.084) and jev.cost_usd({}) == 0


def test_a_dry_run_reads_no_key_sends_nothing_and_gives_the_requests_size():
    fake, key = FakeJev(), Key()
    c = jev.Jev(key=key, transport=fake, dry_run=True)
    res = c.ask({"name": "Faktura.pdf"}, QUESTIONS)
    assert fake.requests == [] and key.calls == 0
    body = jev.request_body({"name": "Faktura.pdf"}, QUESTIONS)
    assert res["answers"] is None and res["dry_run"] is True
    assert res["request_chars"] == len(jev.encode(body).decode())
    assert res["usage"] == {"input_tokens": jev.estimate_tokens(body), "output_tokens": 0} and res["usage"]["input_tokens"] > 0
    assert jev.Jev(dry_run=True).ask({}, QUESTIONS)["answers"] is None      # no key needed at all
    with pytest.raises(jev.JevError, match="key callable"):
        jev.Jev()


def test_a_429_is_retried_after_the_wait_retry_after_asks_for_capped():
    waits = []
    fake = FakeJev(statuses=[429, 429], retry_after="7")
    c = _client(fake, sleep=waits.append)
    assert c.ask({}, QUESTIONS)["answers"]
    assert waits == [7.0, 7.0] and len(fake.requests) == 3 and c.usage.retries == 2
    waits.clear()
    _client(FakeJev(statuses=[429], retry_after="3600"), sleep=waits.append, max_delay=30).ask({}, QUESTIONS)
    assert waits == [30]                                                    # an hour is capped
    stamp = time.time() + 12
    assert 10 <= jev.retry_after(time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(stamp))) <= 12.5


def test_a_5xx_is_retried_with_backoff_and_given_up_after_max_retries():
    waits = []
    assert _client(FakeJev(statuses=[503, 500, 502]), sleep=waits.append).ask({}, QUESTIONS)["answers"]
    assert waits == [0.5, 1.0, 2.0]
    fake = FakeJev(statuses=[500] * 10)
    with pytest.raises(jev.JevError, match="HTTP 500 after 2 retries"):
        _client(fake, max_retries=2).ask({}, QUESTIONS)
    assert len(fake.requests) == 3
    fake = FakeJev(statuses=[400])                                          # a client error is not retried
    with pytest.raises(jev.JevError, match="HTTP 400"):
        _client(fake).ask({}, QUESTIONS)
    assert len(fake.requests) == 1


def test_a_network_error_is_retried_and_then_given_up():
    fake = FakeJev(fail=2)
    assert _client(fake).ask({}, QUESTIONS)["answers"] and len(fake.requests) == 3
    with pytest.raises(jev.JevError, match="network failure after 1 retries: ConnectionResetError"):
        _client(FakeJev(fail=5), max_retries=1).ask({}, QUESTIONS)


def test_a_refused_key_stops_at_once_and_no_error_ever_shows_the_key():
    fake = FakeJev(statuses=[401] * 5)
    with pytest.raises(jev.JevAuthError) as exc:
        _client(fake).ask({}, QUESTIONS)
    assert len(fake.requests) == 1 and FAKE_KEY not in str(exc.value) and "refused the key" in str(exc.value)
    for bad in (FakeJev(statuses=[500] * 9), FakeJev(fail=9), FakeJev(body=b"not json"),
                FakeJev(body=json.dumps({"answers": {}, "note": FAKE_KEY}).encode())):
        with pytest.raises(jev.JevError) as exc:
            _client(bad).ask({}, QUESTIONS)
        assert FAKE_KEY not in str(exc.value) and FAKE_KEY not in repr(exc.value)
    with pytest.raises(jev.JevError, match="gave none"):
        _client(FakeJev(), key=Key("")).ask({}, QUESTIONS)


def test_a_malformed_answer_is_refused_by_name():
    with pytest.raises(jev.JevError, match="no probabilities for doctype"):
        jev.choice_probabilities({}, "doctype")
    with pytest.raises(jev.JevError, match="unknown value 'memo'"):
        jev.choice_probabilities({"doctype": {"probabilities": {"memo": 1.0}}}, "doctype", {"invoice"})
    with pytest.raises(jev.JevError, match="outside 0..1"):
        jev.statement_score({"ask:deadline": {"noul": 1.5}}, "ask:deadline")
    with pytest.raises(jev.JevError, match="no score"):
        jev.statement_score({}, "ask:deadline")


NAN, INF = float("nan"), float("inf")
ALLOWED = {"invoice", "receipt", "contract"}


def _probs(**p):
    return {"doctype": {"type": "choice", "probabilities": p}}


@pytest.mark.parametrize("bad", [NAN, INF, -INF, -0.01, 1.01, 2.0, True, False, "0.9", None, [0.9], {"p": 0.9}])
def test_a_probability_that_is_not_a_number_in_0_to_1_is_refused_never_clamped(bad):
    with pytest.raises(jev.JevError, match="not a number in 0..1"):
        jev.choice_probabilities(_probs(invoice=bad, receipt=0.0), "doctype", ALLOWED)
    with pytest.raises(jev.JevError):
        jev.statement_score({"ask:deadline": {"noul": bad}}, "ask:deadline")


def test_a_choice_sums_to_one_and_may_leave_values_out_only_when_what_it_gave_still_does():
    assert jev.choice_probabilities(_probs(invoice=0.9, receipt=0.07, contract=0.03), "doctype", ALLOWED) == \
        {"invoice": 0.9, "receipt": 0.07, "contract": 0.03}
    assert jev.choice_probabilities(_probs(invoice=1), "doctype", ALLOWED) == {"invoice": 1}   # as given
    assert jev.choice_probabilities(_probs(invoice=0.7, receipt=0.3), "doctype", ALLOWED) == \
        {"invoice": 0.7, "receipt": 0.3}                                   # contract left out: still 1
    assert jev.choice_probabilities(_probs(invoice=0.6, receipt=0.4 + 9e-7), "doctype", ALLOWED)   # within 1e-6
    for p in ({"invoice": 0.6, "receipt": 0.3}, {"invoice": 0.9, "receipt": 0.9},
              {"invoice": 0.6, "receipt": 0.4 + 2e-6}, {"invoice": 0.0, "receipt": 0.0}):
        with pytest.raises(jev.JevError, match="sum to"):
            jev.choice_probabilities(_probs(**p), "doctype", ALLOWED)
    with pytest.raises(jev.JevError, match="unknown value 'memo'"):
        jev.choice_probabilities(_probs(invoice=0.5, memo=0.5), "doctype", ALLOWED)
    for answers in (None, [], {"doctype": None}, {"doctype": {"probabilities": []}},
                    {"doctype": {"probabilities": {}}}):
        with pytest.raises(jev.JevError, match="no probabilities"):
            jev.choice_probabilities(answers, "doctype")
    assert jev.statement_score({"ask:deadline": {"noul": 1}}, "ask:deadline") == 1.0
    assert jev.statement_score({"ask:deadline": {"noul": 0}}, "ask:deadline") == 0.0


@pytest.mark.parametrize("body", [
    {"answers": {}, "usage": {}},                                                       # no model
    {"model": 3, "answers": {}, "usage": {}}, {"model": "m", "answers": [], "usage": {}},
    {"model": "m", "answers": {}, "usage": None}, {"model": "m", "answers": {}, "usage": []},
    {"model": "m", "answers": {}, "usage": {"input_tokens": -1}},
    {"model": "m", "answers": {}, "usage": {"input_tokens": "5000"}},
    {"model": "m", "answers": {}, "usage": {"input_tokens": 5.5}},
    {"model": "m", "answers": {}, "usage": {"output_tokens": True}},
    [], "text", None])
def test_a_response_of_the_wrong_shape_or_usage_is_refused(body):
    with pytest.raises(jev.JevError, match="invalid Jev response"):
        _client(FakeJev(body=json.dumps(body).encode())).ask({}, QUESTIONS)


def test_a_missing_token_count_is_zero_as_before_and_a_key_given_twice_is_refused():
    res = _client(FakeJev(body=b'{"model": "m", "answers": {}, "usage": {}}')).ask({}, QUESTIONS)
    assert res["usage"] == {"input_tokens": 0, "output_tokens": 0}
    twice = b'{"model": "m", "answers": {"doctype": {"probabilities": {"invoice": 0.1, "invoice": 1.0}}}, "usage": {}}'
    with pytest.raises(jev.JevError, match="twice"):
        _client(FakeJev(body=twice)).ask({}, QUESTIONS)
    for raw in (b'{"model": "m", "answers": {}, "usage": {"input_tokens": NaN}}', b'{"model": "m"', b"\xff"):
        with pytest.raises(jev.JevError, match="not JSON"):
            _client(FakeJev(body=raw)).ask({}, QUESTIONS)


def test_a_response_larger_than_a_mebibyte_is_refused_from_any_transport():
    big = json.dumps({"model": "m", "answers": {}, "usage": {}, "pad": "x" * jev.MAX_RESPONSE}).encode()
    with pytest.raises(jev.JevError, match="larger than"):
        _client(FakeJev(body=big)).ask({}, QUESTIONS)


class _Answer:
    """An invented urllib response: read(n) gives at most n bytes, as a socket would."""

    def __init__(self, data: bytes, status=200):
        self.data, self.status, self.headers, self.closed = data, status, {}, False

    def read(self, n=-1):
        return self.data if n < 0 else self.data[:n]

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_the_default_transport_reads_at_most_a_mebibyte_and_follows_no_redirect(monkeypatch):
    import urllib.error
    import urllib.request
    asked = []

    def opener(answer):
        def open_(req, timeout=None):
            asked.append(req.full_url)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return open_
    monkeypatch.setattr(jev._OPENER, "open", opener(_Answer(b"x" * (jev.MAX_RESPONSE + 5))))
    with pytest.raises(jev.JevError, match="larger than"):
        jev.urllib_transport(jev.ENDPOINT, b"{}", {}, 1)
    err = urllib.error.HTTPError(jev.ENDPOINT, 500, "boom", {}, _Answer(b"y" * (jev.MAX_RESPONSE + 5)))
    monkeypatch.setattr(jev._OPENER, "open", opener(err))
    with pytest.raises(jev.JevError, match="larger than"):
        jev.urllib_transport(jev.ENDPOINT, b"{}", {}, 1)
    small = urllib.error.HTTPError(jev.ENDPOINT, 503, "busy", {"Retry-After": "2"}, _Answer(b'{"e": 1}'))
    monkeypatch.setattr(jev._OPENER, "open", opener(small))
    assert jev.urllib_transport(jev.ENDPOINT, b"{}", {}, 1) == (503, {"retry-after": "2"}, b'{"e": 1}')
    # The redirect handler itself refuses before a request to the new place is made.
    handler = next(h for h in jev._OPENER.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
    req = urllib.request.Request(jev.ENDPOINT, data=b"{}", headers={"Authorization": "Bearer invented"})
    with pytest.raises(jev.JevError, match="redirect"):
        handler.http_error_302(req, _Answer(b""), 302, "Found", {"location": "https://elsewhere.example/x"})
    assert set(asked) == {jev.ENDPOINT}


@pytest.mark.parametrize("huge", [10 ** 10000, -(10 ** 10000), 2, -1], ids=["1e10000", "-1e10000", "2", "-1"])
def test_an_enormous_or_out_of_range_integer_is_refused_by_name_without_overflow_or_its_digits(huge):
    with pytest.raises(jev.JevError) as exc:
        jev.choice_probabilities(_probs(invoice=huge), "doctype", ALLOWED)
    assert "not a number in 0..1" in str(exc.value) and len(str(exc.value)) < 200
    with pytest.raises(jev.JevError, match="outside 0..1") as exc:
        jev.statement_score({"ask:deadline": {"noul": huge}}, "ask:deadline")
    assert len(str(exc.value)) < 200
    for route in (lambda s: policy.route_one("doctype", s), lambda s: policy.route_many("ask", s),
                  lambda s: policy.route_choice_many("ask", s), lambda s: policy.route_case({"doctype": s})):
        with pytest.raises(policy.PolicyError) as exc:
            route({"invoice": huge})
        assert len(str(exc.value)) < 200
    raw = b'{"model": "m", "usage": {}, "answers": {"doctype": {"probabilities": {"invoice": 1' + b"0" * 1000 + b"}}}}"
    answers = _client(FakeJev(body=raw)).ask({}, QUESTIONS)["answers"]   # parsed; refused when read
    with pytest.raises(jev.JevError, match="not a number in 0..1"):
        jev.choice_probabilities(answers, "doctype", ALLOWED)


@pytest.mark.parametrize("bad", [NAN, INF, -0.1, 1.5, 2.0, True, "0.9", None])
def test_the_policy_refuses_a_bad_probability_whoever_passes_it(bad):
    with pytest.raises(policy.PolicyError):
        policy.route_one("doctype", {"invoice": bad, "receipt": 0.1})
    with pytest.raises(policy.PolicyError):
        policy.route_many("ask", {"question": bad})
    with pytest.raises(policy.PolicyError):
        policy.route_choice_many("ask", {"action": 0.9, "none": 0.1, "deadline": bad}, statements=("deadline",))
    with pytest.raises(policy.PolicyError):
        policy.route_case({"doctype": {"invoice": bad}})
    with pytest.raises(policy.PolicyError):
        policy.route_case({"doctype": None})


# ---------------------------------------------------------------- the answer key

CASES = [{"id": i, "kind": k} for i, k in enumerate(["invoice"] * 80 + ["receipt"] * 15 + ["contract"] * 4 + ["will"])]


def test_a_stratified_sample_is_deterministic_has_every_stratum_and_is_proportional_otherwise():
    s = answerkey.stratified_sample(CASES, "kind", 20, seed=7)
    assert s == answerkey.stratified_sample(CASES, lambda c: c["kind"], 20, seed=7)      # the same seed, the same
    assert s != answerkey.stratified_sample(CASES, "kind", 20, seed=8)
    from collections import Counter
    counts = Counter(c["kind"] for c in s)
    assert counts == {"invoice": 15, "receipt": 3, "contract": 1, "will": 1}               # the one will is in
    assert len({c["id"] for c in s}) == 20                                                 # nothing drawn twice
    assert [c["kind"] for c in s] != sorted(c["kind"] for c in s)                          # shuffled, not in blocks
    assert answerkey.allocate({"a": 3, "b": 1}, 10) == {"a": 3, "b": 1}                    # never more than there is
    assert answerkey.allocate({"a": 10, "b": 5, "c": 1}, 2) == {"a": 1, "b": 1, "c": 0}    # fewer than strata
    assert len(answerkey.stratified_sample(CASES, "kind", 500, seed=1)) == len(CASES)


def test_the_score_keeps_coverage_apart_from_accuracy_and_counts_the_wrong_pairs():
    truth = {1: {"doctype": "invoice", "ask": ["question"]}, 2: {"doctype": "receipt", "ask": []},
             3: {"doctype": "invoice", "ask": ["question", "deadline"]}, 4: {"doctype": "contract"},
             5: {"doctype": None}}
    predictions = {1: {"doctype": "invoice", "ask": ["question"]},
                   2: {"doctype": "invoice", "ask": []},
                   3: {"doctype": policy.route_one("doctype", {"invoice": 0.6, "receipt": 0.4}),   # undecided
                       "ask": ["deadline", "question"]},
                   4: {"doctype": policy.route_one("doctype", {"receipt": 0.9, "invoice": 0.1})},
                   5: {"doctype": "invoice"}}
    s = answerkey.score(predictions, truth)
    assert list(s) == ["ask", "doctype"]
    assert s["doctype"] == {"n": 4, "predicted": 3, "correct": 1, "accuracy": 0.3333, "coverage": 0.75,
                            "wrong": {"invoice→receipt": 1, "receipt→contract": 1}}
    assert s["ask"] == {"n": 3, "predicted": 3, "correct": 3, "accuracy": 1.0, "coverage": 1.0, "wrong": {}}
    assert answerkey.score({}, truth, ["doctype"])["doctype"]["accuracy"] is None
