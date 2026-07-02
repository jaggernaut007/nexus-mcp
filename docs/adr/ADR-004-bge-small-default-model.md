# ADR-004: bge-small-en as Default Embedding Model (Updated)

**Status:** Accepted (originally bge-small-en, briefly jina-code, reverted to bge-small-en 2026-07-02)
**Date:** 2026-03-11 (updated 2026-07-02)
**Decision makers:** Shreyas Jagannath

## Context
CodeGrok defaulted to CodeRankEmbed (137M params, 768 dims, ~500MB download). While it produced the best code search quality, the large download size was the #1 complaint from users and made first-run experience poor. The default was briefly switched to jina-code for better code search quality, then reverted to bge-small-en: the smaller download, no `trust_remote_code` requirement, and lower RAM footprint matter more for first-run experience.

## Decision
Default to bge-small-en (BAAI/bge-small-en-v1.5, 384 dims, PyTorch). Two models are supported:

1. **bge-small-en** (default) — 384d, smallest download (~50MB), no trust_remote_code, good for general text
2. **jina-code** — 768d, code-specific, best quality for code search (ONNX, requires trust_remote_code)

Only registered model names are accepted. Custom model names raise `ConfigurationError`.

GPU/MPS auto-detection is enabled by default (`NEXUS_EMBEDDING_DEVICE=auto`): CUDA > MPS > CPU.

## Consequences
- **Easier:** Small download (~50MB) and no trust_remote_code give a safe, fast first-run experience; 384-dim vectors halve storage vs 768-dim; two well-tested models to choose from
- **Harder:** Not code-specialized — jina-code gives better code search quality for users willing to opt in

## Alternatives Considered
- **jina-code as default:** Was briefly the default — best code search quality, but larger download and requires trust_remote_code
- **CodeRankEmbed:** Removed — too large for default use
- **all-MiniLM-L6-v2:** Rejected — similar size to bge-small but lower benchmark scores
- **CodeSage-Small:** Rejected — 200MB, good quality but 1024 dims increases storage
