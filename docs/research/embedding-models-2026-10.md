# Embedding models for code search — October 2026

Status: measured on 2026-10-03 with `evals/retrieval`. Raw numbers:
`evals/results/retrieval-baseline.json` and `evals/results/retrieval-candidates.json`
(both git-ignored; rerun the commands at the end).

## Question

Is there a better local embedding model than `bge-small-en` for nexus-mcp? The limits
are: CPU only, no API calls, an Apache-2.0 or MIT licence, no `trust_remote_code`, and a
memory target below 350 MB.

## Decision

**Keep `bge-small-en` as the default. Do not add a new model to the registry now.**
No candidate beat it by the 5 points of hit@1 that the plan set as the bar, every ONNX
candidate used more memory, and the query sets are too small to separate the models.
The bigger lever is the fusion step, not the model (see Findings 2 and 3).

## Method

- Two suites. `nexus_mcp` indexes `src/nexus_mcp` (25 queries, 42 files, 457 chunks). `shop_repo`
  indexes the eval fixture (8 queries, 18 files, 52 chunks). Each query names the files that a good
  answer must reach (`evals/retrieval/queries.yaml`).
- Queries describe what the code does in plain words and avoid its identifiers. This
  favours embeddings over keyword search. Real users often type identifiers, so treat
  the bm25 rows as a floor and the vector rows as a ceiling.
- Modes: `bm25` (no model), `vector` (the model alone), `hybrid` (vector, bm25 and graph
  fused with the production weights 0.5/0.3/0.2), `hybrid-no-graph`. No reranker, no
  live grep.
- Every model ran on CPU with a 512-token cap. Each model ran in its own process.
- Candidates are Apache-2.0 or MIT, need no `trust_remote_code`, and use an int8 ONNX file:
  - `bge-small-en-int8`: `Xenova/bge-small-en-v1.5`, `onnx/model_quantized.onnx` (34 MB).
  - `granite-97m-r2-int8`: `ibm-granite/granite-embedding-97m-multilingual-r2`,
    `onnx/model_quint8_avx2.onnx` (98 MB).
  - `gte-modernbert-int8`: `Alibaba-NLP/gte-modernbert-base`, `onnx/model_int8.onnx` (150 MB).
- Not measured: `jina-code` (needs `trust_remote_code` and a 612 MB fp32 download),
  `granite-embedding-small-english-r2` (its ONNX weights sit in an external `.onnx_data`
  file that the loader does not fetch), and any model that needs custom code or has a
  non-commercial licence.

## Results (hit@1, vector mode, then the fused modes)

| Model | nexus_mcp vector | shop_repo vector | nexus_mcp hybrid | nexus_mcp hybrid-no-graph | Peak RSS MB | Index s (nexus_mcp) |
|---|---|---|---|---|---|---|
| bge-small-en (PyTorch, shipped) | 0.76 | 0.75 | 0.60 | 0.72 | 902 | 30 |
| bge-small-en-int8 (ONNX) | 0.76 | 0.75 | 0.60 | 0.72 | 1,578 | 41 |
| granite-97m-r2-int8 (ONNX) | 0.48 | 0.88 | 0.72 | 0.72 | 1,587 | 60 |
| gte-modernbert-int8 (ONNX) | 0.68 | 1.00 | 0.60 | 0.68 | 2,193 | 223 |

MRR@10 follows the same order. hit@5 is 0.88–1.00 for every model and mode, so every
model reaches the right file; the difference is only in how high it ranks.

## Findings

1. **No model wins on both suites.** `gte-modernbert-int8` is best on `shop_repo`
   (hit@1 1.00) and worse than the shipped model on `nexus_mcp` (0.68 against 0.76).
   `granite-97m-r2-int8` is worst on `nexus_mcp` vector (0.48). One query is worth
   4 points on `nexus_mcp` and 12.5 points on `shop_repo`, so differences of one or two
   queries are noise.
2. **The graph list hurts ranking.** On `nexus_mcp`, removing it raises hybrid hit@1 from
   0.60 to 0.72 for the shipped model. `graph_relevance_search` matches every query word
   of two or more letters as a substring of node names ("to", "in", "an") and scores by
   centrality. Call edges (ADR-019) raise the centrality of hub functions, so this noise
   grows. Fix applied 2026-10-03: whole-word matching on identifier words plus stop words. On a re-run the loss falls from 12 points to 4 (hybrid 0.64 against 0.68 without the graph list; the corpus had grown, so vector hit@1 moved from 0.76 to 0.72). The graph list still does not help; tune or drop it with a larger query set (roadmap item 12).
3. **BM25 hurts on paraphrased queries.** On `shop_repo`, hybrid hit@1 is 0.38 and vector
   is 0.75 for the shipped model. This is partly the query design (no shared words).
   Re-measure with identifier-style queries before changing the weights.
4. **Memory is the real problem.** The PyTorch default peaks at 902 MB for one process
   that indexes and searches. The ONNX candidates peak at 1.6–2.2 GB. CLAUDE.md states a
   target below 350 MB. The 902 MB figure covers indexing (batches of 32 chunks) and
   two suites in one process, so it is a ceiling, not an idle figure; it still needs a
   dedicated measurement. A smaller `embedding_batch_size`, unloading the model between
   batches, and the sequence cap are the first things to test.
5. **int8 of the same model keeps the quality.** `bge-small-en-int8` matches the fp32
   model on every metric, but it is slower to index on CPU (41 s against 30 s) and uses
   more memory in this setup. The int8 file is not worth shipping for `bge-small-en`.

## What this changes

- `onnx_file` and `max_seq_length` now work in the registry (`embedding_service.py`), so a
  future model needs only a registry entry.
- The index records its model and rebuilds when the model changes.
- `Dockerfile`, `smithery.yaml` and `glama.json` default to `bge-small-en` (issues #1, #7).

## Next steps

1. Measure idle and indexing memory for the default model in separate runs.
2. Fix `graph_relevance_search` (stop words, whole-word match), then re-run the eval.
3. Add 20 identifier-style queries and re-tune the fusion weights.
4. Grow the query sets before drawing any model conclusion. Use the django suite of
   `benchmarks/tasks` once its clone exists.

## Reproduce

```bash
PYTHONPATH=src python -m evals.retrieval.run --candidates bge-small-en --label baseline
PYTHONPATH=src python -m evals.retrieval.run \
  --candidates bge-small-en-int8,granite-97m-r2-int8,gte-modernbert-int8 --label candidates
```

The ONNX candidates need `optimum-onnx[onnxruntime]`.
