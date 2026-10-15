import http.server
import json
import threading

import pytest

from hippodamus import pheidippides as ph
from hippodamus import thucydides as th


def test_the_runner_talks_only_to_this_mac():
    with pytest.raises(ValueError):
        ph.http_transport("http://other-host.example:7440")
    with pytest.raises(ValueError):
        ph.http_transport("https://127.0.0.1:7440")
    ph.http_transport("http://127.0.0.1:7440")
    ph.http_transport("http://[::1]:7440")
    ph.http_transport("http://localhost:7440")


def test_a_stopped_product_is_unreachable_not_an_error_page():
    u = ph.Urania("http://127.0.0.1:9")   # discard port: nothing listens
    with pytest.raises(ph.Unreachable):
        u.health()


def test_a_refusal_carries_the_other_product_s_reason():
    u = ph.Urania(transport=lambda m, p, b: (400, {"error": "unknown field 'colour'"}))
    with pytest.raises(ph.Refused, match="colour"):
        u.match([{"field": "colour", "op": "is", "value": "x"}])


def test_the_runner_asks_the_paths_it_names():
    seen = []
    u = ph.Urania(transport=lambda m, p, b: (seen.append((m, p, b)), (200, {"ok": True}))[1])
    u.match([{"field": "ext", "op": "is", "value": ".pdf"}], limit=5)
    u.note(12)
    u.changes(since=3)
    assert [s[:2] for s in seen] == [("POST", "/api/bridge/match"), ("GET", "/api/notes/file/12"),
                                     ("POST", "/api/bridge/changes")]


# ---------------------------------------------------------------- the default transport, against invented servers

class Server:
    """A small invented product on a loopback port: answers each path from a table, records what it was asked."""

    def __init__(self, routes, host="127.0.0.1"):
        self.routes, self.asked = routes, []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.asked.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                    "body": self.rfile.read(n) if n else b""})
                status, headers, body = outer.routes.get(self.path, (404, {}, b'{"error": "no such path"}'))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            do_GET = do_POST = _answer

            def log_message(self, *a):
                pass

        cls = http.server.ThreadingHTTPServer
        if ":" in host:
            cls = type("V6", (http.server.ThreadingHTTPServer,), {"address_family": __import__("socket").AF_INET6})
        self.httpd = cls((host, 0), Handler)
        port = self.httpd.server_address[1]
        self.base = f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def servers():
    made = []

    def make(routes, host="127.0.0.1"):
        s = Server(routes, host)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def test_a_redirect_is_refused_before_its_target_is_asked(servers):
    other = servers({"/outside-base": (200, {}, b'{"secret": true}')})
    for code in (301, 302, 303, 307, 308):
        base = servers({"/api/bridge/health": (code, {"Location": other.base + "/outside-base"}, b"")})
        with pytest.raises(ph.Unreachable, match="redirect"):
            ph.Urania(base.base).health()
    off_mac = servers({"/api/bridge/health": (302, {"Location": "http://files.example/x"}, b"")})
    with pytest.raises(ph.Unreachable, match="redirect"):
        ph.Urania(off_mac.base).health()
    same = servers({"/api/bridge/health": (302, {"Location": "/api/bridge/fields"}, b""),
                    "/api/bridge/fields": (200, {}, b"{}")})
    with pytest.raises(ph.Unreachable, match="redirect"):                  # even to the same base
        ph.Urania(same.base).health()
    assert other.asked == [] and [a["path"] for a in same.asked] == ["/api/bridge/health"]
    odd = servers({"/api/bridge/health": (300, {}, b'{"ok": true}')})
    with pytest.raises(ph.Unreachable):                                    # no 3xx passes as an answer
        ph.Urania(odd.base).health()


