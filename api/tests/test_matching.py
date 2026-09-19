"""The match pipeline (design, "Match pipeline"; FR-1..FR-3) over the fake models, whose rerank
score is the word overlap of the two texts: a test sets a score by choosing the words."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fieldnotes_api.document import new_document
from fieldnotes_api.index import EmbeddingCache, Index, observation_path
from fieldnotes_api.matching import Matcher, MatchSettings, candidate, reaction_counts
from fieldnotes_api.testing import FakeModels
from fieldnotes_contracts import MatchClass, Reaction

AT = datetime(2026, 9, 19, 10, 0, 0, tzinfo=UTC)
SETTINGS = MatchSettings(
    cosine_top=8, bm25_top=4, likely=0.6, related=0.3, gap=0.25, both_directions=False
)


def ulid(n: int) -> str:
    return f"01K5H8ZQ3V6D9W2X4Y7B1C{n:04d}"


class Store:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "checkout"
        self.models = FakeModels()
        self.index = Index(self.root, EmbeddingCache(tmp_path / "cache", "m"), self.models)
        self.paths: set[str] = set()

    def add(self, n: int, text: str, area: str = "a", **fields) -> None:
        reaction = Reaction(at=AT, emoji="📝", repo="pvginkel/Example", text=text)
        document = new_document(
            id_=ulid(n), area=area, category="hint", text=text, reaction=reaction
        )
        if fields:
            document = document.with_fields(**fields)
        path = observation_path(ulid(n))
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_text(document.text)
        self.paths.add(path)

    async def matcher(self, settings: MatchSettings = SETTINGS) -> Matcher:
        await self.index.update(self.paths)
        self.models.reranked.clear()
        return Matcher(self.index, self.models, settings)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def ids(scored) -> list[str]:
    return [s.entry.observation.id for s in scored]


async def test_a_novel_post_gets_nothing_back(store):
    store.add(1, "dashboards show the browser timezone")
    store.add(2, "probe timeout defaults to one second")
    matcher = await store.matcher()

    assert await matcher.match("a: uv sync installs no workspace members", 3) == []


async def test_a_duplicate_comes_back_likely(store):
    store.add(1, "uv sync installs no workspace members")
    store.add(2, "dashboards show the browser timezone")
    matcher = await store.matcher()

    [match] = await matcher.match("a: uv sync installs no workspace members", 3)

    assert match.entry.observation.id == ulid(1)
    assert match.score == pytest.approx(1.0)
    assert match.match_class is MatchClass.likely


async def test_classes_follow_the_thresholds(store):
    # Against the query's seven words, the area `a` included: 4 of 7 shared is 0.57 (related), 2
    # of 7 is 0.29 (dropped).
    store.add(1, "uv sync installs")
    store.add(2, "uv")
    matcher = await store.matcher(replace(SETTINGS, gap=1.0))

    matches = await matcher.match("a: uv sync installs no workspace members", 3)

    assert ids(matches) == [ulid(1)]
    assert matches[0].match_class is MatchClass.related


async def test_the_gap_drops_what_trails_the_best(store):
    store.add(1, "uv sync installs no workspace members")  # 7/7 = 1.0
    store.add(2, "uv sync installs no workspace")  # 6/7 = 0.86, within the gap
    store.add(3, "uv sync installs no")  # 5/7 = 0.71: likely, but trails by more than 0.25
    matcher = await store.matcher()

    matches = await matcher.match("a: uv sync installs no workspace members", 3)

    assert ids(matches) == [ulid(1), ulid(2)]
    assert [m.match_class for m in matches] == [MatchClass.likely, MatchClass.likely]


async def test_at_most_k_come_back(store):
    for n in range(5):
        store.add(n, "uv sync installs no workspace members")
    matcher = await store.matcher()

    assert len(await matcher.match("a: uv sync installs no workspace members", 3)) == 3


async def test_closed_observations_match_too(store):
    # FR-3.
    store.add(1, "uv sync installs no workspace members", status="closed", outcome="done")
    matcher = await store.matcher()

    [match] = await matcher.match("a: uv sync installs no workspace members", 3)

    assert match.entry.observation.status == "closed"


async def test_the_candidates_are_the_cosine_top_union_the_bm25_top(store):
    for n in range(30):
        store.add(n, f"filler observation number {n} about nothing in particular")
    store.add(99, "track_build.py rc=3 on a jenkins backlog")
    matcher = await store.matcher(replace(SETTINGS, cosine_top=2, bm25_top=1))

    await matcher.match("a: filler observation mentioning track_build.py", 3)

    [(_, texts)] = store.models.reranked
    assert len(texts) == 3
    assert "a: track_build.py rc=3 on a jenkins backlog" in texts


async def test_the_default_candidate_stage_reranks_at_most_twelve(store):
    for n in range(40):
        store.add(n, f"uv sync observation {n}")
    matcher = await store.matcher()

    await matcher.match("a: uv sync observation", 3)

    [(_, texts)] = store.models.reranked
    assert len(texts) <= 12


async def test_both_directions_average_the_two_scores(store):
    store.add(1, "one")
    matcher = await store.matcher(replace(SETTINGS, both_directions=True))
    query, other = "a: query", "a: one"
    store.models.scores = {(query, other): 0.9, (other, query): 0.5}

    [match] = await matcher.match(query, 3)

    assert match.score == pytest.approx(0.7)
    assert store.models.reranked == [(query, [other]), (other, [query])]


async def test_without_reranking_the_candidates_come_in_cosine_order(store):
    store.add(1, "uv sync installs no workspace members")
    store.add(2, "grafana dashboards")
    matcher = await store.matcher()

    matches = await matcher.match("a: uv sync installs no workspace members", 2, rerank=False)

    assert ids(matches) == [ulid(1), ulid(2)]
    assert [(m.score, m.match_class) for m in matches] == [(None, None), (None, None)]
    assert store.models.reranked == []


async def test_neighbours_keep_what_falls_below_the_thresholds(store):
    store.add(1, "uv sync installs no workspace members")
    store.add(2, "uv sync installs")
    store.add(3, "grafana dashboards")
    matcher = await store.matcher()
    text = store.index.get(ulid(1)).text

    matches = await matcher.match(text, 5, thresholds=False, exclude=ulid(1))

    assert ids(matches) == [ulid(2), ulid(3)]
    assert matches[0].match_class is MatchClass.related
    assert matches[1].match_class is None


async def test_an_empty_store_matches_nothing_and_calls_no_model(store):
    matcher = await store.matcher()
    assert await matcher.match("a: anything", 3) == []
    assert store.models.embedded == []


async def test_a_candidate_carries_what_fr_2_names(store):
    store.add(1, "uv sync installs no workspace members", status="closed", outcome="done")
    store.add(
        1,
        "uv sync installs no workspace members",
        status="closed",
        outcome="done",
        pointer="pvginkel/Example#4",
    )
    matcher = await store.matcher()
    [match] = await matcher.match("a: uv sync installs no workspace members", 3)

    wire = candidate(match)

    assert wire.id == ulid(1)
    assert wire.canonical == "uv sync installs no workspace members"
    assert (wire.status, wire.outcome, wire.pointer) == ("closed", "done", "pvginkel/Example#4")
    assert wire.reactions == ["📝 (1)"]
    assert wire.match_class is MatchClass.likely
    assert wire.score == 1.0
    assert wire.next_step.startswith(f'react(id="{ulid(1)}"')
    assert "force=true" in wire.next_step


async def test_reaction_counts_put_the_most_frequent_first(store):
    store.add(1, "text")
    matcher = await store.matcher()
    entry = matcher.index.get(ulid(1))
    reactions = [
        Reaction(at=AT, emoji=emoji, repo="r") for emoji in ["📝", "👎", "👍", "👍", "👎", "👍"]
    ]
    entry = replace(
        entry, observation=entry.observation.model_copy(update={"reactions": reactions})
    )

    assert reaction_counts(entry) == ["👍 (3)", "👎 (2)", "📝 (1)"]
