# ADR-004: bge-small-en as Default Embedding Model

**Status:** Accepted
**Date:** 2026-03-11
**Decision makers:** Shreyas Jagannath

## Context
CodeGrok defaulted to CodeRankEmbed (137M params, 768 dims, ~500MB download). While it produced the best code search quality, the large download size was the #1 complaint from users and made first-run experience poor.

## Decision
Default to BAAI/bge-small-en-v1.5 (33.4M params, 384 dims, ~50MB). CodeRankEmbed remains available as opt-in via `NEXUS_EMBEDDING_MODEL=coderankembed`.

## Consequences
- **Easier:** 10x smaller download, faster first-run, meets RAM target easily, adequate quality for small/medium codebases
- **Harder:** Lower search quality vs CodeRankEmbed on code-specific queries, not code-specialized (general text model), 384 dims vs 768 means less expressive embeddings

## Alternatives Considered
- **CodeRankEmbed as default:** Rejected — 500MB download blocks adoption
- **all-MiniLM-L6-v2:** Rejected — similar size to bge-small but lower benchmark scores
- **CodeSage-Small:** Rejected — 200MB, good quality but 1024 dims increases storage
- **jina-embeddings-v2-base-code:** Rejected — ~500MB, same size problem as CodeRankEmbed