def test_an_answer_is_read_up_to_one_mebibyte_success_or_error(servers):
    fits = json.dumps({"pad": "x" * (ph.MAX_BODY - 20)}).encode()
    assert len(fits) <= ph.MAX_BODY
    s = servers({"/ok": (200, {}, fits), "/big": (200, {}, b" " * (ph.MAX_BODY + 1)),
                 "/bigerror": (500, {}, b" " * (ph.MAX_BODY + 1)), "/notjson": (200, {}, b"<html>"),
                 "/error": (400, {}, b'{"error": "bad rule"}'), "/errortext": (502, {}, b"<html>")})
    go = ph.http_transport(s.base)
    assert go("GET", "/ok", None)[0] == 200
    for path in ("/big", "/bigerror", "/notjson"):
        with pytest.raises(ph.Unreachable):
            go("GET", path, None)
    assert go("GET", "/error", None) == (400, {"error": "bad rule"})
    assert go("GET", "/errortext", None) == (502, None)


def test_no_proxy_is_used_whatever_the_environment_says(servers, monkeypatch):
    s = servers({"/api/bridge/health": (200, {}, b'{"ok": true}')})
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    assert ph.Urania(s.base).health() == {"ok": True}


def test_ipv6_loopback_is_this_mac_too(servers):
    try:
        s = servers({"/api/bridge/health": (200, {}, b'{"ok": true}')}, host="::1")
    except OSError:
        pytest.skip("no IPv6 loopback here")
    assert ph.Urania(s.base).health() == {"ok": True}


def test_the_products_credentials_are_asked_per_request_and_sent_as_headers(servers):
    s = servers({"/api/bridge/health": (200, {}, b'{"ok": true}')})
    calls = []

    def provider():
        calls.append(1)
        return {"Authorization": "Bearer invented-token-1"}
    u = ph.Urania(s.base, headers=provider)
    assert calls == []                                                     # built, nothing read
    u.health()
    u.health()
    assert len(calls) == 2
    sent = s.asked[0]["headers"]
    assert sent["Authorization"] == "Bearer invented-token-1" and sent["X-Urania"] == "1"


@pytest.mark.parametrize("provider", [lambda: None, lambda: {}, lambda: {"Authorization": ""},
                                      lambda: {"Authorization": 5}, lambda: [("Authorization", "x")],
                                      lambda: {"Authorization": "a\r\nX-Other: b"}])
def test_missing_or_malformed_credentials_are_refused_and_never_sent_anonymously(servers, provider):
    s = servers({"/api/bridge/health": (200, {}, b'{"ok": true}')})
    with pytest.raises(ph.Refused, match="no credentials"):
        ph.Urania(s.base, headers=provider).health()
    assert s.asked == []


def test_a_provider_that_fails_is_refused_without_its_secret_and_its_own_answer_is_kept(servers):
    s = servers({"/api/bridge/health": (200, {}, b'{"ok": true}')})

    def broken():
        raise KeyError("invented-secret-value")
    with pytest.raises(ph.Refused) as exc:
        ph.Urania(s.base, headers=broken).health()
    assert "invented-secret-value" not in str(exc.value)

    def locked():
        raise ph.Unreachable("keychain item invented-secret-value is locked")
    with pytest.raises(ph.Unreachable, match="credential provider is unavailable") as exc:
        ph.Urania(s.base, headers=locked).health()
    assert "invented-secret-value" not in str(exc.value)

    def refusing():
        raise ph.Refused("token invented-secret-value expired")
    with pytest.raises(ph.Refused, match="no credentials") as exc:
        ph.Urania(s.base, headers=refusing).health()
    assert "invented-secret-value" not in str(exc.value) and exc.value.__cause__ is None
    assert s.asked == []


@pytest.mark.parametrize("blank", [{"Authorization": "   "}, {"Authorization": "Bearer"}, {"Authorization": "Bearer  "},
                                   {" ": "Bearer x"}, {"Authorization": "Bearer x\0"}])
def test_a_blank_credential_is_refused_and_nothing_is_sent(servers, blank):
    s = servers({"/api/bridge/health": (200, {}, b'{"ok": true}')})
    with pytest.raises(ph.Refused, match="no credentials"):
        ph.Urania(s.base, headers=lambda: blank).health()
    assert s.asked == []


@pytest.fixture(scope="module")
def listener():
    """One invented product for the many refusals below: none of them may reach it."""
    s = Server({"/api/bridge/health": (200, {}, b'{"ok": true}')})
    yield s
    s.close()


