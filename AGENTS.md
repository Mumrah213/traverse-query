# AGENTS.md — traverse-query

A thin repo on purpose: it combines two existing things and adds one new
seam. Know which is which before changing anything.

## The three parts and their owners

| Part | Lives where | Fixes flow to |
|---|---|---|
| **Browse engine** | `traverse_query/browse.py` (port of `query_to_rec/prototypes/traverse/data.py`) | `query_to_rec` — keep the port close to the original so fixes can flow back; a change here should generally be made there first |
| **Router** | the `query_to_rec` package, used unchanged | `query_to_rec` — never modify router internals from this repo; `anchor.py` only builds query strings |
| **Anchor bridge** | `traverse_query/anchor.py` — **this repo's core** | this repo |

## Conventions

- **The synthesis contract is tested, not hoped.** `tests/test_anchor.py`
  pins `synthesize()`: every rule, the passthrough safety valve, the
  no-anchor passthrough. A new template is a new pin first.
- **Every synthesis is visible.** The UI shows `query` (what was
  searched) and `synthesis` (which rule fired). Never hide the bridge.
- **Degrade, don't die.** The server boots browse-only if the router or
  embeddings are missing, and `/api/query` answers an honest 503; a
  request exception must become a JSON 500, never a dead connection.
- **Data problems are not this repo's problems.** `same_universe`
  overclaims, theme-key drift, curation gaps → `knowledge_graph` /
  `query_to_rec`. This repo renders what the data says; the browse half
  deliberately does not patch around it.
- **Stdlib server.** No FastAPI here; the UI is a single HTML file, no
  build step.

## Data

`data/` is a dev symlink to the sibling `query_to_rec/data` (gitignored);
`TRAVERSE_QUERY_DATA_DIR` overrides it. Tests skip when data is absent.

## Open problem (see PROBLEM.md §5)

Relative attributes ("happier than THIS") — the router inverts absolute
axes and filters on explicit phrases but has no delta-from-anchor
primitive. That is the next build, and it belongs in this repo or as a
router primitive the sibling owns — decide there first.
