"""Replay the mined dataset through the API into an empty store: gate 1's replay (design,
"Validation"). What it records and how the reporter is simulated: `fieldnotes_eval.replay`.

    cexec python uv run --all-packages python eval/replay.py \
        --dataset ../FieldnotesAppSpecs/dataset --out .run/replay

`--out` must not hold a store yet. It is left holding the store (`remote.git`, gate 2's input),
the API's checkout, the embedding cache unless `--cache` puts it elsewhere, `replay.jsonl` (one
line per post) and `summary.json`. The dataset is private: keep `--out` out of this repo's history
(`.run/` is ignored).
"""

import sys

from fieldnotes_eval.replay import main

if __name__ == "__main__":
    sys.exit(main())
