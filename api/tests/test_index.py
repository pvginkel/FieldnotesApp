"""The index (design, "Index maintenance"): the embedding cache, the matrix, BM25, and the
listener's updates. The "Index rebuild" validation row is `test_a_deleted_cache_...`."""

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from fieldnotes_api.document import Document, new_document
from fieldnotes_api.index import Bm25, EmbeddingCache, Index, observation_path, terms
from fieldnotes_api.testing import FakeModels
from fieldnotes_contracts import Reaction

AT = datetime(2026, 9, 19, 10, 0, 0, tzinfo=UTC)


def ulid(n: int) -> str:
    return f"01K5H8ZQ3V6D9W2X4Y7B1C{n:04d}"


def write(root: Path, n: int, area: str, text: str, **fields) -> str:
    reaction = Reaction(at=AT, emoji="📝", repo="pvginkel/Example", text=text)
    document = new_document(id_=ulid(n), area=area, category="hint", text=text, reaction=reaction)
    if fields:
        document = document.with_fields(**fields)
    path = observation_path(ulid(n))
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(document.text)
    return path


@pytest.fixture
def root(tmp_path):
    return tmp_path / "checkout"


@pytest.fixture
def models():
    return FakeModels()


def index(root: Path, cache: Path, models) -> Index:
    return Index(root, EmbeddingCache(cache, "BAAI/bge-base-en-v1.5"), models)


async def test_an_index_takes_in_every_observation_of_the_checkout(root, tmp_path, models):
    paths = {
        write(root, 1, "uv workspace", "sync with --all-packages"),
        write(root, 2, "helm chart", "probe timeouts", card="FN-7"),
        "README.md",
        "triage/2026-09-19.md",
    }
    built = index(root, tmp_path / "cache", models)

    await built.update(paths)

    assert len(built) == 2
    assert built.get(ulid(1)).text == "uv workspace: sync with --all-packages"
    assert built.by_card("FN-7").observation.id == ulid(2)
    assert sorted(models.embedded) == [
        "helm chart: probe timeouts",
        "uv workspace: sync with --all-packages",
    ]
    cached = list((tmp_path / "cache" / "BAAI" / "bge-base-en-v1.5").iterdir())
    assert len(cached) == 2


async def test_a_deleted_cache_rebuilds_an_equal_index(root, tmp_path, models):
    paths = {write(root, n, "area", f"observation number {n}") for n in range(5)}
    first = index(root, tmp_path / "cache", models)
    await first.update(paths)
    shutil.rmtree(tmp_path / "cache")
    models.embedded.clear()

    second = index(root, tmp_path / "cache", models)
    await second.update(paths)

    assert len(models.embedded) == 5  # one vector per observation
    for n in range(5):
        np.testing.assert_array_equal(first.get(ulid(n)).vector, second.get(ulid(n)).vector)


async def test_a_warm_cache_embeds_nothing(root, tmp_path, models):
    paths = {write(root, n, "area", f"observation number {n}") for n in range(3)}
    await index(root, tmp_path / "cache", models).update(paths)
    models.embedded.clear()

    await index(root, tmp_path / "cache", models).update(paths)

    assert models.embedded == []


async def test_only_a_changed_canonical_is_embedded_again(root, tmp_path, models):
    paths = {write(root, n, "area", f"observation number {n}") for n in range(3)}
    built = index(root, tmp_path / "cache", models)
    await built.update(paths)
    models.embedded.clear()
    reacted = Document((root / observation_path(ulid(0))).read_text()).with_reaction(
        Reaction(at=AT, emoji="👍", repo="pvginkel/Other")
    )
    (root / observation_path(ulid(0))).write_text(reacted.text)
    write(root, 1, "area", "a rewritten canonical")

    await built.update({observation_path(ulid(0)), observation_path(ulid(1))})

    assert models.embedded == ["area: a rewritten canonical"]
    assert [r.emoji for r in built.get(ulid(0)).observation.reactions] == ["📝", "👍"]
    assert built.get(ulid(1)).observation.canonical == "a rewritten canonical"


async def test_a_removed_file_leaves_the_index(root, tmp_path, models):
    paths = {write(root, 1, "area", "gone soon", card="FN-1"), write(root, 2, "area", "stays")}
    built = index(root, tmp_path / "cache", models)
    await built.update(paths)
    (root / observation_path(ulid(1))).unlink()

    await built.update({observation_path(ulid(1))})

    assert built.get(ulid(1)) is None
    assert built.by_card("FN-1") is None
    assert built.bm25_top("gone soon", 5) == []
    assert [id_ for id_, _ in built.cosine_top(built.get(ulid(2)).vector, 5)] == [ulid(2)]


async def test_an_invalid_file_is_logged_and_left_out(root, tmp_path, models, caplog):
    good = write(root, 1, "area", "fine")
    bad = write(root, 2, "area", "broken")
    (root / bad).write_text((root / bad).read_text().replace("status: open", "status: lost"))
    renamed = observation_path(ulid(3))
    (root / renamed).write_text((root / good).read_text())
    built = index(root, tmp_path / "cache", models)

    with caplog.at_level(logging.ERROR):
        await built.update({good, bad, renamed})

    assert [entry.observation.id for entry in built.entries()] == [ulid(1)]
    assert f"{bad} is left out of the index" in caplog.text
    assert f"{renamed} is left out of the index: its id is {ulid(1)}" in caplog.text


async def test_a_failed_embedding_changes_nothing(root, tmp_path, models):
    built = index(root, tmp_path / "cache", models)
    await built.update({write(root, 1, "area", "first")})
    models.fail = True

    with pytest.raises(Exception, match="down"):
        await built.update({write(root, 2, "area", "second")})

    assert len(built) == 1


async def test_cosine_ranks_by_similarity_and_leaves_out_the_excluded(root, tmp_path, models):
    paths = {
        write(root, 1, "uv", "sync installs no workspace members"),
        write(root, 2, "uv", "sync installs members with all packages"),
        write(root, 3, "grafana", "dashboards show browser timezone"),
    }
    built = index(root, tmp_path / "cache", models)
    await built.update(paths)
    query = built.get(ulid(1)).vector

    ranked = built.cosine_top(query, 2)
    assert [id_ for id_, _ in ranked] == [ulid(1), ulid(2)]
    assert ranked[0][1] == pytest.approx(1.0)
    assert [id_ for id_, _ in built.cosine_top(query, 2, exclude=ulid(1))] == [ulid(2), ulid(3)]


def test_terms_keep_identifiers_whole_and_in_parts():
    assert terms("On rc=3, raise --appear-timeout in track_build.py") == [
        "rc",
        "3",
        "raise",
        "appear-timeout",
        "appear",
        "timeout",
        "track_build.py",
        "track",
        "build",
        "py",
    ]


def test_bm25_prefers_the_rare_identifier():
    bm25 = Bm25()
    bm25.put("a", "the build failed with rc=3 from track_build.py")
    bm25.put("b", "the build failed again")
    bm25.put("c", "the deploy failed")
    ranked = bm25.top("track_build.py exits 3", 5)
    assert [id_ for id_, _ in ranked] == ["a", "b"]
    # `build`, a part of the identifier, is in "b" too.
    assert [id_ for id_, _ in bm25.top("track_build.py", 5, exclude="a")] == ["b"]
    bm25.remove("a")
    assert [id_ for id_, _ in bm25.top("track_build.py", 5)] == ["b"]
