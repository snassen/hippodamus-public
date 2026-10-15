# Security

## Reporting a vulnerability

Please report vulnerabilities privately, not in a public issue, pull request or discussion: use the repository
host's private vulnerability reporting (on GitHub: the Security tab, "Report a vulnerability") or contact the
maintainers privately. Include the affected part and version or commit, what an attacker can do, and a minimal
reproduction with invented data.

Never include real credentials, tokens, personal data, mail, documents, file paths from your machine or screenshots of
them. If a report needs such material, say so and we will agree on a private channel first.

## Scope

Hippodamus is a library; it opens no network port and starts no service. The boundaries it is responsible for:

- `pheidippides` talks to a product on the same machine only (loopback HTTP), follows no redirect, uses no proxy,
  bounds the size of what it reads and refuses malformed credential headers before anything is sent.
- `aristotle.jev` sends a classification request with a key the product supplies as a callable; `aristotle.mask`
  masks identifiers before text leaves the machine.
- `theophrastus` compiles stored rules to parameterised SQL and bounds a rule's size and depth.
- `thucydides` and `xenophon.store` write only to tables a product created with their schema, through the
  product's own connection.

Where a product keeps its secrets, who may call its API and how it authenticates a request are the product's own
responsibility.

## Supported versions

Fixes go into a new release; an existing tag is never moved. Products should pin the newest tag and read the
migration notes in `CHANGELOG.md` before moving.
