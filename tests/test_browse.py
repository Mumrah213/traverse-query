"""Pin tests for the browse half (ported from prototypes/traverse).

These pin the *browse* model on the real catalog: the Apollo 13 demo the
idea started from, the reverse-index discipline on comparable edges, the
uncurated fallback, and the universe audit (The Sixth Sense report).

The browse engine builds reverse indexes (cast/theme/keyword/genre), so it
loads once per module and every test runs pure functions over that graph.
Tests skip when the shared data dir is absent (fresh clone before `make
data-link`), so the suite stays green in CI without the 200 MB sibling.
"""

from __future__ import annotations

from typing import Any

import pytest

from traverse_query.browse import Graph, connection_members, load, node

_HAS_DATA = __import__("pathlib").Path("data/catalog.jsonl").exists() or __import__(
    "traverse_query.browse", fromlist=["DATA"]).DATA.joinpath("catalog.jsonl").exists()

pytestmark = [
    pytest.mark.skipif(not _HAS_DATA, reason="shared data dir absent (make data-link)"),
    pytest.mark.usefixtures("_traverse_graph"),
]


@pytest.fixture(scope="module")
def _traverse_graph() -> Graph:
    return load()


def _node(g: Graph, title: str) -> dict[str, Any]:
    hits = [m for m in g.movies.values() if m.title == title]
    assert hits, f"no title {title!r}"
    m = max(hits, key=lambda x: x.vote_count)
    d = node(g, m.id)
    assert d is not None
    return d


class TestApollo13Node:
    """The demo: Apollo 13 -> Tom Hanks / Space-Survival -> repeat."""

    def test_people_are_big_connections_sorted_by_count(self, _traverse_graph: Graph) -> None:
        d = _node(_traverse_graph, "Apollo 13")
        names = [p["name"] for p in d["people"]]
        assert names[0] == "Tom Hanks"
        assert d["people"][0]["count"] == 71
        assert "Kevin Bacon" in names and "Ed Harris" in names
        counts = [p["count"] for p in d["people"]]
        assert counts == sorted(counts, reverse=True), "people chips must be count-sorted"

    def test_defining_themes_first(self, _traverse_graph: Graph) -> None:
        d = _node(_traverse_graph, "Apollo 13")
        labels = [t["label"] for t in d["themes"]]
        assert labels[0] == "based on true story"
        assert "space travel" in labels and labels.index("space travel") < labels.index("survival")
        st = next(t for t in d["themes"] if t["label"] == "space travel")
        assert st["centrality"] == 1.0 and st["count"] >= 100

    def test_generic_tags_never_become_chips(self, _traverse_graph: Graph) -> None:
        d = _node(_traverse_graph, "Apollo 13")
        labels = {t["label"] for t in d["themes"]}
        assert "tense" not in labels and "ensemble" not in labels

    def test_curated_comparable_similar(self, _traverse_graph: Graph) -> None:
        d = _node(_traverse_graph, "Apollo 13")
        similar = {s["title"] for s in d["similar"]}
        for want in ("The Right Stuff", "First Man", "Gravity", "The Martian"):
            assert want in similar, f"{want} missing from comparable similar"
        assert "curated" in d["similar_note"]


class TestUniverseAudit:
    """The Sixth Sense report: same_universe claims were curated without the
    relation ever being defined, so the model supplied its own semantics.
    The audited edges (data/universe-edges.json) are what reach the UI."""

    def test_same_director_is_not_a_universe(self, _traverse_graph: Graph) -> None:
        d = _node(_traverse_graph, "The Sixth Sense")
        titles = {u["title"] for u in d["universe"]}
        for wrong in ("Signs", "Unbreakable", "Split", "Glass"):
            assert wrong not in titles, f"{wrong} still claimed as universe"

    def test_real_universes_survive(self, _traverse_graph: Graph) -> None:
        d = _node(_traverse_graph, "Avengers: Infinity War")
        titles = {u["title"] for u in d["universe"]}
        assert "Captain America: Civil War" in titles

    def test_rosters_do_not_merge_separate_series(self, _traverse_graph: Graph) -> None:
        d = _node(_traverse_graph, "Alien")
        titles = {u["title"] for u in d["universe"]}
        assert "Aliens" in titles
        assert not {"Predator", "Predators", "Prey"} & titles


class TestUncuratedFallback:
    def test_keyword_themes_and_overlap_similar(self, _traverse_graph: Graph) -> None:
        m = next(x for x in _traverse_graph.movies.values()
                 if not x.curated_tags and len(x.keywords) >= 3 and
                 x.id in _traverse_graph.cast and len(_traverse_graph.cast[x.id]) >= 5)
        d = node(_traverse_graph, m.id)
        assert d is not None
        assert d["theme_source"] == "keywords"
        assert d["themes"], "uncurated title with keywords should get theme chips"
        assert "fallback" in d["similar_note"]
        assert d["similar"], "uncurated title should get overlap similar"


class TestConnectionMembers:
    def test_cast_members_ranked_by_significance(self, _traverse_graph: Graph) -> None:
        cm = connection_members(_traverse_graph, "cast", "Tom Hanks")
        assert cm["count"] == 71
        titles = [m["title"] for m in cm["members"]]
        assert titles[0] == "Forrest Gump"
        assert "Cast Away" in titles
        scores = [m["score"] for m in cm["members"]]
        assert scores == sorted(scores, reverse=True), "members must be significance-sorted"

    def test_theme_label_and_raw_resolve_to_same_members(self, _traverse_graph: Graph) -> None:
        a = connection_members(_traverse_graph, "theme", "space travel")
        b = connection_members(_traverse_graph, "theme", "space-travel")
        assert a["count"] == b["count"] >= 100
        assert [m["id"] for m in a["members"]] == [m["id"] for m in b["members"]]

    def test_unknown_theme_is_empty_not_an_error(self, _traverse_graph: Graph) -> None:
        cm = connection_members(_traverse_graph, "theme", "definitely not a theme")
        assert cm["count"] == 0 and cm["members"] == []


class TestSignificance:
    def test_classic_outranks_flop(self, _traverse_graph: Graph) -> None:
        from traverse_query.browse import Movie, significance

        classic = Movie(id=1, title="x", year=1983, kind="movie", genres=(),
                        keywords=(), overview="", vote_count=800,
                        vote_average=7.6, popularity=5.0)
        flop = Movie(id=2, title="y", year=2024, kind="movie", genres=(),
                     keywords=(), overview="", vote_count=42,
                     vote_average=6.0, popularity=3.0)
        assert significance(classic) > significance(flop)


class TestSearch:
    def test_exact_title(self, _traverse_graph: Graph) -> None:
        hits = [m.title for m in _traverse_graph.search("apollo 13")]
        assert hits[0] == "Apollo 13"

    def test_article_handling(self, _traverse_graph: Graph) -> None:
        assert _traverse_graph.search("the wire")[0].title == "The Wire"
        assert _traverse_graph.search("wire")[0].title == "The Wire"
