"""The client addresses the API relative to its base URL, which carries the surface's /api."""

import httpx

from fieldnotes_contracts import PostRequest
from fieldnotes_mcp.api_client import ApiClient
from fieldnotes_mcp.config import DEFAULT_API_URL


async def test_the_paths_join_the_base_urls_api_prefix():
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(
            201, json={"id": "01K5H8ZQ3V6D9W2X4Y7B1C0E5F", "candidates": []}
        )

    client = ApiClient(DEFAULT_API_URL, "token", transport=httpx.MockTransport(answer))
    request = PostRequest(
        area="uv", category="hint", text="text", repo="pvginkel/Example"
    )
    await client.post(request)
    await client.aclose()

    assert seen == ["/api/observations"]
