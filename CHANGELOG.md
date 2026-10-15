# Changelog

## [0.3.2] - 2026-10-15

Tests only; no change in behaviour, API or packaging beyond the version number. 0.3.1 was tagged privately but never
published: a public copy's privacy scan read the trace check's own home-path patterns and its invented example as home
directories. The patterns are now written so they do not look like a path (they match the same text), the example
uses an invented generic name, and a Linux example is checked too.

## [0.3.1] - 2026-10-15

Licence, package metadata, documentation and CI only. No change in behaviour or API: the code is the same as 0.3.0
apart from the version number, and no product needs to change anything to move its pin.

- License: MIT, the same notice as talos (`LICENSE`), declared in the package metadata (`license = "MIT"`,
  `license-files = ["LICENSE"]`), and carried in both the wheel and the sdist. `docs/attribution.md` maps what
  theophrastus, xenophon and aristotle took from talos (literal, adapted or the idea), keeps talos's MIT notice and
  ships in the sdist. Tests hold the license file, the metadata and the attribution page.
- Docs read as a generic project and stand on their own: a neutral README with installation by pinned tag,
  `CONTRIBUTING.md` (how parts get here, setup, a disposable scratch database, the rules for fixtures and changes)
  and `SECURITY.md` (private reporting). The package description is neutral and the README is its long description.
- CI (`.github/workflows/tests.yml`): the full suite on Python 3.14 with uv and a disposable PostgreSQL service on
  port 5441; any skip fails the run. No secrets, models or data.
- Tests that hold this: no shipped file or built artifact carries a personal or instance trace, the metadata stays
  neutral with no runtime dependencies, and the wheel installs and imports every part from a clean home.
- Public copies: a public copy of a release carries the package, its tests and these docs. The maintainers' own
  working notes and release tooling stay in the private repository and are left out of every public copy; they are
  not part of the package and change nothing in it.

## 0.3.0 (13 October 2026)

Safety fixes to four seams. **Breaking for every feed producer** (thucydides) and stricter for Jev answers and
rules; read the migration notes before moving a pin.

- thucydides: **events are written only inside `writer`.** `with thucydides.writer(conn, table=…) as w:` begins the
  transaction itself (the connection must have none open: psycopg autocommit, or idle), at READ COMMITTED, and first
  takes a transaction-scoped advisory lock keyed on the feed table's OID; the product's own change and `w.record(…)`
  follow, and the block commits or rolls back both together. Writers of one feed are serialized, so event numbers
  become visible in order and a cursor can no longer pass a number that commits later. `record` outside a writer
  of the same connection and table, after the block, or after a raw COMMIT inside it is a FeedError, even with the
  lock taken by hand; entering a writer inside an open transaction or another writer is a FeedError. A writer object
  whose block ended stays unusable, even while a newer writer runs on the same connection. The writer pins the
  table it locked (inserts go to its resolved, quoted schema and name), and a `record` whose table name now
  resolves elsewhere (a `set local search_path` inside the block) is a FeedError; a rename inside the block fails
  the insert and rolls the block back.
- thucydides: `events(conn, cursor)`, the unfiltered read without a join to the things (a move out of a filter is
  still there). `since` is unchanged and documented for what it is: the events of things that match the filter
  *now*, not membership transitions. The module documents the watcher algorithm (durable membership in the
  product, transitions by state, restart by replaying a page, rule changes as a recompute from a read head).
- pheidippides: the default transport follows no redirect (refused before its target is asked) and reads at most
  1 MiB of an answer, success or error; a success that is not JSON, or JSON nested too deep to decode, is
  Unreachable (was an uncaught ValueError or RecursionError); such an error body gives no reason (Refused with the
  status). `Urania(headers=…)` and `http_transport(headers=…)` take the product's credential provider, a callable
  returning a `{name: value}` dict, called once per request; a missing, failing, blank or malformed one (an
  Authorization value needs a scheme and credentials) is Refused, a provider's Unreachable is a generic Unreachable,
  and the request is not sent. The provider's own message never reaches the error. A custom transport keeps its own
  policy; giving it `headers=` too is a ValueError.
