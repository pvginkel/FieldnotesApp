import pytest

from fieldnotes_mcp.config import SettingsError, load_settings

TOKENS = {"FIELDNOTES_API_TOKEN": "api-token", "FIELDNOTES_MCP_TOKEN": "mcp-token"}


def test_defaults():
    settings = load_settings(TOKENS)

    assert settings.api_url == "http://localhost:8080"
    assert settings.api_token == "api-token"
    assert settings.mcp_token == "mcp-token"
    assert (settings.host, settings.port) == ("0.0.0.0", 8081)
    assert settings.log_level == "INFO"


def test_overrides():
    settings = load_settings(
        {
            **TOKENS,
            "FIELDNOTES_API_URL": "http://api:9000",
            "FIELDNOTES_MCP_HOST": "127.0.0.1",
            "FIELDNOTES_MCP_PORT": "9001",
            "FIELDNOTES_LOG_LEVEL": "debug",
        }
    )

    assert settings.api_url == "http://api:9000"
    assert (settings.host, settings.port) == ("127.0.0.1", 9001)
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize("name", sorted(TOKENS))
@pytest.mark.parametrize("value", [None, "", "  "])
def test_both_tokens_are_required(name, value):
    environ = {key: token for key, token in TOKENS.items() if key != name}
    if value is not None:
        environ[name] = value

    with pytest.raises(SettingsError, match=name):
        load_settings(environ)


def test_a_malformed_port_names_its_variable():
    with pytest.raises(SettingsError, match="FIELDNOTES_MCP_PORT"):
        load_settings({**TOKENS, "FIELDNOTES_MCP_PORT": "eighty"})
