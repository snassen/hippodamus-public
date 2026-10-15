"""What a note says, read deterministically, in Swedish and English.

Lifted from talos.notes (shipped in talos 0.24.0, 10 October 2026), where it reads the owner's notes on mail. The
words are the same for every product; a product may pass its own vocabulary (urania adds "rename" for files) without
changing anyone else's.
"""

from __future__ import annotations

import re

BODY_MAX = 20_000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# English then Swedish. Longer phrases are matched first, so "not important" is unimportant, never important, and
# "inte viktigt" likewise.
INTENTS: dict[str, tuple[str, ...]] = {
    "spam": ("spam", "junk", "junk mail", "scam", "phishing", "fraud",
             "skräp", "skräppost", "bluff", "bedrägeri", "nätfiske"),
    "delete": ("delete", "trash", "remove", "throw away", "bin it", "get rid of",
               "radera", "ta bort", "släng", "släng den", "kasta", "papperskorgen"),
    "archive": ("archive", "archive it", "arkivera", "arkivera den"),
    "move": ("move to", "move it", "put in", "folder",
             "flytta", "flytta till", "lägg i", "mapp", "mappen"),
    "important": ("important", "urgent", "priority", "high priority", "vip",
                  "viktig", "viktigt", "viktiga", "brådskande", "akut", "prioritera", "prioriterad"),
    "unimportant": ("not important", "unimportant", "ignore", "don't care", "low priority",
                    "oviktig", "oviktigt", "inte viktig", "inte viktigt", "inte viktiga", "ignorera",
                    "strunta i", "struntar i", "bryr mig inte"),
    "task": ("task", "todo", "to do", "to-do", "follow up", "follow-up", "remind me", "do this",
             "uppgift", "att göra", "att-göra", "följ upp", "påminn mig", "fixa"),
    "reply": ("reply", "answer", "respond", "write back", "svara", "besvara", "svar"),
    "file": ("file", "file it", "file under", "binder", "project",
             "pärm", "pärmen", "projekt", "projektet", "arkivera i", "lägg i pärmen"),
    "label": ("label", "tag", "type", "category", "classify", "mark as",
              "etikett", "etiketten", "märk", "märk som", "tagg", "kategori", "klassa"),
    "newsletter": ("newsletter", "unsubscribe", "nyhetsbrev", "nyhetsbrevet", "avprenumerera",
                   "avsluta prenumeration"),
    "waiting": ("waiting for", "waiting on", "wait for", "väntar på", "vänta på"),
    "done": ("done", "handled", "finished", "taken care of", "klar", "klart", "hanterad", "hanterat", "färdig",
             "färdigt", "avklarad", "avklarat"),
    "reference": ("reference", "keep", "save", "for reference", "referens", "spara", "behåll", "ha kvar"),
    "meeting": ("meeting", "calendar", "book", "schedule", "möte", "mötet", "kalender", "kalendern", "boka"),
}
REACH_WORDS: dict[str, tuple[str, ...]] = {
    "thread": ("thread", "conversation", "the whole thread", "tråd", "tråden", "konversation", "konversationen"),
    "sender": ("sender", "this sender", "from him", "from her", "from them", "all from", "everything from",
               "avsändare", "avsändaren", "från honom", "från henne", "från dem", "allt från", "alla från"),
    "domain": ("domain", "company", "everyone at", "the whole company", "all from the company",
               "domän", "domänen", "företaget", "alla på", "hela företaget", "alla från företaget", "allt från företaget"),
    "always": ("always", "from now on", "in future", "in the future", "every time", "all future",
               "alltid", "framöver", "i fortsättningen", "från och med nu", "varje gång"),
    "subject": ("subject", "title", "headline", "ämne", "ämnet", "ämnesraden", "rubrik", "rubriken"),
}
INTENT_LABELS = {"spam": "Spam", "delete": "Delete", "archive": "Archive", "move": "Move", "important": "Important",
                 "unimportant": "Not important", "task": "Task", "reply": "Reply", "file": "File", "label": "Label",
                 "newsletter": "Newsletter", "waiting": "Waiting", "done": "Done", "reference": "Reference",
                 "meeting": "Meeting"}
