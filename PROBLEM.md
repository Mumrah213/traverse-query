# Traverse-Query — problem statement & analysis

Status: prototype built and verified end-to-end · 2026-08-14

## 1. The problem statement

> Combine the user-facing parts of query_to_rec with the traverse style.
> Like traverse → find a movie I like → I can now do a "special query"
> with that movie.

Traverse answers *browse* ("movies with tom hanks", "space survival").
The router answers *queries* ("the absolute opposite of die hard", "90s
action movies"). This repo is the missing seam: a concrete movie found by
browsing becomes the **anchor** for a query that is *relative to it*.

The loop:

```
browse:   Apollo 13 ──▶ Tom Hanks (71) ──▶ Cast Away ──▶ Survival (691) ──▶ Gravity
anchor:   ⛓ this movie (Apollo 13)
query:    "the opposite"        → "the absolute opposite of Apollo 13"   → INVERSION
          "more like this"      → "like Apollo 13"                       → SET_OPS
          "but from the 90s"    → "like Apollo 13 but from the 90s"      → SET_OPS + filter
          "sequel"              → "the sequel to Apollo 13"              → franchise machinery
click:    a result is a new node → keep browsing
```

## 2. What was built (2026-08-14)

- **Browse half** — port of `query_to_rec/prototypes/traverse/data.py`
  into `traverse_query/browse.py`, unchanged behaviour, all original pins
  re-pinned (Apollo 13 demo, universe audit, uncurated fallback,
  significance ranking, article handling).
- **Anchor half** — `traverse_query/anchor.py`: `synthesize()` maps
  "what the user typed against the anchor" to "what the router searches",
  with the rule that fired attached (`opposite` / `relation` / `more_like`
  / `free` / `passthrough` / `none`). Every synthesis is shown in the UI.
- **Server + UI** — stdlib server (browse API + `POST /api/query`), one
  page: browse → anchor button on any node → sticky anchor bar with quick
  chips → results panel; clicking a result jumps back into browse.
- **Verified end-to-end** (real catalog + router + embeddings):
  - `the opposite` on Apollo 13 → `inversion`, "Opposite on every axis:
    Only Lovers Left Alive, …"
  - `more like this` on Apollo 13 → `set_ops`: First Man, The Right
    Stuff, The Martian
  - `but from the 90s` on Apollo 13 → the decade filter survives the
    anchor: October Sky, Armageddon, Lost in Space
  - `sequel` on Die Hard → `exact_title`: "A Good Day to Die Hard" (the
    franchise roster resolved it)
  - 25 tests pass; the dispatch pins run the real router.

## 3. The synthesis contract (what the rules are, and why)

| user typed | synthesis | router sees |
|---|---|---|
| *nothing* | `more_like` | `like {anchor}` |
| "opposite / inverse / flip / nothing like this" | `opposite` | `the absolute opposite of {anchor}` |
| "sequel / prequel / spin-off" | `relation` | `the {relation} to {anchor}` |
| "more like this / similar / same vibe" | `more_like` | `like {anchor}` |
| anything else ("happier", "but 90s") | `free` | `like {anchor} but {text}` |
| query already names the anchor | `passthrough` | verbatim |
| no anchor | `none` | verbatim (plain query_to_rec) |

Judgement calls worth knowing:

- **The anchor is text, not plumbing.** The router's plan machinery is
  keyed on text spans; injecting the anchor's title means every branch
  (matchers, filters, franchise rosters, the escalation gate) works
  against it with zero router changes. The cost — "happier" can be
  swallowed by the similarity reading — is exactly the open problem
  below.
- **Filters survive the default template.** "like Apollo 13 but from the
  90s" keeps the decade filter because the router's plan shape treats
  filters as non-competing claims. Measured, not assumed.
- **Passthrough is a safety valve.** If the user types the anchor's title
  themselves, we never double-inject ("opposite of apollo 13" stays
  verbatim).
- **Honest fallbacks.** "the sequel to Apollo 13" resolves to nothing in
  the franchise roster and the router says so via its semantic fallback
  (Apollo 13, Apollo 18, Apollo 11) — a *named* sequel-less film is a
  different case from Die Hard, and the router already handled both.

## 4. What this repo does NOT own

- **Data quality.** `same_universe` overclaiming (The Sixth Sense report),
  theme-key resolution, the universe audit — all live in the sibling
  repos (`knowledge_graph/curation`, `query_to_rec/prototypes/traverse`).
  The browse half renders what the data says; fixing the data is not this
  repo's job. See `AGENTS.md`.
- **The router.** `anchor.py` never touches router internals; it only
  builds query strings. Router behaviour (ambiguity notes, branch choice)
  is the sibling's contract.

## 5. Open questions worth answering before this becomes more than a prototype

1. **Relative attributes — the real "special query".** "happier" on
   Apollo 13 currently synthesises to "like Apollo 13 but happier" and the
   router's similarity reading wins — the modifier is *heard* but not
   *applied*. The feature this repo exists for is a delta-from-anchor
   primitive: shift the anchor's attribute vector along an axis ("happier
   than THIS"), or filter on the anchor's own attribute profile ("like
   this but darker"). That needs a router-side primitive or a
   this-repo-side attribute lookup — either way it is the next build.
2. **Should anchored queries exclude the anchor itself?** "like Apollo
   13" returns Apollo 13 first (its own embedding is nearest). For browse
   that is defensible; for a *recommend* reading it is noise. The router's
   exclusion machinery exists ("like X but not X") — worth a setting.
3. **The embed backend at query time.** Semantic fallback needs
   sentence-transformers + torch at runtime (not at build time, where the
   matrix is precomputed). The sibling loads both lazily, so the *first
   user query* used to pay ~20s of torch import + model load, plus ~5s
   for the tag-vocabulary matrix `tags.py` builds on first search. This
   repo absorbs both at boot: `_warm_up()` runs the public `embed()` and
   one warm `router.search()` at startup, with a log line per step (~35s
   boot, every query fast afterwards). The heavier question -- should the
   runtime model be a service instead of a per-process import -- belongs
   to the sibling's deployment, not this repo.
4. **Where results belong in the UI.** Currently results render below the
   node and clicking a title jumps back into browse. Reasonable, but the
   anchor bar persisting across hops (by design) wants a clearer visual
   story.
5. **Data coupling.** The repo runs against the sibling's live `data/`
   via symlink. The `query-to-rec@git` dependency + `TRAVERSE_QUERY_DATA_DIR`
   make a clean clone work, but the data itself is ~200 MB and growing —
   a snapshot/export story belongs to the data owner, not this repo.
