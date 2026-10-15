"""The Jev client: TypeSafe's hosted classifier, standard library only.

Lifted from talos.jev (the client half: the request, the retries, usage and cost); talos keeps its records, question
templates and runs, and a product here keeps its own.

**The API**: one case per request, ``POST https://api.typesafe.ai/v1/systemone`` with a Bearer key and the body
``{"state": {...}, "model": "jev-1.13.0", "questions": {...}}``. A question is either

- ``{"type": "choice", "instructions": "...", "criteria": {value: criterion, ...}}`` (``Taxonomy.choice_question``),
  answered ``{"type": "choice", "choice": v, "confidence": p, "probabilities": {value: p, ...}}``, or
- ``{"type": "noul", "instructions": "a statement"}``, answered ``{"type": "noul", "noul": s}`` with s from 0 to 1.

The response is ``{"model", "answers": {name: answer}, "usage": {"input_tokens", "output_tokens"}}``. Input costs
$0.042 per million tokens; output is free.

**The key** comes from the product as a callable (urania and talos read it from the Keychain). It is called on the
first real request only, never when the client is built and never in a dry run; never read from the environment,
never logged, never in an error message or the client's repr.

**Retries**: 429 and 5xx (and 529) are retried up to MAX_RETRIES times, honouring Retry-After up to MAX_DELAY, else
with backoff 0.5, 1, 2, 4… seconds (at most 8); a network failure likewise. A refused key (401, 403) is JevAuthError
at once: nothing else will work.

**The transport** is a seam: ``transport(url, body, headers, timeout) -> (status, headers, body)``, raising OSError
on a network failure. The default is urllib; tests pass a fake. The default follows no redirect (the key is never
carried to another place; a redirect is a JevError before its target is asked) and reads at most MAX_RESPONSE bytes
of an answer, success or error (more is a JevError). ``ask`` refuses a longer body from any transport as well.

**What is accepted** (version 0.3.0 on; anything else is a JevError naming the question, never a quiet fix):

- The response is one JSON object, no key given twice anywhere in it, with ``model`` a string, ``answers`` an object
  and ``usage`` an object whose ``input_tokens`` and ``output_tokens``, when present, are whole numbers ≥ 0 (a
  missing one counts 0, as before).
- A probability or a statement's score is a JSON number (int or float, not a bool, not a string), finite, in 0..1.
  Nothing is clamped, rounded or coerced.
- A choice's probabilities are an object of value → probability. With ``allowed``, every value must be one of them;
  a value Jev left out is allowed only because the ones it gave already sum to 1, and it is not filled in (the
  probabilities come back as given). Exclusive values: they sum to 1 within ``SUM_TOLERANCE`` (1e-6), absolute.

Mask (aristotle.mask) whatever goes into the state before asking.
"""

from __future__ import annotations

import email.utils
import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import timezone
from typing import Callable

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
PRICE_PER_MTOK_INPUT = 0.042     # dollars per million input tokens; output is free
TIMEOUT = 30.0
MAX_RETRIES = 4
MAX_DELAY = 60.0                 # the longest wait between tries, Retry-After included
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
# Estimated tokens per character of the request body, calibrated by talos on its runs: good to ±15%; a real request
# reports the exact usage.
TOKENS_PER_CHAR = 0.345
MAX_RESPONSE = 1024 * 1024       # the most bytes of one answer read, success or error
SUM_TOLERANCE = 1e-6             # how far a choice's probabilities may sum from 1

Transport = Callable[[str, bytes, dict, float], tuple[int, dict, bytes]]


class JevError(RuntimeError):
    pass


