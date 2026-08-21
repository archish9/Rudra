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

from collections.abc import Sequence
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


@dataclass(frozen=True)
class MemoryRecord:
    """One stored drawer, as the CLI sees it.

    Distinct from MemoryHit because listing and searching answer different
    questions: a hit carries a relevance score and no id, a record carries
    the id `delete` needs and no score. Merging them would give every
    caller a field that is meaningless half the time.
    """

    id: str
    content: str
    room: str
    added_by: str
    filed_at: str


def _reassemble(triples: Any) -> list[MemoryRecord]:
    """One MemoryRecord per logical memory, not per stored drawer.

    `write` splits content over CHUNK_CHARS-sized drawers whose ids are
    `<base>_chunk_NNNNNN`, but this returned one row per drawer with no
    reassembly -- and export_memory consumes exactly these rows. Measured on
    a real palace: one 2300-char memory (well under MAX_CONTENT, and typical
    of the summaries `remember` files) stored as 3 drawers, exported as 3,
    and re-imported into the SAME palace as 3 more, because import re-hashes
    each fragment as a whole entry so its id no longer matches. So the
    documented idempotency was false for any memory over CHUNK_CHARS,
    re-importing a backup silently doubled the palace, and a restore into a
    fresh palace produced unrelated mid-sentence fragments (CR-A2).

    Grouped by the id prefix and ordered by the `chunk_index` metadata that
    `write` already records.
    """
    grouped: dict[str, list[tuple[int, str, Any]]] = {}
    order: list[str] = []
    for drawer_id, doc, meta in triples:
        text = str(drawer_id)
        base, marker, suffix = text.partition("_chunk_")
        key = base if marker and suffix.isdigit() else text
        index = int(suffix) if marker and suffix.isdigit() else 0
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append((index, str(doc), meta))

    records: list[MemoryRecord] = []
    for key in order:
        pieces = sorted(grouped[key], key=lambda piece: piece[0])
        meta = pieces[0][2]
        records.append(
            MemoryRecord(
                id=key,
                content="".join(piece[1] for piece in pieces),
                room=str(meta.get("room", "")),
                added_by=str(meta.get("added_by", "")),
                filed_at=str(meta.get("filed_at", "")),
            )
        )
    return records


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

    @degrades(default=[])
    def list_entries(
        self,
        room: str | None = None,
        added_by: str | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        """Every drawer, newest first, optionally narrowed.

        Filtered in Python rather than through a `where` clause: the
        predicates are two exact-match fields over a store that is small by
        construction (one project's decisions), and a backend-specific
        filter dialect is a second thing to get right per backend.
        """
        got = self._open().get(include=["documents", "metadatas"])
        rows = _reassemble(
            zip(
                got.get("ids") or [],
                got.get("documents") or [],
                got.get("metadatas") or [],
                strict=False,
            )
        )
        if room is not None:
            rows = [r for r in rows if r.room == room]
        if added_by is not None:
            rows = [r for r in rows if r.added_by == added_by]
        rows.sort(key=lambda r: r.filed_at, reverse=True)
        return rows[:limit]

    @degrades(default=0)
    def delete(self, ids: Sequence[str]) -> int:
        """Remove memories by id. Returns how many were named.

        Irreversible, which is why the confirmation lives in the CLI and
        not here: a library call that prompts cannot be scripted.

        Takes the ids `list_entries` reports, which are per MEMORY, so an id
        naming a chunked entry must take every one of its drawers with it --
        otherwise `rudra memory forget` on a memory over CHUNK_CHARS would
        silently delete nothing and leave the fragments behind (CR-A2).
        """
        ids = list(ids)
        if not ids:
            return 0
        collection = self._open()
        named = set(ids)
        stored = [str(one) for one in (collection.get(include=[]).get("ids") or [])]
        doomed = [one for one in stored if one in named or one.partition("_chunk_")[0] in named]
        collection.delete(ids=doomed or ids)
        return len(ids)

    @degrades(default=0)
    def count(self) -> int:
        """How many drawers this project's palace holds."""
        return int(self._open().count())

    @degrades(default=[])
    def raw_metadatas(self) -> list[dict]:
        """Every drawer's metadata. For tests and for 14c's `list`."""
        got = self._open().get(include=["metadatas"])
        return list(got.get("metadatas") or [])


__all__ = [
    "CHUNK_CHARS",
    "EMBEDDING_MODEL",
    "RUDRA_COLLECTION",
    "MemoryHit",
    "MemoryRecord",
    "MemoryStore",
]
