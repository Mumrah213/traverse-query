"""Browse engine for traverse-query: the traverse half of the app.

Port of `prototypes/traverse/data.py` (query_to_rec repo, 2026-08-12),
kept as close to the original as the rename allows so fixes can flow back.

Reads the shared data files directly -- catalog.jsonl, cast.jsonl,
curated.jsonl -- and builds the indexes that make connection traversal
cheap:

    movie -> connections (people, themes, genres, universe, similar)
    connection -> members, ranked

The connection model is deliberately a *browse* model, not a *recommend*
model. "Tom Hanks" and "space travel" are big connections because a viewer
asks for them directly ("movies with tom hanks"), not because shared cast
predicts "feels like". The curated `comparable_to` edges are the one
similarity-quality signal, and they are surfaced as their own group.

The data dir is the shared substrate the router also reads (catalog +
curated + cast + embeddings). Resolution: TRAVERSE_QUERY_DATA_DIR env,
then <repo>/data (a symlink in dev), then the sibling checkout the
prototype grew up in. See README.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import os

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve_data_dir() -> Path:
    """Where the shared data lives. First hit wins."""
    env = os.environ.get("TRAVERSE_QUERY_DATA_DIR")
    if env:
        return Path(env)
    local = _REPO_ROOT / "data"
    if (local / "catalog.jsonl").exists():
        return local
    # Dev convenience: the simply-tv monorepo the prototype grew up in.
    sibling = _REPO_ROOT.parent / "simply-tv" / "query_to_rec" / "data"
    if (sibling / "catalog.jsonl").exists():
        return sibling
    return local


DATA = _resolve_data_dir()

# Caps: how many chips per group, how many preview titles per chip.
MAX_PEOPLE = 10
MAX_THEMES = 12
PREVIEW = 3
# Fallback "similar" for uncurated movies: shared cast/theme/keyword overlap.
SIMILAR_FALLBACK = 8
OVERLAP_MIN_SCORE = 0.8

# `related_titles` relations that are traversal-worthy (the franchise
# relation -- deliberately excluded from *similarity* in graph.py, and
# exactly right for a browse UI).
_UNIVERSE_RELATIONS = {"same_universe", "adaptation_of", "sequel_to", "prequel_to", "spin_off"}

# `same_universe` was curated without ever being defined, so a large share of
# the raw claims mean "same director/studio/author" instead -- the Sixth
# Sense report. `knowledge_graph/curation/` re-derives the defensible ones
# and writes data/universe-edges.json; we read that allowlist rather than
# the raw claims. The other relations are self-defining and pass through.
_GATED_RELATION = "same_universe"
_UNIVERSE_EDGES = "universe-edges.json"


def _load_universe_edges(data_dir: Path) -> dict[str, list[dict]]:
    """src id (as str) -> audited universe edges cleared for display.

    Fails loud rather than falling back to the raw claims: silently
    reverting to the un-audited data would reintroduce exactly the bug this
    file is here to avoid, and the failure would be invisible in the UI.
    """
    path = data_dir / _UNIVERSE_EDGES
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing -- traverse will not render unaudited "
            f"same_universe claims.\nBuild it with:\n"
            f"  cd knowledge_graph && python -m curation.post_curation "
            f"&& python -m curation.build_universe_edges"
        )
    payload = json.loads(path.read_text())

    # The verdicts were derived from a specific curated.jsonl; if that file
    # has since changed, they may be stale. Warn rather than fail -- stale
    # verdicts are still audited ones, and a live demo should not die on it.
    stamped = payload.get("source_sha256")
    curated = data_dir / "curated.jsonl"
    if stamped and curated.exists():
        actual = hashlib.sha256(curated.read_bytes()).hexdigest()
        if actual != stamped:
            print(
                f"warning: {_UNIVERSE_EDGES} was built from a different "
                f"curated.jsonl ({stamped[:12]}… vs {actual[:12]}…); "
                f"universe edges may be stale. Rebuild with "
                f"curation.post_curation + curation.build_universe_edges."
            )
    return payload.get("edges") or {}

_TAG_STOP = {
    # Generic curated tags that say nothing distinctive about a movie and
    # would drown the theme chips (huge member sets, near-zero signal).
    "tense", "ensemble", "hopeful", "emotionally-heavy", "propulsive",
    "gritty", "dark-comedy", "rewatchable", "feel-good", "sincere",
}


@dataclass
class Movie:
    id: int
    title: str
    year: int
    kind: str
    genres: tuple[str, ...]
    keywords: tuple[str, ...]
    overview: str
    vote_count: int
    vote_average: float
    popularity: float
    curated_tags: tuple[str, ...] = ()
    tag_centrality: dict[str, float] = field(default_factory=dict)
    comparable: tuple[int, ...] = ()
    universe: tuple[tuple[int, str], ...] = ()

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "year": self.year,
            "kind": self.kind,
            "genres": list(self.genres),
            "overview": self.overview,
            "vote_average": self.vote_average,
            "vote_count": self.vote_count,
            "popularity": round(self.popularity, 1),
            "curated": bool(self.curated_tags),
        }


def significance(m: Movie) -> float:
    """Popularity-weighted rating for ranking members within a connection.

    `vote_average * log10(vote_count + 1)` lets a 1983 classic with 800
    votes outrank a 2024 flop with 40, and keeps blockbusters honest by
    the breadth of their audience rather than their recency.
    """
    if m.vote_count <= 0:
        return 0.0
    return round(m.vote_average * math.log10(m.vote_count + 1), 3)


def humanise(label: str) -> str:
    """'space-travel' -> 'Space travel'; 'based on true story' unchanged."""
    return label.replace("-", " ").strip()


@dataclass
class Graph:
    movies: dict[int, Movie]
    cast: dict[int, tuple[str, ...]]
    by_actor: dict[str, list[int]]
    by_tag: dict[str, list[int]]  # curated tags only
    by_keyword: dict[str, list[int]]
    by_genre: dict[str, list[int]]
    titles_by_norm: dict[str, list[int]]
    lookup: Any = None

    # -- lookups ----------------------------------------------------------
    def get(self, mid: int) -> Movie | None:
        return self.movies.get(mid)

    def resolve_title(self, title: str) -> int | None:
        """Title string -> best catalog id (most-voted when ambiguous)."""
        normalise = _normalise()
        ids = self.titles_by_norm.get(normalise(title))
        if not ids:
            return None
        return max(ids, key=lambda i: self.movies[i].vote_count)

    def search(self, query: str, limit: int = 8) -> list[Movie]:
        normalise = _normalise()
        key = normalise(query)
        if not key:
            return []
        hits: dict[int, Movie] = {}

        # 1. exact
        for mid in self.titles_by_norm.get(key, ()):
            hits[mid] = self.movies[mid]
        # 2. contains (whole-title and word-boundary)
        for norm, ids in self.titles_by_norm.items():
            if len(hits) >= 40:
                break
            if key in norm and norm != key:
                for mid in ids:
                    hits.setdefault(mid, self.movies[mid])
        # 3. fuzzy prefix fallback (title startswith each token)
        if len(hits) < limit:
            toks = key.split()
            for norm, ids in self.titles_by_norm.items():
                if len(hits) >= 40:
                    break
                if all(norm.startswith(t) for t in toks[:2]) and norm != key:
                    for mid in ids:
                        hits.setdefault(mid, self.movies[mid])
        ranked = sorted(hits.values(), key=lambda m: (m.vote_count, m.popularity), reverse=True)
        return ranked[:limit]


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load(data_dir: Path | None = None) -> Graph:
    data_dir = data_dir or DATA

    movies: dict[int, Movie] = {}
    for row in _load_jsonl(data_dir / "catalog.jsonl"):
        movies[int(row["id"])] = Movie(
            id=int(row["id"]),
            title=row.get("title", ""),
            year=int(row.get("year") or 0),
            kind=row.get("kind", ""),
            genres=tuple(row.get("genres") or ()),
            keywords=tuple(k.lower() for k in (row.get("keywords") or ())),
            overview=row.get("overview", "") or "",
            vote_count=int(row.get("vote_count") or 0),
            vote_average=float(row.get("vote_average") or 0.0),
            popularity=float(row.get("popularity") or 0.0),
        )

    # curated tags + centrality
    for row in _load_jsonl(data_dir / "curated.jsonl"):
        m = movies.get(int(row["id"]))
        if m is None:
            continue
        m.curated_tags = tuple(row.get("tags") or ())
        m.tag_centrality = {k: float(v) for k, v in (row.get("tag_centrality") or {}).items()}
        m.comparable = tuple()
        m.universe = tuple()

    # comparable_to edges, bidirectional (a curation writes one direction;
    # graph.py already established the reverse index belongs to the reader)
    title_str: dict[str, list[int]] = {}
    for m in movies.values():
        title_str.setdefault(m.title.lower(), []).append(m.id)
    universe_edges = _load_universe_edges(data_dir)
    universe_allowlist = {
        (int(src), str(e["title"]).lower())
        for src, entries in universe_edges.items() for e in entries
    }
    for row in _load_jsonl(data_dir / "curated.jsonl"):
        m = movies.get(int(row["id"]))
        if m is None:
            continue
        for name in row.get("comparable_to") or ():
            ids = title_str.get(str(name).lower())
            if not ids:
                continue
            target = max(ids, key=lambda i: movies[i].vote_count)
            m.comparable = tuple(dict.fromkeys((*m.comparable, target)))
            movies[target].comparable = tuple(dict.fromkeys((*movies[target].comparable, m.id)))
        for rel in row.get("related_titles") or ():
            if rel.get("relation") not in _UNIVERSE_RELATIONS:
                continue
            claimed = str(rel.get("title", ""))
            if (rel.get("relation") == _GATED_RELATION
                    and (m.id, claimed.lower()) not in universe_allowlist):
                continue
            ids = title_str.get(claimed.lower())
            if not ids:
                continue
            target = max(ids, key=lambda i: movies[i].vote_count)
            m.universe = tuple(dict.fromkeys((*m.universe, (target, rel["relation"]))))

    # Franchise rosters generate edges no curation ever claimed -- if the
    # roster says these are one continuity, that holds for every pair, not
    # just the pairs a model happened to mention. These carry an explicit
    # destination id, so they also sidestep the vote_count title resolution
    # above (which is what once resolved DCEU's "The Flash" to the 2014 TV
    # series).
    for src, entries in universe_edges.items():
        m = movies.get(int(src))
        if m is None:
            continue
        for e in entries:
            if e.get("source") != "franchise-roster":
                continue
            target = e.get("dst")
            if target is None or target not in movies or target == m.id:
                continue
            # A claimed edge already naming this title keeps its own, more
            # specific relation (sequel_to, prequel_to); the roster only
            # fills in what the curation never mentioned.
            if any(t == target for t, _ in m.universe):
                continue
            m.universe = (*m.universe, (target, _GATED_RELATION))

    # cast
    cast: dict[int, tuple[str, ...]] = {}
    for row in _load_jsonl(data_dir / "cast.jsonl"):
        cast[int(row["id"])] = tuple(row.get("cast") or ())

    # reverse indexes
    by_actor: dict[str, list[int]] = defaultdict(list)
    for mid, names in cast.items():
        for name in names:
            by_actor[name].append(mid)
    by_tag: dict[str, list[int]] = defaultdict(list)
    by_keyword: dict[str, list[int]] = defaultdict(list)
    by_genre: dict[str, list[int]] = defaultdict(list)
    for m in movies.values():
        for tag in m.curated_tags:
            by_tag[tag].append(m.id)
        for kw in m.keywords:
            by_keyword[kw].append(m.id)
        for gen in m.genres:
            by_genre[gen].append(m.id)

    titles_by_norm: dict[str, list[int]] = defaultdict(list)
    normalise = _normalise()

    for m in movies.values():
        titles_by_norm[normalise(m.title)].append(m.id)

    g = Graph(
        movies=movies,
        cast=cast,
        by_actor=dict(by_actor),
        by_tag=dict(by_tag),
        by_keyword=dict(by_keyword),
        by_genre=dict(by_genre),
        titles_by_norm=dict(titles_by_norm),
    )
    try:
        g.lookup = _build_lookup()
    except Exception:
        g.lookup = None
    return g


def _build_lookup() -> Any:
    """The project's title lookup, wired to this catalog (scar tissue, lazy).

    Mirrors prototypes/spread/data.py: imports inside the function so a
    missing install degrades to the plain-data search above.
    """
    from query_to_rec.catalog import Title
    from query_to_rec.router.lookup import CatalogTitleLookup, TitleIndex

    titles = [
        Title(
            id=m.id,
            title=m.title,
            year=m.year,
            kind=m.kind,
            genres=list(m.genres),
            overview=m.overview,
            keywords=list(m.keywords),
            vote_count=m.vote_count,
            vote_average=m.vote_average,
            popularity=m.popularity,
        )
        for m in _MOVIES_GLOBAL
    ]
    index = TitleIndex(titles, [t.title for t in titles], [])
    return CatalogTitleLookup(index)


_MOVIES_GLOBAL: list[Movie] = []


def _normalise() -> Any:
    """The project's title normaliser (drops articles, strips punctuation)."""
    from query_to_rec.text import normalise

    return normalise


