# Memory use

Measured on 2026-10-05 with `scripts/measure_memory.py` on an Apple Silicon laptop (default
settings: `bge-small-en`, device `auto`, which uses the GPU through MPS). The project indexed
was `src/nexus_mcp` (42 files, 485 chunks). Each phase ran in a fresh process, so its peak
does not include an earlier phase. The figure is peak resident memory (RSS), imports included.

| Phase | Peak RSS | What it is |
|---|---|---|
| idle | 87 MB | The server is created. No tool has run |
| restore | 271 MB | A new process reattaches to the stored index and answers a graph query. No model is loaded, but the libraries are |
| search | 460 MB | A new process runs one search. This loads the embedding model |
| index | 505 MB | A full index of the project (parse, embed in batches of 32, store) |

## What this means

- The old target of **350 MB is not met** once the embedding model is loaded. Idle is far below
  it, and a session that only uses `status`, `graph`, `map` or `analyze` stays near 270 MB.
- Indexing a small project costs about as much as one search. A large repository will cost more:
  the chunk batches are small, but the graph and the call-edge metadata grow with the number of
  files (a 14,000-file Python library peaked near 770 MB for parsing and call resolution alone).
- The numbers come from one machine, one small project and one model. Treat them as an order of
  magnitude. A GPU-less Linux machine will differ.

## Ways to lower it (not done)

Smaller `NEXUS_EMBEDDING_BATCH_SIZE`, a lower `max_seq_length`, an int8 model, and unloading the
model after an idle period. The int8 ONNX models that were tried in
`research/embedding-models-2026-10.md` used more memory, not less, so this is not a quick win.

## Reproduce

```bash
PYTHONPATH=src python scripts/measure_memory.py src/nexus_mcp
```
