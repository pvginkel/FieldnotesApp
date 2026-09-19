"""An invented dataset in the mined dataset's format, and the settings of an API over an empty
store for it. The fake models' cosine is the share of words two texts have in common, so each
row's wording sets what it matches under the thresholds below."""

import json
import subprocess

import pytest

from fieldnotes_api.config import load_settings
from fieldnotes_api.testing import FakeModels
from fieldnotes_eval.dataset import load_clusters, load_rows
from fieldnotes_eval.replay import TOKEN, replay

# In date order. r1 and r2 make the same point (c1) in nearly the same words (cosine 0.90), and so
# do r3 and r5 (c2), in words too different for the fake to see (0.32); r4 shares enough words
# with r1 to be returned for it (0.52), and is novel.
ROWS = [
    ("r1", "2026-01-01", "helm chart", "the chart renders locally but server side apply fails on "
     "a defaulted field"),
    ("r2", "2026-01-01", "helm chart", "the chart renders locally but server side apply fails "
     "because of a defaulted field"),
    ("r3", "2026-01-02", "grafana dashboard", "panels show times in the browser timezone"),
    ("r4", "2026-01-03", "helm chart", "the chart renders locally and the values file keeps a "
     "defaulted replica count"),
    ("r5", "2026-01-04", "grafana panels", "screenshots from other timezones disagree about alert "
     "times"),
]  # fmt: skip
CLUSTERS = {"c1": ["r1", "r2"], "c2": ["r3", "r5"]}
PAIRS = [
    ("r1", "r2", "same"),
    ("r3", "r5", "same"),
    ("r1", "r4", "related"),
    ("r2", "r4", "related"),
    ("r1", "r3", "unrelated"),
    ("r4", "r5", "unrelated"),
]


@pytest.fixture
def dataset(tmp_path):
    directory = tmp_path / "dataset"
    directory.mkdir()
    lines = [
        {"id": id_, "date": date, "repo": f"pvginkel/{id_}", "area": area, "category": "hint",
         "text": text, "source": {"kind": "close-out"}}
        for id_, date, area, text in ROWS
    ]  # fmt: skip
    (directory / "observations.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    clusters = [{"cluster": c, "theme": None, "members": m} for c, m in CLUSTERS.items()]
    (directory / "clusters.jsonl").write_text("".join(json.dumps(x) + "\n" for x in clusters))
    pairs = [{"a": a, "b": b, "label": label} for a, b, label in PAIRS]
    (directory / "pairs.jsonl").write_text("".join(json.dumps(x) + "\n" for x in pairs))
    return directory


@pytest.fixture
def settings(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--quiet", "--bare", "--initial-branch", "main", str(remote)], check=True
    )
    return load_settings(
        {
            "FIELDNOTES_STORE_URL": str(remote),
            "FIELDNOTES_STORE_DIR": str(tmp_path / "checkout"),
            "FIELDNOTES_CACHE_DIR": str(tmp_path / "cache"),
            "FIELDNOTES_CLIENT_TOKEN_REPLAY": TOKEN,
            "FIELDNOTES_MATCH_LIKELY": "0.8",
            "FIELDNOTES_MATCH_RELATED": "0.4",
            "FIELDNOTES_MATCH_GAP": "0.5",
            "FIELDNOTES_MATCH_LEXICAL_WEIGHT": "0",
        }
    )


@pytest.fixture
def records(dataset, settings):
    """The replay of the invented dataset, one record per row."""
    return replay(load_rows(dataset), load_clusters(dataset), settings, models=FakeModels())