- pheidippides (correction to the entry above, before any tag): the provider's headers are checked as a whole before
  anything is sent, so urllib can no longer mangle or choke on them. A name is an HTTP token, taken as given, and no
  name may appear twice in any casing (two Authorization headers are refused, not merged); a value is printable ASCII
  or Latin-1 0xA0..0xFF, spaces inside only, no control character (tab included), DEL or C1; an Authorization value
  is a token scheme, spaces, then credentials (Digest parameters allowed). A name with a space or colon, or a value
  outside Latin-1, was sent anonymously or raised a raw ValueError or UnicodeEncodeError; now each is
  `Refused("no credentials …")` naming neither the header nor its value. Authorization is not required (a product's
  own header will do); the dict is copied before it is checked and sent in the provider's own casing.
- theophrastus: one iterative, bounded walk checks a rule before it is counted or compiled: depth 8, 200 conditions,
  2,000 parts in all (a list's length is counted before its items), `in` at most 2,000 values. A malformed, cyclic,
  too deep or too large rule is a RuleError, never a RecursionError or TypeError, and never skipped. Only a list is
  a rule: `[]` still matches nothing, but `None`, `{}`, `False`, `0` or `""` are now RuleErrors (were "false").
  `leaves` takes a rule or one condition object and refuses malformed parts (a bare number, a list in a list, an
  `any` that is not a non-empty list, a condition without a text field and a known operator) where it used to skip
  them. Valid rules compile to the same SQL as before.
- aristotle: a probability or statement score is a JSON number (not a bool or a string), finite, in 0..1; nothing is
  coerced or clamped, and an enormous integer is refused by its bounds (no OverflowError, no digits in the error). A choice's probabilities sum to 1 within 1e-6, unknown values are refused, a value left out is
  allowed only when the rest still sum to 1 (and is not filled in). The response must be a JSON object without a
  repeated key or NaN, with `model` a string, `answers` an object and whole, non-negative token counts (a missing
  count is still 0). The default transport follows no redirect (the key never goes elsewhere) and reads at most
  1 MiB; `ask` refuses a longer body from any transport. `policy.route_*` refuse a score outside 0..1 with
  PolicyError whoever passes it (the sum is the parser's check, so talos's hand-made cases route as before).
- `hippodamus.__version__` says 0.3.0 (it had stayed at 0.1.0 through 0.2.0); the package metadata agrees.

Migration:

- **Every feed producer** (urania's indexer, its action and undo paths, anything that calls `thucydides.record`):
  wrap the product change and its events in `with thucydides.writer(conn, table=…) as w:` on a connection with no
  transaction open, and call `w.record` (or `thucydides.record(conn, …)`) inside it. Do not take product row locks
  before entering. A raw insert into the feed table bypasses the protocol and can make a reader skip an event.
- Watchers that need "entered/left" use `events` and their own durable membership, as the module describes.
- Callers of `compile_conditions` or `leaves` that passed `None` or `{}` for "no rule", or stored rules with junk
  parts that `leaves` used to skip: pass `[]`, and map RuleError to a 4xx.
- Products that call urania with a token pass `headers=` (a Keychain read per request is the product's); tests that
  pass a fake `transport` change nothing. A provider must give each header once, in one casing, with a value that
  needs no trimming: pheidippides refuses rather than repairs.
- Products that route Jev answers: expect JevError on answers older versions accepted (string numbers, NaN, sums
  off 1 by more than 1e-6); route your own scores only after the same checks.

## 0.2.0 (11 October 2026)

- aristotle: the classification decision core, lifted from talos 0.26.0 so urania can classify files with the
  engine talos uses for mail. taxonomy (talos's rules/taxonomy.json shape, the choice question), policy (talos's
  routing rule; gates are the product's own callables), sureness and boundary (unchanged, without the SQL), mask
  (unchanged), answerkey (a stratified sample and a score per field) and jev (the TypeSafe client on the standard
  library, the key a callable the product supplies).

## 0.1.0 (11 October 2026)

- solon, xenophon, theophrastus, pheidippides and thucydides: the first parts, for the talos–urania bridge.
- xenophon's words and theophrastus's compiler are lifted from talos 0.24.1 unchanged.
