"""Compare candidate scorers for `post`, embedding models among them, in a simulated replay of the
dataset. What it measures: `fieldnotes_eval.bench`; `eval/embed_local.py` embeds a model that the
models pod does not serve.

    cexec python uv run --all-packages python eval/bench.py \
        --dataset ../FieldnotesAppSpecs/dataset --work .run/bench MODEL... > .run/bench/report.md

The unlabeled pairs it can list name the dataset's rows, which is private: write them outside this
repo's history.
"""

import sys

from fieldnotes_eval.bench import main

if __name__ == "__main__":
    sys.exit(main())
