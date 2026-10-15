# Hippodamus

*Hippodamus of Miletus laid cities out on one grid, so every quarter could reach every other.*

Shared parts for local-first products that sit side by side on one machine. Each is a small, swappable seam named
after a classical Greek person:

| Part | What it is | Used by |
|---|---|---|
| `solon` | whose value counts: human > rule > accepted model > import | talos, urania (conformance tests) |
| `xenophon` | the owner's notes: one vocabulary (Swedish and English), one store schema | talos (words), urania (file notes) |
| `theophrastus` | rules as data, compiled to SQL over a product's own fields | urania (binder file rules) |
| `pheidippides` | the runner: one product asks another over its local API | talos → urania |
| `thucydides` | the change feed: what happened since a cursor | urania (feed), talos (file watchers) |
| `aristotle` | the classification decision core: taxonomy, policy, sureness, boundaries, answer key, Jev client, masking | talos (mail, source), urania (files) |

"The owner" in the code and docs means whoever runs a product on their own machine: the person whose decisions count
above any rule or model.

## Install

Hippodamus needs Python 3.14 or newer and has no runtime dependencies. It is not on PyPI; a product pins an exact,
immutable git tag (or commit) of this repository, so a release keeps the version it was tested with:

```toml
# the product's pyproject.toml
[project]
dependencies = ["hippodamus"]

[tool.uv.sources]
hippodamus = { git = "<repository-url>", tag = "vX.Y.Z" }
```

or, without uv's sources table:

```bash
uv pip install "hippodamus @ git+<repository-url>@vX.Y.Z"
python -c "import hippodamus; print(hippodamus.__version__)"
```

`<repository-url>` is wherever you get Hippodamus from (a clone, a mirror, a `file://` path). Prefer the full commit
SHA over the tag name when you need the pin to be exactly reproducible, and read `CHANGELOG.md` before moving a pin:
some releases need changes in the product (see the migration notes there).

A product that uses the database parts (`xenophon.store`, `theophrastus`, `thucydides`) passes its own psycopg 3
connection; Hippodamus never opens one itself.

## Develop and test

See `CONTRIBUTING.md`. In short: `uv sync`, start a disposable PostgreSQL on port 5441, then
`HIPPODAMUS_TEST_DSN="host=/tmp port=5441 dbname=hippodamus_test" uv run pytest -q`.

## More

- `CONTRIBUTING.md`: how parts get here, how to set up and test, and the rules for a change.
- `docs/attribution.md`: what each part took from talos, and talos's license notice.
- `SECURITY.md`: how to report a vulnerability.
- `CHANGELOG.md`: what changed in each release, with migration notes.

## License

MIT, the same license as talos: see `LICENSE`. Parts taken from talos keep their attribution in their module
docstrings, and `docs/attribution.md` says, module by module, what was taken and how.
