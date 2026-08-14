"""Anchored queries: the query half of traverse-query.

Browse gives the user a concrete movie (the *anchor*). The router speaks
text queries ("the absolute opposite of die hard", "90s action movies").
The bridge between the two is a small, visible set of synthesis templates
in `synthesize()` -- every synthesis is returned to the UI and shown to
the user, so nothing happens behind their back.

    browse:  Apollo 13 ──▶ anchor it ──▶ type "the opposite"
    bridge:  synthesize → "the absolute opposite of Apollo 13"
    router:  INVERSION branch → ranked results
    back:    click a result → that movie is a new node to keep browsing

The interesting open problem is *relative attributes*: "happier" against
Apollo 13 should shift the anchor's attribute vector along the happy axis,
and "darker" along the dark axis. The router today inverts absolute axes
("opposite of X") and filters on explicit phrases; it has no delta-from-
anchor primitive. Until it does, free text rides the default template and
the router's own ambiguity note says how well that read landed. That gap
is written down in PROBLEM.md.

Why text at all, when the anchor is a concrete id? Because the router's
plan machinery -- matchers, filters, exclusion, the escalation gate -- is
keyed on text spans. Reusing it means every existing branch (similarity,
inversion, popularity, franchise) works against the anchor for free, and
the synthesis is the only new surface to audit.
"""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------
# Synthesis: anchor + user text → router query string.
# First match wins; every rule returns the query and the rule that fired.
# --------------------------------------------------------------------------

_OPPOSITE_RE = re.compile(
    r"^(?:the\s+|an?\s+|really\s+|exact(?:ly)?\s+|absolute\s+|total(?:ly)?\s+)*"
    r"(opposite|inverse|reverse|invert(?:ed)?|flip(?:ped)?|mirror)"
    r"(?:\s+of)?\s*(?:this|that|it|him|her)?\s*$",
    re.IGNORECASE,
)
# "nothing like this" / "not like this" are the everyday way people ask
# for the opposite without using the word. The prefix is REQUIRED so bare
# "like this" stays a more-like ask.
_OPPOSITE_PHRASE_RE = re.compile(
    r"^(?:nothing|not|anything)\s+like\s+(?:this|that|it)\s*$",
    re.IGNORECASE,
)
# "sequel", "prequel", "spin-off" -- the router's franchise machinery
# resolves "the sequel to X" into the year-ordered roster.
_RELATION_RE = re.compile(
    r"^(?:the\s+)?(sequel|prequel|spin[- ]off|part\s+2|part\s+two)"
    r"(?:\s+of|\s+to)?\s*(?:this|that|it)?\s*$",
    re.IGNORECASE,
)
# "like this but funnier" -- "like this" is scaffolding, "funnier" is the
# real modifier, so this goes to the free branch (anchor + modifier), not
# to more-like. Bare "more like this" (no modifier) stays more-like.
_SCAFFOLD_AND_MODIFIER_RE = re.compile(
    r"^(?:(?:more|very|quite|really|kinda|kind\s+of)\s+)?"
    r"like\s+(?:this|that|it)\b.*\b(?:but|and)\b",
    re.IGNORECASE,
)
# "more like this", "similar", "same vibe", "another one", bare "like this".
_MORE_LIKE_RE = re.compile(
    r"^(?:(?:more|very|quite|really|kinda|kind\s+of)\s+)?"
    r"like\s+(?:this|that|it)$"
    r"|^similar\b|^same\s+vibe\b|^same\s+energy\b|^another\b|^recommend\b",
    re.IGNORECASE,
)


def synthesize(anchor_title: str, text: str) -> tuple[str, str]:
    """Turn `text` typed against `anchor_title` into a router query.

    Returns (query, kind) where kind names the rule that fired -- shown in
    the UI and used by the tests as the contract. Never raises; the
    default template catches everything.
    """
    text = text.strip()
    if not anchor_title:
        return text, "passthrough"
    if not text:
        return f"like {anchor_title}", "more_like"

    # The user said the anchor themselves -- "opposite of apollo 13" is
    # already a complete router query. Double-injecting would corrupt it.
    if anchor_title.casefold() in text.casefold():
        return text, "passthrough"

    if _OPPOSITE_RE.match(text) or _OPPOSITE_PHRASE_RE.match(text):
        return f"the absolute opposite of {anchor_title}", "opposite"

    m = _RELATION_RE.match(text)
    if m:
        rel = m.group(1).replace(" ", "-")
        return f"the {rel} to {anchor_title}", "relation"

    if _SCAFFOLD_AND_MODIFIER_RE.match(text):
        return _free_with_anchor(anchor_title, text)

    if _MORE_LIKE_RE.match(text):
        return f"like {anchor_title}", "more_like"

    return _free_with_anchor(anchor_title, text)


def _free_with_anchor(anchor_title: str, text: str) -> tuple[str, str]:
    """Default template: keep the user's words as the *modifier* and let the
    router's filters and ambiguity note sort out what they mean. Strips
    scaffolding so "but happier", "happier" and "like this but happier"
    all synthesise identically."""
    tail = re.sub(
        r"^(?:(?:more|very|quite|really|kinda|kind\s+of)\s+)?"
        r"like\s+(?:this|that|it)\s+", "", text, flags=re.IGNORECASE)
    tail = re.sub(r"^(?:but|and)\s+", "", tail, flags=re.IGNORECASE)
    return f"like {anchor_title} but {tail}", "free"


# --------------------------------------------------------------------------
# Dispatch: anchor + text → router response, with the synthesis visible.
# --------------------------------------------------------------------------


def search(
    router: Any,
    anchor_title: str | None,
    text: str,
    *,
    k: int = 10,
) -> dict[str, Any]:
    """Run `text` against the router, anchored on `anchor_title` when given.

    The payload always carries `query` (what was actually searched) and
    `synthesis` (which rule produced it), so the UI can show the bridge
    instead of hiding it. No anchor → plain passthrough: browse is the new
    part, but the query box still answers a bare "90s action movies".
    """
    if not anchor_title:
        query, kind = text.strip(), "none"
    else:
        query, kind = synthesize(anchor_title, text)

    response = router.search(query, k=k)
    return {
        "anchor": anchor_title,
        "query": query,
        "synthesis": kind,
        "response": response.model_dump(mode="json"),
    }