def _ranked(members: list[int], graph: Graph) -> list[Movie]:
    """Members ranked by significance, ties by popularity."""
    return sorted(
        (graph.movies[i] for i in members if i in graph.movies),
        key=lambda m: (significance(m), m.popularity),
        reverse=True,
    )


def _preview(members: list[int], graph: Graph) -> list[dict[str, Any]]:
    out = []
    for m in _ranked(members, graph)[:PREVIEW]:
        out.append({"id": m.id, "title": m.title, "year": m.year})
    return out


# ---------------------------------------------------------------------------
# the node: connections for a movie
# ---------------------------------------------------------------------------

def node(graph: Graph, mid: int) -> dict[str, Any] | None:
    m = graph.movies.get(mid)
    if m is None:
        return None

    people: list[dict[str, Any]] = [
        {
            "name": name,
            "count": len(graph.by_actor.get(name, [])),
            "preview": _preview(graph.by_actor.get(name, []), graph),
        }
        for name in graph.cast.get(mid, ())
        if graph.by_actor.get(name)
    ]
    people.sort(key=lambda p: p["count"], reverse=True)
    people = people[:MAX_PEOPLE]

    themes: list[dict[str, Any]] = []
    if m.curated_tags:
        for tag in m.curated_tags:
            if tag in _TAG_STOP:
                continue
            members = graph.by_tag.get(tag, ())
            if not members:
                continue
            themes.append(
                {
                    "label": humanise(tag),
                    "count": len(members),
                    "centrality": m.tag_centrality.get(tag, 0.0),
                    "preview": _preview(members, graph),
                }
            )
        # Defining themes first (space-travel: 1.0), then big ones.
        themes.sort(key=lambda t: (t["centrality"], t["count"]), reverse=True)
        theme_source = "curated"
    else:
        # Uncurated titles: keyword themes as the honest fallback.
        for kw in m.keywords:
            members = graph.by_keyword.get(kw, ())
            if len(members) < 2:
                continue
            themes.append({"label": humanise(kw), "count": len(members), "centrality": 0.0,
                           "preview": _preview(members, graph)})
        themes.sort(key=lambda t: t["count"], reverse=True)
        theme_source = "keywords"

    genres: list[dict[str, Any]] = [
        {"name": gen, "count": len(graph.by_genre.get(gen, []))}
        for gen in m.genres
        if graph.by_genre.get(gen)
    ]
    genres.sort(key=lambda g: g["count"], reverse=True)

    universe = [
        {"id": tmid, "title": graph.movies[tmid].title, "year": graph.movies[tmid].year,
         "relation": rel.replace("_", " ")}
        for tmid, rel in m.universe
        if tmid in graph.movies
    ]

    if m.comparable:
        similar = [
            {"id": tmid, "title": graph.movies[tmid].title, "year": graph.movies[tmid].year,
             "why": "curated comparable", "score": significance(graph.movies[tmid])}
            for tmid in m.comparable
            if tmid in graph.movies
        ]
        similar_note = "curated comparable edges (human-judged)"
    else:
        similar, similar_note = _overlap_similar(graph, m)

    return {
        "movie": m.to_public(),
        "people": people,
        "themes": themes,
        "theme_source": theme_source,
        "genres": genres,
        "universe": universe,
        "similar": similar,
        "similar_note": similar_note,
    }