def _quietly_refused(exc, given):
    """A refusal says what kind of thing was wrong, never the header's name or value, and hides no encoding error."""
    msg = str(exc.value)
    assert msg.startswith("no credentials") and "invented" not in msg
    for s in (x for kv in given.items() for x in kv if isinstance(x, str)):
        if len(s.strip()) >= 4 and s.lower() != "authorization":
            assert s not in msg
    assert exc.value.__cause__ is None and exc.value.__context__ is None


def _received(server, i=-1):
    return {k.lower(): v for k, v in server.asked[i]["headers"].items()}


@pytest.mark.parametrize("given", [{"Authorization ": "Bearer invented-token"}, {"Bad:Name": "invented-token"},
                                   {"Authorization": "Bearer invented-☃"}],
                         ids=["name-with-space", "name-with-colon", "value-outside-latin-1"])
def test_a_header_urllib_would_mangle_or_choke_on_is_refused_before_anything_is_sent(servers, given):
    s = servers({"/api/bridge/health": (200, {}, b'{"ok": true}')})
    with pytest.raises(ph.Refused) as exc:
        ph.Urania(s.base, headers=lambda: given).health()
    _quietly_refused(exc, given)
    assert s.asked == []


REFUSED = {
    "name-empty": ({"": "Bearer invented-token"}, "HTTP token"),
    "name-space-inside": ({"X Invented": "invented-key"}, "HTTP token"),
    "name-leading-space": ({" Authorization": "Bearer invented-token"}, "HTTP token"),
    "name-separator": ({"X-Invented(Key)": "invented-key"}, "HTTP token"),
    "name-latin-1": ({"X-Invented-Tøken": "invented-key"}, "HTTP token"),
    "name-dotless-i": ({"Authorizatıon": "Bearer invented-token"}, "HTTP token"),
    "name-not-text": ({5: "invented-key"}, "not text"),
    "value-not-text": ({"X-Invented-Key": b"invented-key"}, "not text"),
    "value-empty": ({"X-Invented-Key": ""}, "cannot be sent"),
    "value-leading-space": ({"X-Invented-Key": " invented-key"}, "cannot be sent"),
    "value-trailing-space": ({"X-Invented-Key": "invented-key "}, "cannot be sent"),
    "value-only-spaces": ({"X-Invented-Key": "    "}, "cannot be sent"),
    "value-past-latin-1": ({"X-Invented-Key": "invented-Ā"}, "cannot be sent"),
    "authorization-scheme-only": ({"Authorization": "Bearer"}, "scheme"),
    "authorization-scheme-and-spaces": ({"Authorization": "Bearer   "}, "cannot be sent"),
    "authorization-no-scheme": ({"Authorization": "invented-token"}, "scheme"),
    "authorization-scheme-equals": ({"Authorization": "Bearer=invented-token"}, "scheme"),
    "authorization-scheme-colon": ({"Authorization": "Bearer: invented-token"}, "scheme"),
    "authorization-scheme-comma": ({"Authorization": ",Bearer invented-token"}, "scheme"),
    "authorization-scheme-latin-1": ({"Authorization": "Béarer invented-token"}, "scheme"),
    "duplicate-authorization-same-value": ({"Authorization": "Bearer invented-token",
                                            "authorization": "Bearer invented-token"}, "twice"),
    "duplicate-authorization-different-values": ({"Authorization": "Bearer invented-a",
                                                  "AUTHORIZATION": "Bearer invented-b"}, "twice"),
    "duplicate-authorization-other-order": ({"authorization": "Bearer invented-b",
                                             "Authorization": "Bearer invented-a"}, "twice"),
    "duplicate-custom": ({"X-Invented-Key": "invented-a", "x-invented-key": "invented-b"}, "twice"),
}


@pytest.mark.parametrize("given,why", list(REFUSED.values()), ids=list(REFUSED))
def test_a_malformed_set_of_headers_is_refused_for_its_reason_and_nothing_is_sent(listener, given, why):
    with pytest.raises(ph.Refused, match=why) as exc:
        ph.http_transport(listener.base, headers=lambda: given)("GET", "/api/bridge/health", None)
    _quietly_refused(exc, given)
    assert listener.asked == []


