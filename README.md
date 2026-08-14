# traverse-query

Browse the movie graph to find a movie you like — then run a **special
query with that movie as context**.

```
Apollo 13 ──browse──▶ ⛓ anchor it ──▶ "the opposite"  ──▶ router: inversion of Apollo 13
                                     ├─▶ "more like this" ──▶ set-ops: First Man, The Right Stuff…
                                     ├─▶ "but from the 90s" ──▶ filters survive the anchor
                                     └─▶ "sequel" ──▶ franchise roster (Die Hard → A Good Day…)
```

Two halves, one page:

- **Browse** (ported from `query_to_rec/prototypes/traverse`): start at a
  title, see its big connections — People, Themes, Genres, Universe &
  franchise, Similar — step into one, pick another title, repeat. This is
  a *browse* model, not a recommend model: "movies with tom hanks" is a
  connection, not a query.
- **Anchor + query** (this repo's core): once a movie is anchored, a text
  box turns the router loose *relative to that movie*. The bridge is a
  small, visible set of synthesis templates (`traverse_query/anchor.py`);
  every synthesis is shown in the UI, so nothing happens behind the user's
  back.

The query engine is the `query_to_rec` router, reused unchanged. Its
branches — similarity, inversion, popularity, franchise, filters — are
keyed on text spans, so anchoring a concrete movie into them is just a
template ("the absolute opposite of Apollo 13") and every existing
capability works for free.

## Run

```sh
make install     # venv + deps (dev layout: editable sibling query_to_rec)
make serve       # http://127.0.0.1:8500
```

Or by hand:

```sh
uv venv
uv pip install -e /path/to/query_to_rec    # dev: live sibling checkout
uv pip install -e . --no-deps
uv pip install sentence-transformers pytest   # embed backend + tests
.venv/bin/python -m traverse_query          # http://127.0.0.1:8500
```

Standalone (clean clone, no sibling): `uv sync` pulls
`query-to-rec @ git+https://github.com/Mumrah213/query-to-rec@main` from
pyproject.toml, then point at a data dir:

```sh
TRAVERSE_QUERY_DATA_DIR=/path/to/data .venv/bin/python -m traverse_query
```

### Data

The shared substrate: `catalog.jsonl`, `curated.jsonl`, `cast.jsonl` (the
browse half) plus `embeddings*.npy` (the query half). The canonical live
copy lives in the sibling `query_to_rec/data` — the dev layout symlinks
it:

```sh
make data-link    # ln -s <sibling>/data data  (TRAVERSE_QUERY_DATA_DIR overrides)
```

## Tests

```sh
make test         # pytest: browse pins (ported) + anchor synthesis/dispatch pins
```

Browse pins skip when `data/` is absent, so the suite stays green in CI.
The dispatch pins run the real router + embeddings when data is present —
they are the proof the bridge actually answers.

## API

```
GET  /                          the UI
GET  /api/search?q=             title search (for the entry box)
GET  /api/movie?id=             a node: movie + its connections
GET  /api/connection?kind=&value=  members of a chip
POST /api/query                 {query, anchor_id?, k?} → anchored search
GET  /api/health                status: browse + router readiness
```

`POST /api/query` returns the router's response plus `query` (what was
actually searched) and `synthesis` (which rule produced it) — the UI
shows both.

## Files

- `traverse_query/browse.py` — browse engine (port of `prototypes/traverse/data.py`)
- `traverse_query/anchor.py` — **the core**: synthesis templates + dispatch
- `traverse_query/server.py` — stdlib HTTP server (browse API + `/api/query`)
- `traverse_query/static/index.html` — single-page UI, no build step
- `tests/` — browse pins (ported) + anchor pins
- `PROBLEM.md` — the concept, the measured behaviour, open questions