def _overlap_similar(graph: Graph, m: Movie) -> tuple[list[dict[str, Any]], str]:
    """Fallback 'similar' for uncurated movies: shared cast + themes +
    keywords, weighted so rare actors are the strong signal (Tom Hanks
    appears in 71 films; a 3-film character actor shared by two titles
    means something).
    """
    scores: defaultdict[int, float] = defaultdict(float)
    why: defaultdict[int, list[str]] = defaultdict(list)

    def add(ids: list[int], w: float, label: str) -> None:
        for oid in ids:
            if oid == m.id or oid not in graph.movies:
                continue
            scores[oid] += w
            why[oid].append(label)

    for name in graph.cast.get(m.id, ()):
        n = len(graph.by_actor.get(name, []))
        add(graph.by_actor.get(name, []), 2.0 / math.log10(n + 2), name)
    for tag in m.curated_tags:
        n = len(graph.by_tag.get(tag, []))
        add(graph.by_tag.get(tag, []), 1.5 / math.log10(n + 2), humanise(tag))
    for kw in m.keywords:
        n = len(graph.by_keyword.get(kw, []))
        add(graph.by_keyword.get(kw, []), 1.0 / math.log10(n + 2), humanise(kw))

    out: list[dict[str, Any]] = []
    for oid, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        if score < OVERLAP_MIN_SCORE:
            continue
        om = graph.movies[oid]
        labels = why[oid]
        out.append({
            "id": oid,
            "title": om.title,
            "year": om.year,
            "why": "shares " + ", ".join(labels[:3]),
            "score": significance(om),
            "overlap": round(score, 2),
        })
        if len(out) >= SIMILAR_FALLBACK:
            break
    out.sort(key=lambda s: s["overlap"], reverse=True)
    return out, "shared cast & themes (fallback for uncurated titles)"


