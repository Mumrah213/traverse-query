"""Pin tests for the anchor bridge: the traverse→query handoff.

`synthesize()` is the contract — pure text mapping from "what the user
typed against the anchor" to "what the router actually searches", with the
rule that fired attached so a regression says *which* reading changed.

The dispatch pins (against the real router + embeddings) run only when the
shared data dir is present, like the browse pins.
"""

from __future__ import annotations

from typing import Any

import pytest

from traverse_query.anchor import search, synthesize

_HAS_DATA = __import__("pathlib").Path("data/catalog.jsonl").exists() or __import__(
    "traverse_query.browse", fromlist=["DATA"]).DATA.joinpath("catalog.jsonl").exists()

ANCHOR = "Apollo 13"


class TestSynthesis:
    def test_empty_is_more_like(self) -> None:
        assert synthesize(ANCHOR, "") == (f"like {ANCHOR}", "more_like")
        assert synthesize(ANCHOR, "   ") == (f"like {ANCHOR}", "more_like")

    def test_passthrough_when_user_names_the_anchor(self) -> None:
        """'opposite of apollo 13' is already a complete router query; we
        must not double-inject the anchor into it."""
        assert synthesize(ANCHOR, "opposite of apollo 13") == ("opposite of apollo 13", "passthrough")
        assert synthesize(ANCHOR, "the absolute opposite of Apollo 13") == (
            "the absolute opposite of Apollo 13", "passthrough")

    def test_opposite_family(self) -> None:
        for text in ("the opposite", "opposite", "the exact opposite",
                     "opposite of this", "inverse", "reverse", "flip it",
                     "nothing like this", "not like this"):
            q, kind = synthesize(ANCHOR, text)
            assert kind == "opposite", f"{text!r} should route opposite, got {kind}"
            assert q == f"the absolute opposite of {ANCHOR}", f"{text!r} -> {q!r}"

    def test_relation_family(self) -> None:
        q, kind = synthesize(ANCHOR, "sequel")
        assert kind == "relation" and q == f"the sequel to {ANCHOR}"
        q, kind = synthesize(ANCHOR, "prequel to this")
        assert kind == "relation" and q == f"the prequel to {ANCHOR}"
        q, kind = synthesize(ANCHOR, "spin-off")
        assert kind == "relation" and q == f"the spin-off to {ANCHOR}"

    def test_more_like_family(self) -> None:
        for text in ("more like this", "like this", "similar", "same vibe",
                     "another one", "recommend something"):
            q, kind = synthesize(ANCHOR, text)
            assert kind == "more_like", f"{text!r} should route more_like, got {kind}"
            assert q == f"like {ANCHOR}"

    def test_free_text_becomes_modifier(self) -> None:
        q, kind = synthesize(ANCHOR, "happier")
        assert kind == "free" and q == f"like {ANCHOR} but happier"
        q, kind = synthesize(ANCHOR, "but from the 90s")
        assert q == f"like {ANCHOR} but from the 90s", "leading 'but' must not double"
        q, kind = synthesize(ANCHOR, "like this but funnier")
        assert q == f"like {ANCHOR} but funnier", "'like this' scaffolding must be stripped"
        q, kind = synthesize(ANCHOR, "90s action")
        assert q == f"like {ANCHOR} but 90s action"

    def test_title_without_anchor_is_passthrough(self) -> None:
        # a bare "opposite" with no anchor is handed to the router verbatim
        assert synthesize("", "opposite") == ("opposite", "passthrough")


class TestSearchDispatch:
    def test_no_anchor_is_plain_passthrough(self) -> None:
        resp = search(FakeRouter(), None, "90s action movies")
        assert resp["anchor"] is None
        assert resp["query"] == "90s action movies"
        assert resp["synthesis"] == "none"

    def test_synthesis_is_visible_in_payload(self) -> None:
        resp = search(FakeRouter(), ANCHOR, "the opposite")
        assert resp["anchor"] == ANCHOR
        assert resp["query"] == f"the absolute opposite of {ANCHOR}"
        assert resp["synthesis"] == "opposite"


class FakeRouter:
    """Records the query it was given; returns a minimal SearchResponse."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, query: str, *, k: int = 10) -> Any:
        self.calls.append(query)
        from query_to_rec.models import SearchResponse

        return SearchResponse(query=query, groups=[])


def _real_router() -> Any:
    from traverse_query.server import _build_router

    from traverse_query.browse import DATA

    return _build_router(DATA)


@pytest.mark.skipif(not _HAS_DATA, reason="shared data dir absent")
class TestDispatchAgainstRouter:
    """End-to-end through the real router + embeddings. Slow (matrix load
    once per module); these are the proof the bridge actually answers."""

    @pytest.fixture(scope="module")
    def router(self) -> Any:
        return _real_router()

    def test_opposite_uses_inversion_branch(self, router: Any) -> None:
        from traverse_query.browse import load as browse_load

        g = browse_load()
        a13 = next(m for m in g.movies.values() if m.title == "Apollo 13")
        d = search(router, a13.title, "the opposite")
        resp = d["response"]
        assert resp["groups"], "opposite of Apollo 13 should produce groups"
        mechs = {g["mechanism"] for g in resp["groups"]}
        assert "inversion" in mechs, f"expected inversion, got {mechs}"
        assert resp["routed_as"] == "inversion"

    def test_more_like_produces_results(self, router: Any) -> None:
        d = search(router, "Apollo 13", "more like this")
        assert d["response"]["groups"], "more like Apollo 13 should produce groups"
