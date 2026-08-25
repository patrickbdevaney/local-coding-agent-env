# Repo map

Generated 2026-08-25 15:23. 6 files.
Regenerate with the `repomap` tool after structural changes.

- `fetcher/src/main.rs` (325 lines) — Concurrent fetch + text extraction for the research loop.
  - Doc, Link, to_links, to_text, find, truncate_chars, fetch, main
- `local/keyring.py` (29 lines) — LOCAL ONLY -- gitignored, never committed, not part of the published harness.
  - _env, keys
- `mcp/memory.py` (280 lines)
  - agent_dir, slug, tokenize, chunks_of, corpus, bm25, build_repomap, memory_write, call, handle
- `mcp/research.py` (370 lines)
  - wide_lane, llm, search, fetch_many, fetch, terms, score_link, deep_research, one, handle
- `mcp/sweep.py` (234 lines)
  - llm, read_paths, summarise_local, sweep, handle
- `router/gateway.py` (451 lines)
  - load_env, keys, next_key, rank_model, Pool, __init__, refresh, available, penalise, reward, _refresher, ladder, _tool_signatures, detect_loop
