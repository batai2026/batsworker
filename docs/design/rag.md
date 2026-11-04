# Local workspace RAG (semantic index)

> Source: `coworker/rag.py` (chunking, VectorStore, reindex/search, tools),
> wired in `coworker/agent.py::build_engine` (registration when configured),
> `risk.py` (both tools classified EGRESS), `manager.py` (`_rag_config`).

A local semantic index over workspace text files, exposed to the agent as two
tools: `semantic_search` (a read — query the index) and `workspace_reindex`
(EGRESS + approval-gated — sends file contents to the embedding endpoint).

## Local-first

The ONLY outbound call is the embedding request itself, sent to the
OpenAI-compatible endpoint the user configured (`embedding_base_url` +
`embedding_api_key` + `embedding_model` in config.toml, all user-global).
Vectors and chunk text live in a local SQLite store (`data_dir/rag.db` or
`workspace/.coworker/rag.db`) and are ranked by **brute-force cosine in
Python** — no vector extension dependency. At the v1 caps (whitelisted text
extensions, ≤1 MB per file, ≤2k chunks) brute force is single-digit
milliseconds; an ANN index would be complexity without a payoff.

## Indexing

- Whitelisted text extensions (`.md .py .ts .json .yaml …`); binaries and
  known-junk dirs (`node_modules .git .venv …`) skipped; files > 1 MB skipped.
- Sliding-window chunks (1200 chars, 150 overlap) with a paragraph-boundary
  preference.
- **Incremental by content hash**: unchanged files are skipped (the embedder
  is the expensive part); files that disappeared are dropped.
- Symlink escape check: collected files must resolve inside the workspace.
- If the embedding endpoint is unreachable mid-index, the file is marked
  failed and skipped — the rest of the index still completes.

## Tool surfaces

| Tool | Risk | Behavior |
|---|---|---|
| `semantic_search(query, k?)` | READ (query text only) | embed query → cosine top-k → `{path, chunk, score, text}` per hit |
| `workspace_reindex()` | **EGRESS, approval-gated** | incremental re-index; returns stats `{embedded, skipped, removed, failed}` |

Both tools report honestly when the embedding endpoint isn't configured
(`"no embedding endpoint configured — set embedding_base_url…"`) instead of
guessing one, and they don't register at all when the config is absent.

## v1 boundaries

- No hosted embedding service assumption — any OpenAI-compatible
  `/embeddings` endpoint works (OpenAI, Ollama's shim, Z AI, DeepSeek, …).
- Anthropic has no embeddings endpoint; users on Anthropic-only configs can't
  use RAG until they add a second provider or Ollama.
- No ANN index (brute force is sufficient at the enforced caps).
- No automatic re-index on file change (the agent or user calls re-index;
  a file-watch trigger is the natural next step once Standing Goals gain a
  file-system watcher).