# ---------------------------------------------------------------------------
# a connection: its members
# ---------------------------------------------------------------------------

def _theme_members(graph: Graph, label: str) -> tuple[list[int], str]:
    """Resolve a humanised theme label ('space travel') to its members.

    Curated tags are indexed kebab-case ('space-travel'), keywords are not;
    a label can name either, so try both spellings against both indexes and
    say which one matched.
    """
    variants = [label, label.replace(" ", "-")]
    for v in variants:
        if v in graph.by_tag:
            return graph.by_tag[v], "curated"
    for v in variants:
        if v in graph.by_keyword:
            return graph.by_keyword[v], "keywords"
    return [], "none"


def connection_members(graph: Graph, kind: str, value: str, limit: int = 40) -> dict[str, Any]:
    """Members of one connection, ranked by significance.

    kind: cast | theme | genre
    """
    if kind == "cast":
        members = graph.by_actor.get(value, [])
        why = value
    elif kind == "theme":
        members, _src = _theme_members(graph, value)
        why = humanise(value)
    elif kind == "genre":
        members = graph.by_genre.get(value, [])
        why = value
    else:
        return {"kind": kind, "value": value, "count": 0, "members": []}

    ranked = _ranked(members, graph)[:limit]
    return {
        "kind": kind,
        "value": value,
        "count": len(members),
        "members": [
            {
                "id": mm.id,
                "title": mm.title,
                "year": mm.year,
                "kind": mm.kind,
                "vote_average": mm.vote_average,
                "vote_count": mm.vote_count,
                "why": why,
                "score": significance(mm),
                "curated": bool(mm.curated_tags),
            }
            for mm in ranked
        ],
    }
