"""Local workspace RAG (v1) — chunk, embed, and search workspace files.

Local-first in the strict sense: the ONLY outbound call is the embedding
request itself, sent to the OpenAI-compatible embeddings endpoint the user
configured (``config.embedding_base_url`` + ``embedding_api_key`` +
``embedding_model``) — the same trust level as chatting about those files.
Vectors and chunk text live in a local SQLite store under the state dir and
are served by the `semantic_search` tool with zero network.

v1 pragmatics:
  - **No vector extension**: chunks for a workspace are loaded and ranked by
    brute-force cosine in Python. With the default caps (whitelisted text
    extensions, ≤1 MB per file, ≤2k chunks) this is single-digit milliseconds —
    an ANN index would be complexity without a payoff at this scale.
  - **Incremental by content hash**: unchanged files are skipped on re-index
    (the embedder is the expensive part); files that disappeared are dropped.
  - Fixed extension whitelist — binary formats can't be chunked usefully.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Text formats worth indexing. Everything else (binaries, notebooks, archives)
# is skipped — chunking them produces noise, not knowledge.
TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".py", ".ts", ".tsx", ".js", ".jsx", ".json",
    ".yaml", ".yml", ".toml", ".csv", ".html", ".css", ".rs", ".go", ".java",
    ".sh", ".sql", ".ini", ".cfg",
}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".coworker"}
MAX_FILE_BYTES = 1024 * 1024  # 1 MB per file
MAX_CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150
MAX_CHUNKS = 2000


def chunk_text(text: str, *, size: int = MAX_CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Sliding-window chunks with a paragraph-boundary preference: split on blank
    lines when the paragraph fits the window, else hard-wrap with overlap."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    paragraphs = [p for p in text.split("\n\n")]
    chunks: List[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > size:
            # Hard-wrap oversized paragraphs with overlap.
            if current:
                chunks.append(current)
                current = ""
            step = max(size - overlap, 1)
            for i in range(0, len(para), step):
                chunks.append(para[i : i + size])
            continue
        if len(current) + len(para) + 2 <= size:
            current = f"{current}\n\n{para}".strip()
        else:
            if current:
                chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return chunks[:MAX_CHUNKS]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_files(workspace: str | Path) -> List[Path]:
    """Text files under the workspace worth indexing (whitelist extensions,
    skip known junk dirs, size-capped). Symlinks that escape the workspace are
    excluded — the index is the workspace's, not the filesystem's."""
    root = Path(workspace)
    out: List[Path] = []
    resolved_root = root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            if not path.resolve().is_relative_to(resolved_root):
                continue
        except (OSError, ValueError):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        out.append(path)
    return out


class EmbeddingClient:
    """OpenAI-compatible /embeddings client (OpenAI, Ollama's OpenAI shim, Z AI,
    DeepSeek, … all expose the same shape)."""

    def __init__(self, base_url: str, api_key: str, model: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def embed(self, texts: List[str]) -> List[List[float]]:
        import httpx

        resp = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return [item["embedding"] for item in data]


class VectorStore:
    """SQLite chunk store: one row per chunk, vectors as JSON floats (loaded
    once per query and ranked in Python — see the module docstring)."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS chunks (
                    workspace TEXT NOT NULL,
                    path TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    hash TEXT NOT NULL,
                    text TEXT NOT NULL,
                    vec TEXT NOT NULL,
                    PRIMARY KEY (workspace, path, idx)
                );
            """)
            self._conn.commit()

    def file_hash(self, workspace: str, path: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT hash FROM chunks WHERE workspace=? AND path=? LIMIT 1",
                (workspace, path),
            ).fetchone()
        return row["hash"] if row else None

    def replace_file(
        self, workspace: str, path: str, hash: str, chunks: List[str], vecs: List[List[float]]
    ) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM chunks WHERE workspace=? AND path=?", (workspace, path)
            )
            for idx, (text, vec) in enumerate(zip(chunks, vecs)):
                self._conn.execute(
                    "INSERT INTO chunks (workspace, path, idx, hash, text, vec) VALUES (?, ?, ?, ?, ?, ?)",
                    (workspace, path, idx, hash, text, json.dumps(vec)),
                )
            self._conn.commit()

    def drop_file(self, workspace: str, path: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM chunks WHERE workspace=? AND path=?", (workspace, path)
            )
            self._conn.commit()

    def indexed_paths(self, workspace: str) -> List[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT path FROM chunks WHERE workspace=?", (workspace,)
            ).fetchall()
        return [r["path"] for r in rows]

    def all_vectors(self, workspace: str) -> List[Tuple[str, int, str, List[float]]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT path, idx, text, vec FROM chunks WHERE workspace=?", (workspace,)
            ).fetchall()
        return [(r["path"], r["idx"], r["text"], json.loads(r["vec"])) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def reindex_workspace(
    workspace: str,
    *,
    embedder: EmbeddingClient,
    store: VectorStore,
    log: Optional[Callable[[str], None]] = None,
) -> Dict[str, int]:
    """Incremental re-index: hash-skip unchanged files, embed new/changed ones in
    batches, drop files that disappeared. Returns a stats dict for the tool."""
    ws_key = str(Path(workspace).resolve())
    say = log or (lambda _msg: None)
    present: Dict[str, str] = {}
    embedded = skipped = removed = failed = 0
    for path in collect_files(workspace):
        rel = str(path.relative_to(Path(workspace).resolve()))
        try:
            hash_ = file_hash(path)
        except OSError:
            continue
        present[rel] = hash_
        if store.file_hash(ws_key, rel) == hash_:
            skipped += 1
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            failed += 1
            continue
        chunks = chunk_text(text)
        if not chunks:
            continue
        try:
            vecs = embedder.embed(chunks)
        except Exception as exc:
            say(f"embedding failed for {rel}: {exc}")
            failed += 1
            continue
        store.replace_file(ws_key, rel, hash_, chunks, vecs)
        embedded += 1
    # Drop files that disappeared since the last index.
    for path in store.indexed_paths(ws_key):
        if path not in present:
            store.drop_file(ws_key, path)
            removed += 1
    return {"embedded": embedded, "skipped": skipped, "removed": removed, "failed": failed}


def search_workspace(
    workspace: str,
    query: str,
    *,
    embedder: EmbeddingClient,
    store: VectorStore,
    k: int = 8,
) -> List[Dict[str, Any]]:
    ws_key = str(Path(workspace).resolve())
    [qvec] = embedder.embed([query])
    rows = store.all_vectors(ws_key)
    scored = [(_cosine(qvec, vec), path, idx, text) for path, idx, text, vec in rows]
    scored.sort(key=lambda t: t[0], reverse=True)
    return [
        {"path": path, "chunk": idx, "score": round(score, 4), "text": text}
        for score, path, idx, text in scored[: max(1, min(k, 25))]
    ]


def make_embedder(cfg: Dict[str, Any]) -> Optional[EmbeddingClient]:
    """Build an EmbeddingClient from a config dict, or None when not configured
    (the tools then report that honestly instead of guessing an endpoint)."""
    base = str(cfg.get("embedding_base_url") or "").strip()
    key = str(cfg.get("embedding_api_key") or "").strip()
    model = str(cfg.get("embedding_model") or "").strip()
    if not base or not model:
        return None
    return EmbeddingClient(base, key, model)


# -- agent-facing tools ----------------------------------------------------------


def _meta(name: str, description: str, *, approval: bool, properties: Dict[str, Any], required: List[str]):
    """Tool wrapper in the same dialect as automation/tools.py: schema + aisuite
    metadata (risk via metadata — semantic_search stays a read, re-index is an
    approval-gated egress because it sends file contents to the endpoint)."""
    try:
        import aisuite as _ai
    except ImportError:  # pragma: no cover
        _ai = None

    def wrap(func: Callable[..., Any]) -> Callable[..., Any]:
        func.__name__ = name
        func.__doc__ = description
        metadata = {
            "name": name,
            "category": "search",
            "risk_level": "medium" if approval else "low",
            "capabilities": ["rag"],
            "requires_approval": approval,
        }
        try:
            func.__aisuite_tool_metadata__ = _ai.ToolMetadata(**metadata)
        except Exception:
            class _TM:  # minimal shim if ToolMetadata's shape drifts
                pass
            for key, value in metadata.items():
                setattr(_TM, key, value)
            func.__aisuite_tool_metadata__ = _TM()
        func.__coworker_schema__ = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
        return func

    return wrap


def rag_tools(
    *,
    workspace: str,
    config: Dict[str, Any],
    state_dir: Optional[str | Path] = None,
) -> list[Callable[..., Any]]:
    """The semantic_search / workspace_reindex pair, bound to one workspace and
    the user's embedding config. Not-configured embedders make both tools return
    an honest error (they only register when the config is complete, but the
    config can change between build and call)."""

    store = VectorStore(
        Path(state_dir) / "rag.db" if state_dir else Path(workspace) / ".coworker" / "rag.db"
    )

    def _embedder() -> EmbeddingClient:
        embedder = make_embedder(config)
        if embedder is None:
            raise ValueError(
                "no embedding endpoint configured — set embedding_base_url, "
                "embedding_api_key and embedding_model in config.toml"
            )
        return embedder

    @_meta(
        "semantic_search",
        "Search this workspace's semantic index for chunks relevant to a natural-language "
        "query. The index must be built first with workspace_reindex. Returns the most "
        "relevant file excerpts with paths and scores.",
        approval=False,
        properties={
            "query": {"type": "string", "description": "What to look for, in plain language."},
            "k": {"type": "number", "description": "Max results (default 8, max 25)."},
        },
        required=["query"],
    )
    def semantic_search(query: str, k: int = 8):
        try:
            hits = search_workspace(
                workspace, query, embedder=_embedder(), store=store, k=int(k)
            )
        except ValueError as exc:
            return {'error': str(exc)}
        except Exception as exc:
            return {"error": f"semantic search failed: {exc}"}
        if not hits:
            return {
                "ok": True,
                "results": [],
                "hint": "the index looks empty — run workspace_reindex first",
            }
        return {"ok": True, "results": hits}

    @_meta(
        "workspace_reindex",
        "Rebuild this workspace's semantic index: read whitelisted text files, chunk them, "
        "and send the chunks to the configured embedding endpoint. Unchanged files are "
        "skipped. This sends file CONTENTS to the embedding endpoint.",
        approval=True,
        properties={},
        required=[],
    )
    def workspace_reindex():
        try:
            stats = reindex_workspace(workspace, embedder=_embedder(), store=store)
        except ValueError:
            raise
        except Exception as exc:
            return {"error": f"re-index failed: {exc}"}
        return {"ok": True, **stats}

    return [semantic_search, workspace_reindex]
