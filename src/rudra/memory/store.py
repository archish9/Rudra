"""The only module in Rudra that imports mempalace (C8.1).

One import site, for the reason rudra/llm/ has one: a vendor API change
is then one file's problem. tests/test_no_direct_provider_imports.py
enforces both the boundary and the laziness.

Every mempalace call passes palace_path, collection_name and backend as
explicit arguments. This is not defensive style, it is required (C8.1b):

  collection_name  reads config.json ONLY -- no env override exists
                   (mempalace config.py:413-415)
  backend          reads config.json BEFORE env, so MEMPALACE_BACKEND
                   loses to a user's global setting (config.py:425-432)
  embedding_model  reads env first, so the env var is used for that one
                   (config.py:755)

A user with ~/.mempalace/config.json would otherwise silently redirect
every Rudra read and write to a different collection on a different
backend. tests/test_memory_isolation.py is that scenario, executable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rudra.memory.degrade import MemoryUnavailable, degrades
from rudra.memory.entry import MemoryEntry
from rudra.memory.taxonomy import wing_for
from rudra.state.paths import rudra_paths

# Rudra's own collection. Named rather than inherited, because
# get_configured_collection_name() reads the user's global config.json and
# has no env escape hatch.
RUDRA_COLLECTION = "rudra_memory"

# mempalace's DEFAULT_CHUNK_SIZE (config.py:274). Mirrored rather than
# imported so this constant is readable without importing mempalace at
# module scope; test_memory_store.py checks the two agree.
CHUNK_CHARS = 800

# minilm is chromadb's own bundled ONNX MiniLM, cached at
# ~/.cache/chroma/onnx_models. Set explicitly rather than left to
# resolution: env wins over config.json for this key, so this is the one
# setting an environment variable can actually pin.
EMBEDDING_MODEL = "minilm"


def _chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split on size. Whole-drawer content above chunk_size embeds as one
    vector that matches nothing well, and the library add_drawer will not
    split it -- only mcp_server's version does."""
    return [text[i : i + size] for i in range(0, len(text), size)] or [text]


@dataclass(frozen=True)
class MemoryHit:
    """One search result, flattened out of mempalace's response dict.

    Flattened deliberately: search_memories returns a dict whose shape is
    mempalace's business, and letting it reach 14b's prompt renderer would
    put a vendor schema in a system prompt.
    """

    content: str
    room: str
    added_by: str
    score: float


class MemoryStore:
    """One project's palace.

    Construction validates the wing and resolves paths; it opens nothing.
    That split is rudra_paths()/ensure_layout()'s (A1.43): a constructor
    that creates a ChromaDB store as a side effect makes every read-only
    command mutate the filesystem.
    """

    def __init__(self, project_root: Path, backend: str = "chroma") -> None:
        self.project_root = Path(project_root)
        self.wing = wing_for(self.project_root)
        self.backend = backend
        self.collection_name = RUDRA_COLLECTION
        self.palace_path = str(rudra_paths(self.project_root).memory_palace)
        self._collection: Any = None

    def _open(self) -> Any:
        """Open the collection, creating it on first use.

        Raises MemoryUnavailable rather than returning None, so a caller
        that forgot the `degrades` decorator fails loudly in tests instead
        of quietly writing nothing.
        """
        if self._collection is not None:
            return self._collection

        import os

        from mempalace import palace

        os.environ["MEMPALACE_EMBEDDING_MODEL"] = EMBEDDING_MODEL
        Path(self.palace_path).mkdir(parents=True, exist_ok=True)
        collection = palace.get_collection(
            palace_path=self.palace_path,
            collection_name=self.collection_name,
            backend=self.backend,
            create=True,
        )
        if collection is None:
            msg = f"mempalace returned no collection for {self.palace_path}"
            raise MemoryUnavailable(msg)
        self._collection = collection
        return collection

    @degrades(default=False)
    def write(self, entry: MemoryEntry) -> bool:
        """File one memory. Idempotent on identical content."""
        from datetime import datetime

        from mempalace.ids import ID_RECIPE, make_drawer_id_from_content

        collection = self._open()
        pieces = _chunks(entry.content)
        filed_at = datetime.now().isoformat()

        # Hash the WHOLE entry once, then suffix the chunk index -- the
        # convention mcp_server.tool_add_drawer uses for its own chunked
        # path. Hashing each piece separately looked equivalent and is not:
        # an entry whose chunks repeat ("x " * 1600 splits into four
        # identical pieces) produced four identical ids and ChromaDB
        # rejected the whole upsert with DuplicateIDError, so the write
        # degraded to False and the memory was silently lost. Idempotency
        # survives, because the base still comes from the full content.
        base = make_drawer_id_from_content(self.wing, entry.room, entry.content)
        single = len(pieces) == 1

        ids, documents, metadatas = [], [], []
        for index, piece in enumerate(pieces):
            ids.append(base if single else f"{base}_chunk_{index:06d}")
            documents.append(piece)
            metadatas.append(
                {
                    "wing": self.wing,
                    "room": entry.room,
                    "source_file": entry.source,
                    "added_by": entry.added_by,
                    "filed_at": filed_at,
                    "chunk_index": index,
                    "id_recipe": ID_RECIPE,
                }
            )
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        return True

    @degrades(default=[])
    def search(self, query: str, room: str | None = None, limit: int = 5) -> list[MemoryHit]:
        """Semantic search over this project's palace.

        collection_name is passed for the same reason it is on open: it has
        no env override, so a user's global config.json would otherwise
        decide which collection is searched.

        The `added_by` lookup is not redundant. search_memories returns
        drawer_id / text / wing / room / similarity and no `added_by` at
        all, so the tag every drawer carries would be lost exactly where
        14b needs it to label a hit and 14c needs it to filter a purge.
        One `get` by the ids already in hand is the cheapest way back to it.
        """
        from mempalace.searcher import search_memories

        collection = self._open()
        result = search_memories(
            query=query,
            palace_path=self.palace_path,
            wing=self.wing,
            room=room,
            n_results=limit,
            collection_name=self.collection_name,
        )
        if result.get("error"):
            msg = f"search failed: {result['error']}"
            raise MemoryUnavailable(msg)

        rows = list(result.get("results") or [])[:limit]
        if not rows:
            return []

        ids = [row.get("drawer_id", "") for row in rows]
        got = collection.get(ids=ids, include=["metadatas"])
        by_id = dict(zip(got.get("ids") or [], got.get("metadatas") or [], strict=False))

        hits = []
        for row in rows:
            meta = by_id.get(row.get("drawer_id", "")) or {}
            hits.append(
                MemoryHit(
                    content=row.get("text", ""),
                    room=str(row.get("room", "")),
                    added_by=str(meta.get("added_by", "")),
                    score=float(row.get("similarity", 0.0)),
                )
            )
        return hits

    @degrades(default=0)
    def count(self) -> int:
        """How many drawers this project's palace holds."""
        return int(self._open().count())

    @degrades(default=[])
    def raw_metadatas(self) -> list[dict]:
        """Every drawer's metadata. For tests and for 14c's `list`."""
        got = self._open().get(include=["metadatas"])
        return list(got.get("metadatas") or [])


__all__ = ["CHUNK_CHARS", "EMBEDDING_MODEL", "RUDRA_COLLECTION", "MemoryHit", "MemoryStore"]
