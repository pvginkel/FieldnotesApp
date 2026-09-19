import pytest

from fieldnotes_mcp.testing import FakeApi


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()
