"""The models client against a stand-in for the pod's HTTP surface, and the suites' fake."""

import json

import httpx
import numpy as np
import pytest

from fieldnotes_api.models import HttpModels, ModelsError
from fieldnotes_api.testing import FakeModels


class Pod:
    """The pod's `/embed` and `/rerank`, as TEI answers them."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append((request.url.path, body))
        if self.status != 200:
            return httpx.Response(self.status, text="Model is overloaded")
        if request.url.path == "/embed":
            return httpx.Response(200, json=[[float(len(text)), 1.0] for text in body["inputs"]])
        scores = [len(text) / 100 for text in body["texts"]]
        ranked = sorted(enumerate(scores), key=lambda item: -item[1])
        return httpx.Response(200, json=[{"index": i, "score": s} for i, s in ranked])


def client(pod) -> HttpModels:
    transport = httpx.MockTransport(pod)
    return HttpModels(httpx.AsyncClient(transport=transport, base_url="http://models"))


async def test_embedding_goes_in_chunks_of_32_and_comes_back_normalised():
    pod = Pod()
    texts = [f"text {i}" for i in range(70)]

    matrix = await client(pod).embed(texts)

    assert [len(body["inputs"]) for _, body in pod.requests] == [32, 32, 6]
    assert matrix.shape == (70, 2)
    assert matrix.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(matrix, axis=1), 1.0, rtol=1e-6)


async def test_rerank_scores_come_back_in_the_texts_order():
    pod = Pod()

    scores = await client(pod).rerank("query", ["a", "ccc", "bb"])

    assert pod.requests == [("/rerank", {"query": "query", "texts": ["a", "ccc", "bb"]})]
    assert scores == [0.01, 0.03, 0.02]


async def test_rerank_goes_in_chunks_of_64():
    pod = Pod()
    scores = await client(pod).rerank("q", ["x" * i for i in range(100)])
    assert [len(body["texts"]) for _, body in pod.requests] == [64, 36]
    assert scores == [i / 100 for i in range(100)]


async def test_a_refusal_is_a_models_error():
    with pytest.raises(ModelsError, match="/embed answered 503"):
        await client(Pod(status=503)).embed(["a"])


async def test_an_unreachable_pod_is_a_models_error():
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ModelsError, match="/rerank could not be reached"):
        await client(refuse).rerank("q", ["a"])


async def test_the_fake_is_deterministic_and_scores_by_shared_words():
    fake = FakeModels(scores={("q", "pinned"): 0.42})
    first, again = (
        await fake.embed(["uv sync all packages"]),
        await fake.embed(["uv sync all packages"]),
    )
    np.testing.assert_array_equal(first, again)
    close, far = await fake.embed(["uv sync packages", "grafana timezone"])
    assert float(first[0] @ close) > float(first[0] @ far)
    assert await fake.rerank("a b c d", ["a b", "x y", "pinned"]) == [0.5, 0.0, 0.0]
    assert await fake.rerank("q", ["pinned"]) == [0.42]
    fake.fail = True
    with pytest.raises(ModelsError):
        await fake.embed(["a"])
