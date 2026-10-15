"""Xenophon: the owner's notes on anything.

Xenophon wrote down what Socrates said, beside the man himself, without changing him. A note is the owner's own words
beside a thing (a message, a file); the thing never changes.

- ``words``: what a note says (intents and reach, Swedish and English), deterministic.
- ``store``: the one schema notes are kept in, and the operations on it.
"""

from .words import DEFAULT, NoteError, Vocabulary, clean, labels, understand  # noqa: F401
