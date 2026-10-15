# Contributing

Only what is shared and proven moves in: a part comes here when two products already do the same thing, or when a
bridge between them needs one seam. One product's idea stays in that product until a second one needs it. Each part
is one seam with a classical Greek person's name, so a product can swap one without touching the others; a new part
gets the next name and a line in `src/hippodamus/__init__.py` saying what it is.

## Set up

You need [uv](https://docs.astral.sh/uv/), Python 3.14 or newer and, for the database tests, PostgreSQL 15 or newer.

```bash
git clone <repository-url> hippodamus
cd hippodamus
uv sync --locked          # the dev group: pytest and psycopg; the package itself has no runtime dependencies
```

## A scratch database

The database tests drop and create their own tables, so they run only on a disposable database. The guard in
`tests/conftest.py` refuses any DSN that is not a local server (a socket directory or loopback) on port 5441 with a
database whose name begins `hippodamus_test`. The suite is meant to run with no skips: a missing database or
psycopg is a skip, so treat any skip as a failed run.

A throwaway cluster, created in a temporary directory and removed afterwards:

```bash
scratch="$(mktemp -d)"
initdb -D "$scratch/data" -U "$USER" --auth=trust >/dev/null
pg_ctl -D "$scratch/data" -o "-p 5441 -k $scratch -c listen_addresses=''" -l "$scratch/log" -w start
createdb -h "$scratch" -p 5441 hippodamus_test

HIPPODAMUS_TEST_DSN="host=$scratch port=5441 dbname=hippodamus_test" uv run pytest -q -rs

pg_ctl -D "$scratch/data" -w stop && rm -rf "$scratch"
```

Or with a container: `docker run --rm -d -p 127.0.0.1:5441:5432 -e POSTGRES_HOST_AUTH_METHOD=trust
-e POSTGRES_DB=hippodamus_test postgres:17`, then
`HIPPODAMUS_TEST_DSN="host=127.0.0.1 port=5441 user=postgres dbname=hippodamus_test"`.

If you already run a scratch cluster on 5441, give each checkout its own database name
(`hippodamus_test_<something>`) so parallel runs do not drop each other's tables, and drop it when you are done.

## Changes

- Tests are sentences: a test's name says the behaviour it holds.
- A part stays free of product data and product tables; a product passes its connection, field map or target in.
- Fixtures use invented values only (`example.invalid`, `127.0.0.1`, made-up names). Never real mail, files, paths,
  host names, accounts or credentials, and never a call to a real model or service: tests use fake transports.
- A change in behaviour is a new minor version and an entry in `CHANGELOG.md`, with migration notes when a product
  must change too. A published tag is never moved or reused; a fix is a new tag.
- CI (`.github/workflows/tests.yml`) runs the whole suite on a disposable PostgreSQL service and fails on any skip.

## Security

Do not open a public issue for a vulnerability; see `SECURITY.md`.
