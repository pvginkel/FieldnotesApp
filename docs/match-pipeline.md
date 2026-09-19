# Matching and the index

This covers the in-memory index the API keeps over the store, the embedding cache, BM25, the models
pod, and the pipeline that turns a query into ranked candidates.

## The index

The index holds every observation of the checkout, parsed, with the vector of its embedded text
(`area: canonical`, see [observation-file.md](observation-file.md)) and its terms in a BM25 index.
On start it reads every file; after that, after every pull or write, it re-reads only the paths that
changed since the commit it last took in. An update fetches all it needs first and then applies at
once, so a match never sees half an update. If embedding fails during an update, nothing is applied
and the next pull or write reports the same paths again.

## The embedding cache

A directory at `FIELDNOTES_CACHE_DIR`, one file per vector at
`<cache>/<FIELDNOTES_EMBED_MODEL>/<sha256 of the embedded text>`, holding the float32 vector's raw
bytes, written aside and renamed into place. What the cache lacks is embedded and written. It is
content-addressed by model and text, so a reaction costs no embedding, a rewritten canonical costs
one, and deleting the cache costs a full re-embed and nothing else. The server alone writes it.

## BM25

Okapi BM25, k1 = 1.2, b = 0.75, over the same embedded texts. Terms are lowercase runs of
`[a-z0-9]`, joined across `.`, `_`, `/` and `-` into one token (`track_build.py`, `appear-timeout`
from `--appear-timeout`), each such token indexed whole and by its parts as well, so an identifier,
path or error string in a post finds the observation that quotes it. A short list of English stop
words is left out. Only observations sharing a term with the query score.

## The models pod

NGINX in front of two Text Embeddings Inference containers, `BAAI/bge-base-en-v1.5` at `/embed` and
`BAAI/bge-reranker-base` at `/rerank`, at `FIELDNOTES_MODELS_URL`. Embedding goes in chunks of 32
texts, reranking in chunks of 64; vectors come back L2-normalised so a dot product is the cosine;
rerank scores come back through a sigmoid, in 0–1. The client allows 60 s a call.

## The pipeline

For a query text (a post's `area: text`, `/match`'s text, or an observation's own embedded text for
`/neighbors`; see [rest-api.md](rest-api.md)):

1. Embed the query.
2. **Candidates**: the cosine top `FIELDNOTES_MATCH_COSINE_TOP` (default 8) over the matrix, union
   the BM25 top `FIELDNOTES_MATCH_BM25_TOP` (default 4), over every status, closed ones included
   (FR-3): at most 12 with the defaults.
3. **Rerank** each candidate against the query, the query first, in one call. With
   `FIELDNOTES_MATCH_BOTH_DIRECTIONS=true` each candidate is also scored against the query the other
   way round, one call per candidate, and the two scores are averaged; that doubles the rerank cost.
4. **Classify**: `likely` at or above `FIELDNOTES_MATCH_LIKELY`, `related` at or above
   `FIELDNOTES_MATCH_RELATED`. A candidate below both is dropped, and so is any candidate whose
   score trails the best by more than `FIELDNOTES_MATCH_GAP`, so one strong match does not carry
   weak ones along.
5. **Select** the best `k`, highest score first: 3 for a post.

No LLM is involved. The expected answer to a novel post is no candidates at all, which is why the
cut is a threshold on the reranker's score rather than a fixed top 3: a cosine similarity only
ranks, while the cross-encoder scores each pair as an absolute estimate of sameness.

## Variants

- `/match` with `rerank: false` stops after step 2 and returns the candidates in cosine order,
  unclassified, with no threshold.
- `/neighbors` runs steps 1–3 and 5 but skips the cut in step 4, leaves the observation itself out,
  and leaves `match_class` null for a neighbour below both thresholds.

## Settings

| Variable | Default |
| --- | --- |
| `FIELDNOTES_MATCH_COSINE_TOP` | 8 |
| `FIELDNOTES_MATCH_BM25_TOP` | 4 |
| `FIELDNOTES_MATCH_LIKELY` | 0.995 |
| `FIELDNOTES_MATCH_RELATED` | 0.985 |
| `FIELDNOTES_MATCH_GAP` | 0.3 |
| `FIELDNOTES_MATCH_BOTH_DIRECTIONS` | false |
| `FIELDNOTES_EMBED_MODEL` | `BAAI/bge-base-en-v1.5` |

Startup refuses thresholds that do not satisfy 0 ≤ related ≤ likely ≤ 1, a cosine top below 1, and a
negative BM25 top or gap.

The three threshold defaults (`FIELDNOTES_MATCH_LIKELY`, `FIELDNOTES_MATCH_RELATED`,
`FIELDNOTES_MATCH_GAP`) were read at gate 1 from the eval harness's replay of the mined dataset
(`eval/replay.py`, `eval/run.py`): `related` is the lowest rerank score at which at most one novel
post in ten is answered with a candidate, and `likely` the score at which answers are right as often
as wrong. The reranker scores texts on one topic close to 1 whether or not they make the same point,
so both thresholds sit high, and at these values the gap drops nothing. Match quality is measured by
that eval, not by the unit tests, whose fake models score by word overlap.