class JevAuthError(JevError):
    """The key was refused. Nothing else will work, so a run stops at once."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise JevError(f"TypeSafe answered with a redirect (HTTP {code}); redirects are not followed")


_OPENER = urllib.request.build_opener(_NoRedirect())


def _bounded(r) -> bytes:
    raw = r.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise JevError(f"invalid Jev response: larger than {MAX_RESPONSE} bytes")
    return raw


def urllib_transport(url: str, body: bytes, headers: dict, timeout: float) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, _bounded(r)
    except urllib.error.HTTPError as e:
        try:
            raw = _bounded(e) or b""
        finally:
            e.close()
        if e.code < 400:
            raise JevError(f"TypeSafe answered HTTP {e.code}; redirects are not followed") from None
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, raw


def _no_duplicates(pairs: list) -> dict:
    d = dict(pairs)
    if len(d) != len(pairs):
        raise ValueError("a key is given twice")
    return d


def _no_constant(name: str):
    raise ValueError(f"{name} is not JSON")   # NaN, Infinity: Python's json would take them, JSON does not


def _number01(p) -> bool:
    """A JSON number in 0..1: int or float, not a bool, finite. No strings, no coercion."""
    # The bounds first: a comparison takes any int (10**10000 too), where isfinite would overflow; NaN fails it.
    return isinstance(p, (int, float)) and not isinstance(p, bool) and 0 <= p <= 1 and math.isfinite(p)


def _count(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def encode(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()


def request_body(state: dict, questions: dict, *, model: str = MODEL) -> dict:
    return {"state": state, "model": model, "questions": questions}


def estimate_tokens(body: dict) -> int:
    return round(len(encode(body).decode()) * TOKENS_PER_CHAR)


def cost_usd(usage: dict | int, price: float = PRICE_PER_MTOK_INPUT) -> float:
    """Dollars for a usage ({"input_tokens", …}, or input tokens as a number): input is paid, output is free."""
    tokens = usage if isinstance(usage, (int, float)) else int((usage or {}).get("input_tokens") or 0)
    return tokens * price / 1_000_000


def retry_after(value: str | None, now: float | None = None) -> float | None:
    """Seconds to wait from a Retry-After header: delta-seconds or an HTTP date; None if absent or unreadable."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = time.time() if now is None else now
    return max(0.0, when.timestamp() - now)


@dataclass
class Usage:
    requests: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict:
        return {"requests": self.requests, "retries": self.retries, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens}