REACH_LABELS = {"thread": "the conversation", "sender": "the sender", "domain": "the domain", "always": "from now on",
                "subject": "by subject"}


class NoteError(ValueError):
    """A note or an action that cannot be made; the message says why."""


class Vocabulary:
    """A set of intent and reach words, compiled once. ``DEFAULT`` is talos's; a product may extend it."""

    def __init__(self, intents: dict[str, tuple[str, ...]], reach: dict[str, tuple[str, ...]],
                 intent_labels: dict[str, str], reach_labels: dict[str, str]):
        self.intents, self.reach = intents, reach
        self.intent_labels, self.reach_labels = intent_labels, reach_labels
        self._intent_re = _patterns(intents)
        self._reach_re = _patterns(reach)

    def extended(self, *, intents: dict | None = None, reach: dict | None = None, intent_labels: dict | None = None,
                 reach_labels: dict | None = None) -> "Vocabulary":
        """A new vocabulary with more words; the shared ones are kept as they are."""
        return Vocabulary({**self.intents, **(intents or {})}, {**self.reach, **(reach or {})},
                          {**self.intent_labels, **(intent_labels or {})}, {**self.reach_labels, **(reach_labels or {})})

    def understand(self, body: str) -> dict:
        """The intents and reach words in a note, in the order they were said."""
        text = body or ""
        return {"intents": _found(text, self._intent_re), "reach": _found(text, self._reach_re)}

    def labels(self, understood: dict) -> list[str]:
        """The chips under a note: "Spam", "Delete", "the sender" …"""
        u = understood or {}
        return [self.intent_labels[i] for i in u.get("intents", []) if i in self.intent_labels] + \
               [self.reach_labels[r] for r in u.get("reach", []) if r in self.reach_labels]


def _patterns(words: dict[str, tuple[str, ...]]) -> list[tuple[re.Pattern, str]]:
    """Each word or phrase as a pattern, longest first. A last word of four letters or more takes a short ending, so
    the inflections are found too: arkiveras, raderade, viktigare, deleted, tasks."""
    pairs = sorted(((w, k) for k, ws in words.items() for w in ws), key=lambda p: -len(p[0]))

    def rx(w: str) -> re.Pattern:
        ending = r"\w{0,3}" if len(w.split()[-1]) >= 4 else ""
        return re.compile(r"(?<!\w)" + re.escape(w).replace(r"\ ", r"\s+") + ending + r"(?!\w)", re.IGNORECASE)
    return [(rx(w), k) for w, k in pairs]


def _found(text: str, patterns: list[tuple[re.Pattern, str]]) -> list[str]:
    """The keys whose words appear, in the order they first appear; a longer phrase claims its words first."""
    taken: list[tuple[int, int]] = []
    first: dict[str, int] = {}
    for rx, key in patterns:
        for m in rx.finditer(text):
            if any(m.start() < b and a < m.end() for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            first[key] = min(first.get(key, m.start()), m.start())
    return sorted(first, key=first.get)


DEFAULT = Vocabulary(INTENTS, REACH_WORDS, INTENT_LABELS, REACH_LABELS)


def understand(body: str) -> dict:
    """What the default vocabulary reads in a note."""
    return DEFAULT.understand(body)


def labels(understood: dict) -> list[str]:
    return DEFAULT.labels(understood)


def clean(body, max_len: int = BODY_MAX) -> str:
    """A note's text as it is stored: a string, line ends made plain, trimmed, no control characters, not too long."""
    if body is None:
        return ""
    if not isinstance(body, str):
        raise NoteError("a note is text")
    body = body.replace("\r\n", "\n").strip()
    if _CONTROL.search(body):
        raise NoteError("a note has control characters")
    if len(body) > max_len:
        raise NoteError(f"a note is at most {max_len:,} characters")
    return body
