# Attribution: what came from talos

Three parts of Hippodamus were taken from talos: `theophrastus`, `xenophon` and `aristotle`. This page says, module
by module, what was taken and how. `pheidippides` and `thucydides` were written here for the bridge. `solon` was
written here too, as an **idea** shared by both products: it states the precedence talos's `effective_assignment`
view (`src/talos/sql/001_foundation.sql`) and urania's `effective_labels` view already ranked (human 4, rule 3,
model 2, import 1, newest first inside a class), and `rank_sql` builds the same `case` ranking.

## The sources

| Source | Read at | Used for |
|---|---|---|
| talos v0.24.1 | commit `2128e53b52c3cce83142d798bf8edb3559ba8131` | theophrastus, xenophon (and solon's idea) |
| talos 0.26.0 | commit `802e8158f9a1838f923a2c6234c93d421a8cc890` | aristotle |

Both are released by The Talos contributors under the MIT License. Its notice, kept as the source states it:

```text
MIT License

Copyright (c) 2026 The Talos contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Hippodamus is released under the same notice (`LICENSE`), with the same copyright holder.

## How to read the tables

- **Literal**: the code was copied. It may have a new name, a different docstring or a module-level name changed
  to fit, but its logic is the same line for line.
- **Adapted**: the talos code was the starting point. Its logic is still recognisable, but it was reshaped: talos
  tables and talos settings were taken out, a product passes them in, and checks were added.
- **Idea**: talos had the idea or the contract. The code here was written new and does not follow talos's code.
- **New**: there is no talos source.

"Not taken" lists what stayed in talos from the same source file.

## theophrastus (`src/hippodamus/theophrastus.py`)

From talos `src/talos/rules.py` at `2128e53`.

| Hippodamus | Talos | How |
|---|---|---|
| `TEXT_OPS`, `OPS`, `RuleError` | `TEXT_OPS`, `OPS`, `RuleError` | literal |
| `like` | `_like` | literal (made public) |
| `cmp` | `_cmp` | literal, plus: `in` refuses an empty list and more than `MAX_NODES` values |
| `compile_conditions`, `_all`, `_one` | `compile_conditions`, `_all`, `_one` | adapted: same SQL shapes and `any` groups; the fields come from the product (`Fields`) instead of talos's `FIELDS` and `EXISTS_FIELDS` maps of its message tables; talos's `body`, `label` and `header:` special cases dropped (a product can pass a `Custom` field); depth bound; only a list is a rule; a range comparison refuses a collection |
| `leaves` | `leaves` | idea: the same result (every plain condition, in order); rewritten as a bounded, checked walk that refuses malformed input |
| `check` | `check` | idea: the same name and purpose; talos's runs the rule against PostgreSQL, this one checks without a database |
| `Column`, `Exists`, `Custom`, `Fields`, `MAX_DEPTH`, `MAX_LEAVES`, `MAX_NODES`, `_walk` | none | new |

Not taken: `Rule`, `FIELDS`, `EXISTS_FIELDS`, `RULE_REF`, `preview`, `save`, `remove`, `remove_all`, `removed`,
`read_file`, `load_file`, `load`, `run_all`, `_apply`, `assign`, `_validate_action`.

## xenophon (`src/hippodamus/xenophon/`)

From talos `src/talos/notes.py` and `src/talos/sql/044_owner_note.sql` at `2128e53`.

### `words.py`

| Hippodamus | Talos (`notes.py`) | How |
|---|---|---|
| `INTENTS`, `REACH_WORDS`, `INTENT_LABELS`, `REACH_LABELS` | the same | literal |
| `BODY_MAX`, `_CONTROL`, `NoteError` | the same | literal |
| `_patterns`, `_found` | the same | literal |
| `Vocabulary.understand`, `understand` | `understand` (with `_INTENT_RE`, `_REACH_RE`) | adapted: the patterns belong to a vocabulary object; the module function uses `DEFAULT` |
| `Vocabulary.labels`, `labels` | `labels` | adapted, as above |
| `clean` | `_clean` | adapted: the limit is a parameter |
| `Vocabulary`, `Vocabulary.extended`, `DEFAULT` | none | new |

### `store.py`

| Hippodamus | Talos | How |
|---|---|---|
| `ddl` | `044_owner_note.sql` | adapted: the same two tables, columns, types, defaults, checks and indexes; the allowed target kinds are a parameter, `if not exists` added, comments left out |
| `COLUMNS` | `044_owner_note.sql` | adapted: the column lists of the two tables |
| `STATUSES`, `SUMMARY_MAX` | `notes.STATUSES`, `notes.SUMMARY_MAX` | literal |
| `_row`, `counts`, `set_status` | the same | literal (`_row` reads through `_one`; `set_status` passes the vocabulary on) |
| `save` | `save` | adapted: the same rules (one note per target, empty is no note, emptied with actions is withdrawn, a change reopens); the product resolves the target (`Target`) instead of talos's `target()`, JSON is passed as text instead of psycopg's `Jsonb` |
| `get` | `get` | adapted: without talos's action summaries and undo flags |
| `listing` | `listing` | adapted: a kind filter instead of id filter and target lookup |
| `answer` | the summary and done update in `act` | adapted |
| `record_action` | the `note_action` insert in `act` | adapted |
| `mark_undone` | the last update in `undo` | adapted: records the undo; the product does the undoing |
| `marks` | `marks` | idea: by target key only; talos's message and thread joins stay in talos |
| `find` | `for_target` | idea |
| `Target`, `check_schema`, `_j`, `_one` | none | new |

Not taken: `TARGET_KINDS` (a product passes its own), `target`, `reach`, `act`, `_one`, `_scope`, `_pick`, `_rule`,
`move`, `for_target`, `undo`, `action_summary`, `_undoable`, `PUBLIC_DOMAINS` and the other mail-specific limits.

## aristotle (`src/hippodamus/aristotle/`)

From talos at `802e815`. `aristotle/__init__.py` holds a docstring only.

### `mask.py`

From `src/talos/mask.py`. **Literal**: every name and line of code is the same, `VERSION` 1 included. The docstring
was edited (a talos doc reference removed, the lift noted).

### `sureness.py`

From `src/talos/sureness.py`, the Python side.

| Hippodamus | Talos | How |
|---|---|---|
| `LEVELS` | `LEVELS` | literal, with two meanings reworded to leave out talos's model name and product name |
| `KEYS`, `FLOOR`, `LABEL`, `SOURCE_CONFIDENCE`, `MODEL_WITHOUT_CONFIDENCE`, `SurenessError` | the same | literal |
| `check`, `confidence_of`, `level_of`, `at_least`, `levels` | the same | literal |

Not taken: `confidence_sql`, `level_sql`, `floor_sql`, `SOURCE_WORD`, `basis`.

### `boundary.py`

From `src/talos/boundary.py`, the pure part.

| Hippodamus | Talos | How |
|---|---|---|
| `BOUNDARY_MIN` | the same | literal |
| `side_probabilities`, `decide` | the same | literal |

Not taken: `FIELDS`, `KIND`, `DERIVED`, `SOURCE`, `OF`, `KIND_MIN`, `EXACT_MIN`, `threshold`, `BoundaryError`, `sides`,
`value_sides`, `DERIVED_SQL`, `params`.

### `policy.py`

From `src/talos/policy.py` (its VERSION 2 rule). Hippodamus numbers its own policy (`VERSION` 1 here); the number is
not talos's.

| Hippodamus | Talos | How |
|---|---|---|
| `NONE`, `Policy`, `DEFAULT` | the same | literal |
| `Decision` | `Decision` | literal fields; the properties `candidate`, `probabilities`, `value` are new |
| `route_one`, `route_many`, `route_choice_many`, `route` | the same | adapted: the same rule and thresholds; every score is checked first (`_checked`) |
| `gate`, `route_case` | `gate`, `route_case` | adapted: the product passes its gates and its many-value fields; talos's mail gates (`ASK_ORIGINS`, `WORK_TOPIC`, `GATES`, `CHOICE_MANY`) and `template` stay in talos |
| `PolicyError`, `_checked`, `Gate` | none | new |

### `taxonomy.py`

From `src/talos/taxonomy.py` and `src/talos/jev.py`.

| Hippodamus | Talos | How |
|---|---|---|
| `CARDINALITIES`, `TaxonomyError`, `_no_duplicates` | `taxonomy.py`, the same | literal |
| `_plain`, `Dimension.criterion` | `jev._plain`, `jev._criterion` | literal logic, on a `Dimension` instead of a value dict |
| `_dimension`, `Taxonomy.from_dict`, `from_json`, `load` | `taxonomy.read` | adapted: no database and no talos paths; `family` and `label` optional; a plain `{value: definition}` map accepted |
| `_check_derived`, `_check_value_map` | the same | adapted: the same checks over `Dimension` objects; a source value without a family is refused |
| `Taxonomy.side_of` | `taxonomy.mapping` | adapted |
| `Taxonomy.choice_question` | the criteria built in `jev._build_v2` | adapted: values as criteria, `none` first when asked for; talos's instruction templates stay in talos |
| `Dimension`, `Taxonomy` (the classes), `Taxonomy.check`, `dimension` | none | new |

Not taken: `DEFAULT_PATH`, `current`, `GOLD_FIELDS`, `in_use`, `load` (the database load), `format_summary`.

### `jev.py`

From `src/talos/jev.py`, the client half. Talos's client is asynchronous over httpx; this one is synchronous over
the standard library.

| Hippodamus | Talos | How |
|---|---|---|
| `ENDPOINT`, `MODEL`, `PRICE_PER_MTOK_INPUT`, `MAX_RETRIES`, `MAX_DELAY`, `RETRY_STATUSES`, `TOKENS_PER_CHAR` | the same | literal |
| `JevError`, `JevAuthError`, `Usage`, `encode`, `estimate_tokens`, `retry_after` | the same | literal |
| `TIMEOUT` | `TIMEOUT` | adapted: a number of seconds instead of an httpx timeout |
| `request_body` | `request_body` | adapted: the product builds the whole state (talos adds its recipient text) |
| `cost_usd` | `cost` | adapted: also takes a usage dict |
| `Jev`, `Jev.ask`, `Jev.backoff`, `Jev.__repr__`, `Jev._authorization` | `JevClient`, `decide`, `backoff`, `__repr__`, `_authorization` | adapted: one request at a time; the key is a callable the product passes, not talos's Keychain lookup; strict response checks (no duplicate keys, no NaN, typed token counts, bounded size); a key never appears in an error |
| `choice_probabilities` | `_choice` | adapted: stricter (numbers only, in 0..1, summing to 1) |
| `statement_score` | `_noul` | adapted: stricter (numbers only) |
| the API description in the docstring | the module docstring | adapted |
| `Transport`, `urllib_transport`, `_NoRedirect`, `_OPENER`, `_bounded`, `MAX_RESPONSE`, `SUM_TOLERANCE`, `_number01`, `_count`, `_no_constant`, `_no_duplicates` | none | new |

Not taken: records and their masking, question templates and question sets, `question_version`, `read_answers`,
`taxonomy_from_db`, `JevClient.run` and its concurrency, `Result`, `now_stamp`, talos's wording and settings.

### `answerkey.py`

From `src/talos/gold.py`.

| Hippodamus | Talos | How |
|---|---|---|
| `_split` | `_split` | literal |
| `_show` | `_show` | adapted: values shown as text |
| `allocate`, `stratified_sample` | `sample` | idea: a stratified, seeded, deterministic draw, shuffled so strata never come in blocks; talos's strata, groups and SQL stay in talos |
| `score` | `compare` | idea: coverage kept apart from accuracy, and the wrong `predicted→truth` pairs counted; talos's per-value, per-source and per-bucket reports stay in talos |
| `_values` | none | new |

Not taken: everything that reads or writes the database (sets, items, labels, labellers, reveal, reports, imports,
checks, groups).

## Other notices

No file in this repository carries another copyright or license notice, and none was found in the talos files
listed above. This page covers code taken from talos only: it is not a certificate that nothing else in the
repository came from elsewhere. Algorithms and standards named in the code (the Luhn check, ISO 13616 IBANs, the HTTP
`Retry-After` header) are described there by name; no third-party code for them is included.

When a part takes more from a product, add its lines here in the same change.