CONTROLS = [chr(c) for c in (*range(0x20), 0x7F, *range(0x80, 0xA0))]


def test_no_control_character_passes_anywhere_in_a_name_or_a_value(listener):
    given = {}
    go = ph.http_transport(listener.base, headers=lambda: given)
    for c in CONTROLS:
        for given in ({"Authorization": c + "Bearer invented-token"},
                      {"Authorization": "Bearer" + c + "invented-token"},       # a tab is no separator either
                      {"Authorization": "Bearer invented" + c + "token"},
                      {"Authorization": "Bearer invented-token" + c},
                      {"X-Invented-Key": "invented" + c + "key"},
                      {"X-Invented" + c + "Key": "invented-key"},
                      {c + "X-Invented-Key": "invented-key"}):
            with pytest.raises(ph.Refused) as exc:
                go("GET", "/api/bridge/health", None)
            _quietly_refused(exc, given)
    assert listener.asked == []


def test_well_formed_credentials_reach_the_product_as_given(servers):
    s = servers({"/api/bridge/health": (200, {}, b'{"ok": true}')})
    digest = 'Digest username="invented", realm="invented realm",  nonce="0a1b", response="2c3d"'
    for given in ({"aUtHoRiZaTiOn": "bearer invented-token"},
                  {"Authorization": digest},
                  {"Authorization": "Bearer   invented-token"},                 # the spaces are kept, not squeezed
                  {"X-Invented-Key": "invented café key ÿ"},     # Latin-1, and no Authorization at all
                  {"X-Invented!#$%&'*+.^_`|~Key": "invented-key", "Authorization": "Bearer invented-token"}):
        assert ph.Urania(s.base, headers=lambda: given).health() == {"ok": True}
        got = _received(s)
        assert all(got[k.lower()] == v for k, v in given.items()) and got["x-urania"] == "1"
    assert len(s.asked) == 5


def test_a_lazy_provider_can_rotate_its_token_between_requests(servers):
    s = servers({"/api/bridge/health": (200, {}, b'{"ok": true}')})
    tokens = iter(["Bearer invented-token-1", "Bearer invented-token-2", "Bearer invented-token-3"])
    u = ph.Urania(s.base, headers=lambda: {"Authorization": next(tokens)})
    for _ in range(3):
        u.health()
    assert [_received(s, i)["authorization"] for i in range(3)] == [
        "Bearer invented-token-1", "Bearer invented-token-2", "Bearer invented-token-3"]


def test_what_is_sent_is_a_plain_copy_of_what_was_checked_in_the_provider_s_own_casing():
    given = {"X-Invented-KEY": "invented-key", "authorization": "Bearer invented-token"}
    sent = ph._credentials(lambda: given)
    assert sent == given and sent is not given and list(sent) == list(given)

    class Sneaky(dict):                         # would show the check one header and urllib another
        def items(self):
            return [("Authorization", "Bearer invented-token")]
    with pytest.raises(ph.Refused, match="HTTP token"):
        ph._credentials(lambda: Sneaky({"Bad:Name": "invented-token"}))

    class Shifty(str):                          # would recase itself into something else when urllib sends it
        def capitalize(self):
            return "Bad:Name"
    sent = ph._credentials(lambda: {Shifty("X-Invented-Key"): Shifty("invented-key")})
    assert [(type(k), type(v)) for k, v in sent.items()] == [(str, str)]


def test_json_nested_too_deep_to_decode_is_no_answer_on_success_and_no_reason_on_error(servers):
    deep = b"[" * 200_000 + b"]" * 200_000                             # well under a mebibyte
    assert len(deep) <= ph.MAX_BODY
    s = servers({"/ok": (200, {}, deep), "/error": (400, {}, deep), "/api/bridge/health": (500, {}, deep)})
    go = ph.http_transport(s.base)
    with pytest.raises(ph.Unreachable, match="not JSON"):
        go("GET", "/ok", None)
    assert go("GET", "/error", None) == (400, None)
    with pytest.raises(ph.Refused, match="urania said 500"):
        ph.Urania(s.base).health()


