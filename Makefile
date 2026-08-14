VENV = .venv
PY = $(VENV)/bin/python

# Dev layout: the sibling checkout is the live router (editable install),
# its data dir is the shared substrate (symlinked).
SIBLING ?= ../simply-tv/query_to_rec

.PHONY: install data-link serve test

install: ## venv + deps (editable sibling query_to_rec + this package)
	uv venv
	uv pip install -e $(SIBLING)
	uv pip install -e . --no-deps
	uv pip install sentence-transformers pytest

data-link: ## symlink the shared data dir from the sibling checkout
	ln -sfn $(abspath $(SIBLING))/data data

serve: ## run the server on http://127.0.0.1:8500
	$(PY) -m traverse_query

test: ## run the suite (browse pins + anchor pins)
	$(PY) -m pytest -q
