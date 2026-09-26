# Matching and the index

This covers the in-memory index the API keeps over the store, the embedding cache, the lexical
overlap, the models pod, and the pipeline that turns a query into ranked candidates.

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

## The lexical overlap

Okapi BM25, k1 = 1.2, b = 0.75, over the same embedded texts. Terms are lowercase runs of
`[a-z0-9]`, joined across `.`, `_`, `/` and `-` into one token (`track_build.py`, `appear-timeout`
from `--appear-timeout`), each such token indexed whole and by its parts as well, so an identifier,
path or error string in a post finds the observation that quotes it. A short list of English stop
words is left out.

A BM25 score grows with the query's length, so a threshold cannot read it. The overlap divides it
out: an observation's BM25 score for the query, over the score the query's own terms would earn as a
document. It says how much of the query the observation covers, about 0–1, and exactly 1 for the
query's own text. A term no observation holds counts in full against every overlap. Only
observations sharing a term with the query have one; the rest are 0.

## The models pod

NGINX in front of a Text Embeddings Inference container, `BAAI/bge-base-en-v1.5` at `/embed`, at
`FIELDNOTES_MODELS_URL`. Embedding goes in chunks of 32 texts; vectors come back L2-normalised so a
dot product is the cosine. The client allows 60 s a call.

## The pipeline

For a query text (a post's `area: text`, `/match`'s text, or an observation's own embedded text for
`/neighbors`; see [rest-api.md](rest-api.md)):

1. Embed the query: the pipeline's one model call.
2. **Score** every observation, over every status, closed ones included (FR-3): the cosine of the
   two vectors, plus the lexical overlap times `FIELDNOTES_MATCH_LEXICAL_WEIGHT`. With the weight
   at 0 the score is the cosine, and the lexical index is not asked. The store is scored
   whole, a matrix product and a walk over the query's postings, so there is no candidate stage.
3. **Classify**: `likely` at or above `FIELDNOTES_MATCH_LIKELY`, `related` at or above
   `FIELDNOTES_MATCH_RELATED`. An observation below both is dropped, and so is any whose score
   trails the best by more than `FIELDNOTES_MATCH_GAP`, so one strong match does not carry weak ones
   along.
4. **Select** the best `k`, highest score first: 3 for a post.

No LLM is involved, and no reranker: gate 1 measured `BAAI/bge-reranker-base` scoring two
observations on one subject close to 1 whatever point each made, and the operator ruled it out
(design, "Match pipeline"). The expected answer to a novel post is no candidates at all, which is
why the cut is a threshold rather than a fixed top 3.

## Variants

- `/neighbors` runs steps 1, 2 and 4 but skips the cut in step 3, leaves the observation itself out,
  and leaves `match_class` null for a neighbour below both thresholds.

## Settings

| Variable | Default |
| --- | --- |
| `FIELDNOTES_MATCH_LIKELY` | 0.94 |
| `FIELDNOTES_MATCH_RELATED` | 0.85 |
| `FIELDNOTES_MATCH_GAP` | 0.05 |
| `FIELDNOTES_MATCH_LEXICAL_WEIGHT` | 0.25 |
| `FIELDNOTES_EMBED_MODEL` | `BAAI/bge-base-en-v1.5` |

Startup refuses a negative weight or gap, and thresholds that do not satisfy 0 ≤ related ≤ likely ≤
1 + the weight, the most a score can be.

**Why the weight is 0.25.** Gate 1 compared scorers at matched false-alarm rates on the mined
dataset (`eval/bench.py`). With the weight anywhere from 0.2 to 0.75 the score found more duplicates
than the cosine alone at every rate, and a replay at 0.25 found 43 of 59 where the cosine found 36,
at the same false alarms, losing none. Six embedding models differed by less than the dataset can
tell apart. The operator ruled 0.25, the low end of the plateau: the dataset's duplicates may share
more wording than real posts will, and the lower weight keeps the cosine leading.

**A threshold belongs to its scorer.** The three defaults are for the cosine of
`BAAI/bge-base-en-v1.5` plus 0.25 times the lexical overlap, read at gate 1 from the same eval
(`eval/bench.py`, confirmed by `eval/replay.py` and `eval/run.py`): `related` is the lowest score at
which at most one novel post in ten is answered with a candidate, `likely` the score from which
three answers in four are right, and the gap the widest that still trims an answer, which costs no
duplicate on the dataset. Another embedding model compresses its cosines differently, and a lexical weight adds to
every score, so changing either means reading new thresholds from the same eval. Match quality is
measured by that eval, not by the unit tests, whose fake models score by shared words.
