"""traverse-query: browse the movie graph to find an anchor, then query it.

    browse (traverse) → anchor a movie you like → special query with it
    as context ("the opposite", "like this but 90s", "sequel").

The two halves: `browse` (the traverse-style graph walk) and `anchor`
(the bridge from a concrete movie to the query_to_rec router).
"""

from .browse import Graph, load, node, connection_members
from . import anchor

__all__ = ["Graph", "load", "node", "connection_members", "anchor"]
