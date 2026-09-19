"""The scorer bench over the invented dataset, with the fake models' vectors in its cache: what
each post is answered, the threshold a false-alarm rate buys, and the report."""

import json

import pytest

from fieldnotes_api.index import EmbeddingCache
from fieldnotes_api.testing import FakeModels
from fieldnotes_eval.bench import Answers, bench
from fieldnotes_eval.dataset import load_clusters, load_rows
from fieldnotes_eval.scoring import ScoringError, cosine_matrix, embed_missing, lexical_matrix


@pytest.fixture
async def answers(tmp_path, dataset):
    rows = load_rows(dataset)
    cache = EmbeddingCache(tmp_path / "work" / "cache", "fake")
    assert await embed_missing(rows, cache, FakeModels()) == 5
    assert await embed_missing(rows, cache, FakeModels()) == 0
    membership = load_clusters(dataset)
    return Answers(
        cosine_matrix(rows, cache),
        [membership.get(row.id) for row in rows],
        [row.date for row in rows],
    )


async def test_each_post_is_answered_from_the_rows_before_it(answers):
    # r2 and r5 have a cluster-mate before them; r2's is from its own date.
    assert answers.duplicates == {
        1: pytest.approx(0.897, abs=1e-3),
        4: pytest.approx(0.316, abs=1e-3),
    }
    assert [answers.cross(i) for i in answers.duplicates] == [False, True]
    # The first post meets an empty store; r4's best is r1.
    assert answers.novel[0] == float("-inf")
    assert answers.novel[3] == pytest.approx(0.519, abs=1e-3)
    assert answers.top[3][0] == 0


async def test_a_false_alarm_rate_buys_a_threshold(answers):
    # No novel post answered: just above r4's 0.5189, which still finds r2's mate.
    strict = answers.threshold(0.0)
    assert answers.novel[3] < strict < answers.novel[3] + 1e-6
    assert answers.found(strict) == {1}
    # One novel post in three: down to just above r3's best, and r5's mate is found too.
    loose = answers.threshold(0.34)
    assert answers.found(loose) == {1, 4}
    assert (3, 0) in answers.returned(loose) and (3, 0) not in answers.returned(strict)
    # With no threshold a post that met an empty store is still not answered.
    assert answers.found(float("-inf")) == {1, 4}


def test_the_lexical_overlap_reads_only_the_rows_before(dataset):
    lexical = lexical_matrix(load_rows(dataset))
    assert lexical[1, 0] > 0.5 and lexical[0, 1] == 0.0
    assert lexical[4, 2] > lexical[4, 0] == 0.0


async def test_the_report_compares_the_scorers(tmp_path, dataset):
    work = tmp_path / "work"
    await embed_missing(load_rows(dataset), EmbeddingCache(work / "cache", "fake"), FakeModels())
    unlabeled = tmp_path / "unlabeled.jsonl"

    report = bench(dataset, work, ["fake"], unlabeled)

    assert "| fake | cosine | 0.500 | 1.000 | 0.750 | 2/2, 1/1 |" in report
    assert "| fake | cosine + 0.5·lexical |" in report and "|  | lexical |" in report
    # r4 against r1 is labeled, and every other returned pair is a cluster-mate.
    assert [json.loads(line) for line in unlabeled.read_text().splitlines()] == []

    with pytest.raises(ScoringError, match="lacks 5 of 5 vectors"):
        bench(dataset, work, ["absent"])
