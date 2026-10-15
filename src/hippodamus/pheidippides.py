"""Pheidippides: the runner between products.

Pheidippides ran from Marathon to Athens with one message, and nothing else. This is how one product asks another a
question on the same Mac: over the other's local API, never through its database. Talos uses it as its file organ:
it asks urania which files belong to a binder, what notes a file has, and what changed; urania owns the files and the
answers.

Standard library only. The transport is a seam: tests and other products pass their own (``transport=``), so the
same client can talk to a fake, an in-process app or another host.

What it never does: write a file, approve an action, or reach anything but the one base URL it was given (by default
urania on 127.0.0.1). The only writes it carries are the owner's own: a note the owner typed, from a signed-in page.

**The default transport** (``http_transport``) holds that promise itself: the base must be http on loopback, no proxy
is used, a redirect is refused before anything is sent to its target (Unreachable), and an answer, success or error,
is read up to MAX_BODY bytes and no further (a longer one is Unreachable too). A transport the product passes in
owns its own policy; pheidippides does not wrap it.

**Credentials** come from the product, never from pheidippides. ``headers`` is a callable the default transport
calls once per request, with no arguments, returning a ``{name: value}`` dict of headers to send (for example a
bearer token the product reads from its Keychain). It is called lazily: not when the client is built, only when a
request is about to go. If it raises ``Unreachable`` (the Keychain is locked, say), the request is Unreachable;
any other failure, or a result that is not a sendable set of headers, is ``Refused("no credentials …")`` and
nothing is sent: a protected product is never asked anonymously instead.

Sendable means a non-empty dict in which:

- each name is an HTTP token (ASCII letters, digits and ``!#$%&'*+-.^_`|~``), taken as given: nothing is trimmed
  or repaired, and no name appears twice in any casing (``Authorization`` and ``authorization`` together are
  refused, not one silently overwriting the other);
- each value is a non-empty string of printable ASCII or Latin-1 0xA0..0xFF, with spaces inside it (kept as they
  are) but not at either end; no control character (tab included), DEL or C1 character;
- an Authorization value (in any casing) is a token scheme, one or more spaces, then its credentials, which may be
  several words (Digest parameters); no scheme is parsed further. Authorization itself is not required: a product
  may authenticate with a header of its own.

The dict is copied before it is checked, and the copy, each name in its own casing, is what is sent (urllib may
recase a name on the wire). Neither the provider's message nor a header's name or value is ever put in an error
message, and no encoding error of a header escapes.

An answer that is not JSON (or is nested too deep to decode) is Unreachable on success and an error without a
reason (``Refused`` with the status) otherwise.

``Urania(headers=…)`` passes the provider to the default transport. A custom ``transport`` cannot receive headers
(its signature has no place for them), so giving both is a ValueError rather than a provider silently ignored.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

DEFAULT_URANIA = "http://127.0.0.1:7440"
TIMEOUT = 8.0
MAX_BODY = 1024 * 1024
"""The most bytes of one answer, success or error, the default transport reads."""

Transport = Callable[[str, str, dict | None], tuple[int, object]]
"""(method, path with query, json body or None) -> (status, decoded json)."""

Headers = Callable[[], dict[str, str]]
"""() -> {header name: value}, called once per request by the default transport; the product's credentials."""


class Unreachable(RuntimeError):
    """The other product did not answer: it may be stopped. The caller shows that, and carries on without it."""


class Refused(ValueError):
    """The other product answered no (a bad rule, an unknown file); the message is its reason."""


def _loopback_only(base: str) -> str:
    u = urllib.parse.urlparse(base)
    if u.scheme != "http" or u.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("pheidippides talks to a product on this Mac only (http://127.0.0.1:…)")
    return base.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Every redirect is refused before its target is asked: the base given is the only place a request goes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Unreachable(f"{_origin(req.full_url)} answered with a redirect ({code}); redirects are not followed")


def _origin(url: str) -> str:
    u = urllib.parse.urlparse(url)
    return f"{u.scheme}://{u.netloc}"


def _read(r) -> bytes:
    raw = r.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise Unreachable(f"the answer is larger than {MAX_BODY} bytes")
    return raw


