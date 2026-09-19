"""The eval over the invented dataset and its replay, with the fake models: every section of the
report is there, and the fake's shared-word cosines separate the invented labels."""

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
    settings = {"embed_model": "fake", "lexical_weight": 0.0, "related": 0.4, "gap": 0.5}
    summary = {"settings": settings, **summarize(records)}
    (replay / "summary.json").write_text(json.dumps(summary))
    models = FakeModels()

    report = await evaluate(dataset, tmp_path / "work", replay, lambda: (models, _close))

    for heading in ("## Labeled pairs", "### Distributions", "## Replay", "### Misses"):
        assert heading in report
    assert "### Sweep on `cosine`" in report and "### Sweep on `score`" not in report
    # The cosine scores the same pair r1-r2 above everything and r3-r5 below both related pairs:
    # half the (same, related) pairs in order, every (same, unrelated) one.
    assert "| cosine | 0.500 | 1.000 | 0.750 |" in report
    # At 0.6 the sweep still finds r2's mate (0.90) and no longer answers r4 (0.52).
    assert "| 0.600 | 1/2, 0/3 (0.000) |" in report
    # The miss is r5, whose mate r3 shares too few words with it.
    assert "**r5**" in report and "mate **r3**" in report
    # The false alarm is r4 against r1, a pair labeled related.
    assert "**r4** against **r1**" in report and "labeled related" in report

    # A second run finds every vector cached and calls the models for nothing.
    calls = len(models.embedded)
    again = await evaluate(dataset, tmp_path / "work", replay, lambda: (models, _close))
    assert len(models.embedded) == calls and again == report


async def test_a_lexical_weight_adds_the_pipelines_score(tmp_path, dataset):
    models = FakeModels()

    report = await evaluate(
        dataset, tmp_path / "work", None, lambda: (models, _close), lexical_weight=1.0
    )

    assert "| lexical |" in report and "| score |" in report
    assert "## Replay" not in report