class Jev:
    """Asks Jev one case at a time. ``usage`` adds up requests, retries and tokens over the client's life."""

    def __init__(self, key: Callable[[], str] | None = None, model: str = MODEL, url: str = ENDPOINT,
                 transport: Transport | None = None, timeout: float = TIMEOUT, *, dry_run: bool = False,
                 max_retries: int = MAX_RETRIES, max_delay: float = MAX_DELAY,
                 sleep: Callable[[float], None] = time.sleep):
        if key is None and not dry_run:
            raise JevError("a key callable is needed (or dry_run=True)")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        self.model, self.url, self.timeout, self.dry_run = model, url, timeout, dry_run
        self.max_retries, self.max_delay = max_retries, max_delay
        self._key_fn, self._transport, self._sleep = key, transport or urllib_transport, sleep
        self._key: str | None = None
        self.usage = Usage()

    def __repr__(self) -> str:  # never the key
        return f"Jev(model={self.model!r}, url={self.url!r}, dry_run={self.dry_run})"

    def _authorization(self) -> str:
        if self._key is None:
            k = self._key_fn() if self._key_fn else None
            if not k or not isinstance(k, str):
                raise JevError("no TypeSafe key: the key callable gave none")
            self._key = k
        return f"Bearer {self._key}"

    def _fail(self, cls: type[JevError], message: str) -> JevError:
        if self._key:
            message = message.replace(self._key, "[key]")
        return cls(message)

    def backoff(self, attempt: int) -> float:
        return min(0.5 * (2 ** attempt), 8.0, self.max_delay)

    def ask(self, state: dict, questions: dict) -> dict:
        """One case: {"answers", "usage": {"input_tokens", "output_tokens"}, "model"}, after retries. Raises JevError
        (JevAuthError on 401/403). A dry run sends nothing and reads no key: answers None, the estimated usage of
        the request, "dry_run": True and its size in characters."""
        body = request_body(state, questions, model=self.model)
        content = encode(body)
        if self.dry_run:
            return {"answers": None, "usage": {"input_tokens": estimate_tokens(body), "output_tokens": 0},
                    "model": self.model, "dry_run": True, "request_chars": len(content.decode())}
        attempt = 0
        while True:
            headers = {"Authorization": self._authorization(), "Content-Type": "application/json",
                       "Accept": "application/json"}
            self.usage.requests += 1
            try:
                status, rheaders, raw = self._transport(self.url, content, headers, self.timeout)
            except OSError as exc:
                if attempt >= self.max_retries:
                    raise self._fail(JevError, f"network failure after {attempt} retries: {type(exc).__name__}") \
                        from None
                self._sleep(self.backoff(attempt))
                attempt += 1
                self.usage.retries += 1
                continue
            if status in RETRY_STATUSES and attempt < self.max_retries:
                wait = retry_after({k.lower(): v for k, v in (rheaders or {}).items()}.get("retry-after"))
                self._sleep(min(self.max_delay, wait if wait is not None else self.backoff(attempt)))
                attempt += 1
                self.usage.retries += 1
                continue
            if status in (401, 403):
                raise self._fail(JevAuthError, f"TypeSafe refused the key (HTTP {status})")
            if status != 200:
                raise self._fail(JevError, f"TypeSafe HTTP {status}" + (f" after {attempt} retries" if attempt else ""))
            if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_RESPONSE:
                raise self._fail(JevError, f"invalid Jev response: not bytes, or larger than {MAX_RESPONSE} bytes")
            try:
                data = json.loads(raw, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
            except (TypeError, ValueError, RecursionError):
                raise self._fail(JevError, "invalid Jev response: not JSON, or a key given twice") from None
            if not isinstance(data, dict) or not all(k in data for k in ("model", "answers", "usage")):
                raise self._fail(JevError, "invalid Jev response: model, answers or usage missing")
            u = data["usage"]
            if not isinstance(data["model"], str) or not isinstance(data["answers"], dict) or not isinstance(u, dict):
                raise self._fail(JevError, "invalid Jev response: model, answers or usage of the wrong kind")
            if not all(_count(u[k]) for k in ("input_tokens", "output_tokens") if k in u):
                raise self._fail(JevError, "invalid Jev response: a token count is not a whole number ≥ 0")
            usage = {"input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0)}
            self.usage.input_tokens += usage["input_tokens"]
            self.usage.output_tokens += usage["output_tokens"]
            return {"answers": data["answers"], "usage": usage, "model": data["model"]}


def choice_probabilities(answers: dict, name: str, allowed=None) -> dict[str, float]:
    """A choice answer's probabilities, checked (see the module): each a number in 0..1, only allowed values if
    given, together summing to 1 within SUM_TOLERANCE. Returned as given (ints stay ints), missing values not added."""
    a = answers.get(name) if isinstance(answers, dict) else None
    probs = a.get("probabilities") if isinstance(a, dict) else None
    if not isinstance(probs, dict) or not probs:
        raise JevError(f"invalid Jev response: no probabilities for {name}")
    for v, p in probs.items():
        if not isinstance(v, str):
            raise JevError(f"invalid Jev response: {name} has a value that is not text")
        if not _number01(p):
            raise JevError(f"invalid Jev response: a probability of {name} is not a number in 0..1")
    if allowed is not None:
        unknown = [v for v in probs if v not in allowed]
        if unknown:
            raise JevError(f"invalid Jev response: {name} has an unknown value {unknown[0]!r}")
    total = math.fsum(probs.values())
    if abs(total - 1.0) > SUM_TOLERANCE:
        raise JevError(f"invalid Jev response: the probabilities of {name} sum to {total:.6f}, not 1")
    return dict(probs)


def statement_score(answers: dict, name: str) -> float:
    """A noul answer's score: a number (not a bool or a string) in 0..1 (talos.jev._noul, stricter)."""
    a = answers.get(name) if isinstance(answers, dict) else None
    if not isinstance(a, dict) or "noul" not in a:
        raise JevError(f"invalid Jev response: no score for {name}")
    s = a["noul"]
    if isinstance(s, bool) or not isinstance(s, (int, float)):
        raise JevError(f"invalid Jev response: the score for {name} is not a number")
    if not _number01(s):
        raise JevError(f"invalid Jev response: the score for {name} is outside 0..1")
    return float(s)
