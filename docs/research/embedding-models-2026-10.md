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
4. **Memory is the real problem.** The PyTorch default peaked at 902 MB in this eval,
   which indexes two suites and searches in one process. The ONNX candidates peak at
   1.6–2.2 GB. A separate measurement of the default model (`docs/MEMORY.md`) gives about
   90 MB idle, 460 MB with the model loaded and 500 MB while indexing a small project,
   so the old target of 350 MB is not met once the model loads.
5. **int8 of the same model keeps the quality.** `bge-small-en-int8` matches the fp32
   model on every metric, but it is slower to index on CPU (41 s against 30 s) and uses
   more memory in this setup. The int8 file is not worth shipping for `bge-small-en`.

## What this changes

- `onnx_file` and `max_seq_length` now work in the registry (`embedding_service.py`), so a
  future model needs only a registry entry.
- The index records its model and rebuilds when the model changes.
- `Dockerfile`, `smithery.yaml` and `glama.json` default to `bge-small-en` (issues #1, #7).

## Second round, 2026-10-05: a larger project and a public project

Two new suites, run with `python -m evals.retrieval.run --candidates bge-small-en,granite-97m-r2-int8
--suites jobscout,flask`. `flask` is `pallets/flask` at `d73fa1c` (22 queries, 24 files). `jobscout`
is a private Python service of about 200 modules (27 queries); its query file is git-ignored.
Each suite has identifier-style queries (7 and 5) beside the prose queries. CPU only, one run.

| Suite | Model | vector hit@1 | hybrid hit@1 | hybrid-no-graph hit@1 | bm25 hit@1 | Index s | Peak RSS MB |
|---|---|---|---|---|---|---|---|
| flask | bge-small-en | 0.64 | 0.50 | 0.59 | 0.55 | 26 | 1,132 |
| flask | granite-97m-r2-int8 | 0.50 | 0.41 | 0.36 | 0.55 | 40 | 2,435 |
| jobscout | bge-small-en | 0.63 | 0.70 | 0.70 | 0.48 | 135 | 1,132 |
| jobscout | granite-97m-r2-int8 | 0.59 | 0.67 | 0.74 | 0.48 | 212 | 2,435 |

The peak RSS is one process per model that indexed both suites. One query is worth 3.7 points on
`jobscout` and 4.5 points on `flask`, so a difference of one or two queries is noise.

1. **`bge-small-en` stays the default.** It is ahead on `flask` (vector 0.64 against 0.50, three
   queries) and level on `jobscout` (0.63 against 0.59; hybrid 0.70 against 0.67). No result
   reaches the 5-point bar in granite's favour that holds on both suites. Granite indexes 1.5 times
   slower and its process peaks at twice the memory. This agrees with the 2026-10-03 round.
2. **Fusion is the bigger lever, and it is not consistent.** On `jobscout`, hybrid beats vector
   (0.70 against 0.63). On `flask` it loses (0.50 against 0.64). The loss is on prose queries
   (hybrid 0.41, vector 0.59), where bm25 has no shared words to match. Removing the graph list
   gives `bge-small-en` back 9 points on `flask` and changes nothing on `jobscout`. For granite
   the effect differs: it gains 7 points on `jobscout` and loses 5 on `flask`. The graph list is
   not a reliable gain. Roadmap item 12 (tune the weights) needs these suites.
3. **Identifier queries are easy for vectors.** On `jobscout` the vector score is 1.00 for 7 of 7
   identifier queries, while bm25 reaches 0.71. Plain keyword search is not the reference for
   identifiers here. Prose queries are the hard case (0.41 to 0.60 hit@1 for `bge-small-en`).
4. **`jina-code` was dropped.** Its first run on these suites was still indexing after more than
   90 minutes and hit the time limit. It is deprecated (see ADR-004, amendment).

## Third round, 2026-10-06: ranking changes, not models

Run with `python -m evals.retrieval.run --candidates bge-small-en
--suites jobscout,flask,nexus_mcp,shop_repo --label source-first`. The new mode
`hybrid-source-first` is what `core_api.search` does now: no graph list, and test files after
source files in each engine list before fusion. `hybrid` is the earlier production order.

| Suite | vector hit@1 | hybrid (earlier) hit@1 | hybrid-source-first (now) hit@1 |
|---|---|---|---|
| jobscout | 0.63 | 0.70 | 0.70 |
| flask | 0.64 | 0.50 | 0.59 |
| nexus_mcp | 0.72 | 0.60 | 0.72 |
| shop_repo | 0.75 | 0.25 | 0.38 |

1. **The graph list is dropped from the default fusion.** The whole gain in this table comes
   from that: `hybrid-no-graph` gives the same numbers. It raised hit@1 on no suite.
2. **Source before tests does not show in these suites**, because they index source folders
   only. It was measured on the stored django index (45,744 chunks, 70% in test files) with the
   12 benchmark task prompts as queries: hit@1 6 to 8 of 12, hit@3 10 to 11, and test files in
   the top 5 from 14 of 60 to 0.
3. **A wider list into fusion is worse.** A first design gave fusion 4 times `limit` results
   for each engine; `nexus_mcp` hit@1 fell from 0.60 to 0.56 and `flask` hit@5 from 0.91 to
   0.86. The final design fetches 4 times `limit`, moves tests down, then gives fusion the
   same 2 times `limit` as before.
4. **Hybrid still does not beat vector on three of four suites.** bm25 costs hit@1 on prose
   queries. The weights 0.5 and 0.3 are the next thing to tune.
5. **The reranker lowers accuracy, so it is now off by default.** `flashrank` was not
   installed before, so `rerank=True` did nothing in every earlier run. Installed and measured
   (mode `hybrid-rerank`, hit@1, against `hybrid-source-first`):

   | Suite | no reranker | MiniLM-L-12 (the default model) | TinyBERT-L-2 |
   |---|---|---|---|
   | jobscout | 0.70 | 0.59 | 0.56 |
   | flask | 0.59 | 0.64 | 0.64 |
   | nexus_mcp | 0.72 | 0.44 | 0.52 |
   | shop_repo | 0.38 | 0.50 | 0.38 |
   | ms per query | 9 to 20 | 1,300 to 4,100 | 50 to 130 |

   Both models are trained on web passages (MS MARCO), not code. hit@5 also fell on three
   suites. `search(rerank=True)` still works for a caller that wants it.
6. **A bug found on the way:** with `flashrank` installed, compact `search` raised on the
   numpy score that the reranker returns. The scores are plain floats now.

## Next steps

1. Measure idle and indexing memory for the default model in separate runs. Done: `docs/MEMORY.md`.
2. Fix `graph_relevance_search` (stop words, whole-word match), then re-run the eval. Done.
3. Re-tune the fusion weights on the `flask` and `jobscout` suites, split by query kind.
4. Run the live agent benchmark per model (`benchmarks.runner --embedding-models`) to see
   whether a retrieval difference changes the answers of an agent.

## Reproduce

```bash
PYTHONPATH=src python -m evals.retrieval.run --candidates bge-small-en --label baseline
PYTHONPATH=src python -m evals.retrieval.run \
  --candidates bge-small-en-int8,granite-97m-r2-int8,gte-modernbert-int8 --label candidates
```

The ONNX candidates need `optimum-onnx[onnxruntime]`.