def test_the_test_database_guard_fails_closed_off_the_scratch_cluster():
    from conftest import scratch_only
    for dsn in ("host=/tmp port=5433 dbname=hippodamus_test", "host=/tmp port=5434 dbname=hippodamus_test",
                "host=/tmp port=5432 dbname=hippodamus_test", "host=/tmp port=5441 dbname=talos",
                "host=db.example port=5441 dbname=hippodamus_test", "host=/tmp dbname=hippodamus_test",
                "host=/tmp,db.example port=5441 dbname=hippodamus_test", "not a dsn = = ="):
        with pytest.raises(pytest.exit.Exception):
            scratch_only(dsn)
    scratch_only("host=/tmp port=5441 dbname=hippodamus_test_programme_shared")
    scratch_only("host=127.0.0.1 port=5441 dbname=hippodamus_test")


def test_a_custom_transport_keeps_its_own_policy_and_cannot_be_given_headers():
    with pytest.raises(ValueError, match="custom transport"):
        ph.Urania(transport=lambda m, p, b: (200, {}), headers=lambda: {"Authorization": "x"})
    # A custom transport owns its policy: a 3xx it returns is its own business, as before.
    assert ph.Urania(transport=lambda m, p, b: (302, {"moved": True})).health() == {"moved": True}
    with pytest.raises(ValueError):
        ph.http_transport("http://127.0.0.1:7440", headers="Bearer x")


# ---------------------------------------------------------------- the feed (single connection)

@pytest.fixture
def feed(db):
    db.execute("create table items (id bigserial primary key, root text)")
    db.execute(th.ddl())
    db.execute("insert into items (root) values ('a'), ('b')")
    return db


def test_the_feed_gives_what_happened_after_the_cursor_in_order(feed):
    with th.writer(feed) as w:
        a = w.record("new", 1)
        b = w.record("changed", 2, {"size": 4})
        c = th.record(feed, "moved", 1, {"from": "x", "to": "y"})
    page = th.since(feed, a)
    assert [e["id"] for e in page["events"]] == [b, c] and page["cursor"] == c
    assert th.since(feed, c)["events"] == [] and th.since(feed, c)["cursor"] == c
    assert th.events(feed, a)["events"] == page["events"]


def test_a_filtered_feed_still_moves_the_cursor_past_what_did_not_match(feed):
    with th.writer(feed) as w:
        w.record("new", 1)
        last = w.record("new", 2)
    page = th.since(feed, 0, where="i.root = %s", params=["a"])
    assert [e["item_id"] for e in page["events"]] == [1] and page["cursor"] == last


def test_a_full_page_stops_at_its_last_event(feed):
    with th.writer(feed) as w:
        ids = [w.record("new", 1) for _ in range(5)]
    page = th.since(feed, 0, limit=2)
    assert page["more"] and page["cursor"] == ids[1]
    page = th.events(feed, 0, limit=2)
    assert page["more"] and page["cursor"] == ids[1]
    page = th.events(feed, ids[1], limit=2)
    assert [e["id"] for e in page["events"]] == ids[2:4]
    assert th.events(feed, ids[3], limit=2) == {"events": [th.events(feed, ids[3])["events"][0]],
                                                "cursor": ids[4], "more": False}


def test_an_unknown_event_kind_is_refused(feed):
    with th.writer(feed) as w:
        with pytest.raises(th.FeedError):
            w.record("deleted", 1)


def test_the_filtered_read_means_matching_now_and_the_unfiltered_read_keeps_the_move_out(feed):
    with th.writer(feed) as w:
        feed.execute("update items set root = 'b' where id = 1")
        moved = w.record("moved", 1, {"from": "a", "to": "b"})
    assert th.since(feed, 0, where="i.root = %s", params=["a"]) == {"events": [], "cursor": moved, "more": False}
    assert [(e["kind"], e["data"]) for e in th.events(feed, 0)["events"]] == [("moved", {"from": "a", "to": "b"})]
