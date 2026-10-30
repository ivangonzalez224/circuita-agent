import httpx
import pytest

from circuita_agent.circuita_client import CircuitaClient, CircuitaError
from circuita_agent.config import Settings
from tests.fakes import FakeCircuita


def test_settings_defaults_and_precedence(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("# comment\nMODEL_API_KEY='from-file'\nMODEL_NAME=muse-spark-1.2\n")
    settings = Settings.from_env({"MODEL_NAME": "muse-spark-1.3"}, dotenv)
    assert settings.model_api_key == "from-file"
    assert settings.model_name == "muse-spark-1.3"  # real environment wins over the file
    assert settings.model_base_url == "https://api.meta.ai/v1"
    assert settings.circuita_url == "http://localhost:8000"
    assert settings.circuita_api_key is None


def test_settings_strip_trailing_slash(tmp_path):
    settings = Settings.from_env({"CIRCUITA_URL": "https://x.example/"}, tmp_path / "none")
    assert settings.circuita_url == "https://x.example"


def test_api_key_header_is_sent_only_when_configured():
    fake = FakeCircuita()
    fake.client("secret").health()
    fake.client(None).health()
    assert fake.headers[0]["x-api-key"] == "secret"
    assert "x-api-key" not in fake.headers[1]


def test_error_detail_is_extracted_from_strings_and_validation_lists():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a":
            return httpx.Response(422, json={"detail": "Net references nonexistent pin U1.X"})
        return httpx.Response(
            422, json={"detail": [{"loc": ["body", "synthesis"], "msg": "Field required"}]}
        )

    client = CircuitaClient("http://t", transport=httpx.MockTransport(handler))
    with pytest.raises(CircuitaError) as first:
        client._request("GET", "/a")
    assert first.value.status == 422 and "U1.X" in first.value.detail
    with pytest.raises(CircuitaError) as second:
        client._request("GET", "/b")
    assert second.value.detail == "body.synthesis: Field required"


def test_unreachable_server_becomes_circuita_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = CircuitaClient("http://t", transport=httpx.MockTransport(handler))
    with pytest.raises(CircuitaError) as excinfo:
        client.health()
    assert excinfo.value.status == 0
