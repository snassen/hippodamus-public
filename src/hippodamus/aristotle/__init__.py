"""Aristotle: the classification decision core.

Aristotle sorted everything there is into categories, and asked of each thing what it is. This part is how a product
asks a model what a thing is, and how the answer becomes a decision the owner can trust. Lifted from talos 0.26.0,
where it decides mail; urania uses it for files.

- ``taxonomy``: the closed value lists (talos's rules/taxonomy.json shape) and the choice question for a field.
- ``policy``: probabilities to decisions (top at least 0.70 and 0.15 ahead; statements at 0.5), cross-field gates.
- ``sureness``: how sure a value is, in words (Yours, Certain, Confident, Likely, Maybe, Doubtful, Guess).
- ``boundary``: a coarse two-sided line (people or machine, work or personal) from an exact field's scores.
- ``answerkey``: a stratified, deterministic sample, and scoring predictions against the owner's answers.
- ``jev``: the TypeSafe (Jev) client, standard library only; the key comes from the product, read only on a request.
- ``mask``: personal ID numbers, cards, IBANs, phone numbers and URL paths masked before anything leaves the Mac.

What stays in each product: its units (a message, a thread, a file, a folder), the questions' wording and the state
it sends, where decisions are stored, and which gates apply. Nothing here touches a database or a file but the
taxonomy the product points it at.
"""
