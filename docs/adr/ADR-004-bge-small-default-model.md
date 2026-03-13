# ADR-004: jina-code as Default Embedding Model (Updated)

**Status:** Superseded (originally bge-small-en, now jina-code)
**Date:** 2026-03-11 (updated 2026-03-12)
**Decision makers:** Shreyas Jagannath

## Context
CodeGrok defaulted to CodeRankEmbed (137M params, 768 dims, ~500MB download). While it produced the best code search quality, the large download size was the #1 complaint from users and made first-run experience poor. The initial decision was to default to bge-small-en. After further evaluation, the default was changed to jina-code for better code search quality.

## Decision
Default to jina-code (jinaai/jina-embeddings-v2-base-code, 768 dims, ONNX, code-specific). Two models are supported:

1. **jina-code** (default) — 768d, code-specific, best quality for code search
2. **bge-small-en** — 384d, smallest download (~50MB), good for general text

Only registered model names are accepted. Custom model names raise `ConfigurationError`.

GPU/MPS auto-detection is enabled by default (`NEXUS_EMBEDDING_DEVICE=auto`): CUDA > MPS > CPU.

## Consequences
- **Easier:** Code-specific default gives better search quality out of the box, GPU acceleration when available, two well-tested models to choose from
- **Harder:** Larger default download than bge-small-en, 768-dim vectors use more storage than 384-dim

## Alternatives Considered
- **bge-small-en as default:** Was the original default — smaller download but not code-specialized
- **CodeRankEmbed:** Removed — too large for default use
- **all-MiniLM-L6-v2:** Rejected — similar size to bge-small but lower benchmark scores
- **CodeSage-Small:** Rejected — 200MB, good quality but 1024 dims increases storage