_TCHAR = frozenset("!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


def _token(s: str) -> bool:
    return bool(s) and all(c in _TCHAR for c in s)


def _sendable(v: str) -> bool:
    """Printable ASCII and Latin-1 0xA0..0xFF, spaces inside only: what http.client puts on the wire unchanged."""
    return bool(v) and v[0] != " " and v[-1] != " " and all(" " <= c <= "~" or "\xa0" <= c <= "\xff" for c in v)


def _scheme_and_credentials(v: str) -> bool:
    scheme, _, rest = v.partition(" ")
    return _token(scheme) and rest.lstrip(" ") != ""      # no trailing space, so what is left is the credential


def _credentials(headers: Headers) -> dict[str, str]:
    # The provider's own words never reach an error: they may hold what it read. Its failure is only named, and so
    # is a bad header: never its name or value.
    try:
        got = headers()
    except Unreachable:
        raise Unreachable("the credential provider is unavailable") from None
    except Exception:  # Refused or anything else: never fall back to asking without credentials
        raise Refused("no credentials for the request: the header provider failed") from None
    if not isinstance(got, dict) or not got:
        raise Refused("no credentials for the request: the header provider gave none")
    sent, seen = {}, set()
    for k, v in tuple(dict.items(got)):  # a snapshot of the dict itself: what is checked is what is sent
        if not isinstance(k, str) or not isinstance(v, str):
            raise Refused("no credentials for the request: a header name or value is not text")
        k, v = str.__str__(k), str.__str__(v)            # plain str copies, whatever a subclass would do later
        if not _token(k):
            raise Refused("no credentials for the request: a header name is not an HTTP token")
        if k.lower() in seen:
            raise Refused("no credentials for the request: a header is named twice")
        if not _sendable(v):
            raise Refused("no credentials for the request: a header value cannot be sent as it is")
        if k.lower() == "authorization" and not _scheme_and_credentials(v):
            raise Refused("no credentials for the request: an authorization value needs a scheme and credentials")
        seen.add(k.lower())
        sent[k] = v
    return sent


def _json(raw: bytes):
    """Decoded JSON, or ValueError for anything else, a nesting too deep for the decoder included."""
    try:
        return json.loads(raw or b"null")
    except RecursionError:
        raise ValueError("nested too deep") from None


def http_transport(base: str, *, header: tuple[str, str] | None = None, timeout: float = TIMEOUT,
                   headers: Headers | None = None) -> Transport:
    """The default transport: loopback http only, no proxy, no redirects, answers bounded to MAX_BODY bytes.
    ``header`` is one fixed public header; ``headers`` the product's per-request provider (see the module)."""
    base = _loopback_only(base)
    if headers is not None and not callable(headers):
        raise ValueError("headers is a callable returning a dict of headers")
    # No proxy: a local call must never leave the Mac, whatever the environment says. No redirect either.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def go(method: str, path: str, body: dict | None):
        extra = _credentials(headers) if headers is not None else {}
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if header:
            req.add_header(*header)
        for k, v in extra.items():
            req.add_header(k, v)
        try:
            with opener.open(req, timeout=timeout) as r:
                raw = _read(r)
                try:
                    return r.status, _json(raw)
                except ValueError:
                    raise Unreachable(f"{base} answered {r.status} with something that is not JSON") from None
        except urllib.error.HTTPError as e:
            try:
                raw = _read(e)
            finally:
                e.close()
            if e.code < 400:  # a 3xx the redirect handler does not take (300, 304…) is no answer either
                raise Unreachable(f"{base} answered {e.code}; redirects are not followed") from None
            try:
                payload = _json(raw)
            except ValueError:
                payload = None
            return e.code, payload
        except Unreachable:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise Unreachable(f"no answer from {base}: {getattr(e, 'reason', e)}") from None
    return go


class Urania:
    """Urania's file layer, as another product sees it.

    ``headers``: the product's credential provider for the default transport (see the module); not allowed with a
    custom ``transport``, which owns its own headers."""

    def __init__(self, base: str | None = None, *, transport: Transport | None = None,
                 headers: Headers | None = None):
        if transport is not None and headers is not None:
            raise ValueError("a custom transport carries its own headers; pass headers= only with the default one")
        self.base = base or os.environ.get("URANIA_URL") or DEFAULT_URANIA
        self._t = transport or http_transport(self.base, header=("X-Urania", "1"), headers=headers)

    def _call(self, method: str, path: str, body: dict | None = None):
        status, payload = self._t(method, path, body)
        if status >= 400:
            why = payload.get("error") or payload.get("detail") if isinstance(payload, dict) else None
            raise Refused(str(why or f"urania said {status}"))
        return payload

    # -------------------------------------------------- reading
    def health(self) -> dict:
        return self._call("GET", "/api/bridge/health")

    def fields(self) -> dict:
        """The fields a file rule may use, with what each means."""
        return self._call("GET", "/api/bridge/fields")

    def match(self, rules: list, *, limit: int = 50, offset: int = 0) -> dict:
        """The files a rule (theophrastus conditions over urania's fields) matches: {total, files}."""
        return self._call("POST", "/api/bridge/match", {"rules": rules, "limit": limit, "offset": offset})

    def check(self, rules: list) -> dict:
        """Whether urania understands a rule, and how many files it matches now; Refused with the reason if not."""
        return self._call("POST", "/api/bridge/match", {"rules": rules, "limit": 0})

    def file(self, item_id: int) -> dict:
        return self._call("GET", f"/api/bridge/file/{int(item_id)}")

    def changes(self, *, since: int = 0, rules: list | None = None, limit: int = 200) -> dict:
        """What happened to files after a cursor, in order: {events, cursor}. With rules, only matching files."""
        return self._call("POST", "/api/bridge/changes", {"since": int(since), "rules": rules, "limit": limit})

    # -------------------------------------------------- notes (the one shared store)
    def notes(self, *, status: str = "open", limit: int = 100) -> list[dict]:
        q = urllib.parse.urlencode({"status": status, "limit": limit})
        return self._call("GET", f"/api/notes?{q}")

    def note(self, item_id: int) -> dict:
        """{target, note} for one file."""
        return self._call("GET", f"/api/notes/file/{int(item_id)}")

    def note_marks(self, item_ids: list[int]) -> dict:
        return self._call("POST", "/api/notes/marks", {"kind": "file", "ids": [int(i) for i in item_ids]})

    def save_note(self, item_id: int, body: str) -> dict:
        """The owner's note on a file, written from the owner's own signed-in page; never from an agent's tool."""
        return self._call("POST", f"/api/notes/file/{int(item_id)}", {"body": body})
