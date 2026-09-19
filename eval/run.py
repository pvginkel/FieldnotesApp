"""Score the labeled pairs, and a replay across thresholds: gate 1's eval (design, "Validation").
What it measures: `fieldnotes_eval.run`.

    cexec python uv run --all-packages python eval/run.py --dataset ../FieldnotesAppSpecs/dataset \
        --replay .run/replay --work .run/eval > .run/eval/report.md

The report quotes the dataset, which is private: write it outside this repo's history.
"""

import sys

from fieldnotes_eval.run import main

if __name__ == "__main__":
    sys.exit(main())
