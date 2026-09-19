"""The eval over the invented dataset and its replay, with the fake models: every section of the
report is there, and the fake's word-overlap scores separate the invented labels."""

import json

from fieldnotes_api.testing import FakeModels
from fieldnotes_eval.replay import summarize
from fieldnotes_eval.run import evaluate


async def _close() -> None:
    return None


async def test_the_report_covers_the_pairs_and_the_replay(tmp_path, dataset, records):
    replay = tmp_path / "replay"
    replay.mkdir()
    (replay / "replay.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    summary = {"settings": {"related": 0.3, "gap": 0.25}, **summarize(records)}
    (replay / "summary.json").write_text(json.dumps(summary))
    models = FakeModels()

    report = await evaluate(dataset, tmp_path / "work", replay, lambda: (models, _close))

    for heading in ("## Labeled pairs", "### Distributions", "## Replay", "### Misses"):
        assert heading in report
    assert "### Sweep on `forward`" in report and "### Sweep on `cosine`" in report
    # Word overlap scores the same pair r1-r2 above everything and r3-r5 below both related
    # pairs: half the (same, related) pairs in order, every (same, unrelated) one.
    assert "| forward | 0.500 | 1.000 | 0.750 |" in report
    # The miss is r5, whose mate r3 shares no word with it.
    assert "**r5**" in report and "mate **r3**" in report
    # The false alarm is r4 against r1, a pair labeled related.
    assert "**r4** against **r1**" in report and "labeled related" in report

    # A second run finds every score cached and calls the models for nothing.
    calls = len(models.reranked)
    again = await evaluate(dataset, tmp_path / "work", replay, lambda: (models, _close))
    assert len(models.reranked) == calls and again == report
