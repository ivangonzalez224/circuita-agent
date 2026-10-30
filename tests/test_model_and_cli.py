import json
from pathlib import Path

import httpx
import pytest
from openai import OpenAI

from circuita_agent import cli
from circuita_agent.config import Settings
from circuita_agent.model import ChatCompletionsModel, ModelError, estimate_cost
from tests.fakes import FakeCircuita, PolicyModel

SETTINGS = Settings("key", "http://meta.test/v1", "muse-spark-1.3", "low", "http://c.test", None)


def _openai(handler):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAI(api_key="key", base_url="http://meta.test/v1", http_client=http, max_retries=0)


def test_model_sends_the_expected_request_and_parses_tool_calls_and_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup_parts", "arguments": '{"query": "led"}'},
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 1,
                "model": "muse-spark-1.3",
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
            },
        )

    turn = ChatCompletionsModel(SETTINGS, _openai(handler)).complete(
        [{"role": "user", "content": "hi"}], [{"type": "function", "function": {"name": "t"}}]
    )
    assert seen["url"] == "http://meta.test/v1/chat/completions" and seen["auth"] == "Bearer key"
    assert seen["body"]["model"] == "muse-spark-1.3" and seen["body"]["reasoning_effort"] == "low"
    assert seen["body"]["tools"][0]["function"]["name"] == "t"
    assert (
        turn.tool_calls[0].name == "lookup_parts"
        and turn.tool_calls[0].arguments == '{"query": "led"}'
    )
    assert (turn.prompt_tokens, turn.completion_tokens) == (120, 30)


def test_provider_errors_become_model_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "overloaded"}})

    with pytest.raises(ModelError, match="overloaded"):
        ChatCompletionsModel(SETTINGS, _openai(handler)).complete([], [])


def test_missing_key_is_explained():
    no_key = Settings(None, "http://meta.test/v1", "muse-spark-1.3", "low", "http://c.test", None)
    with pytest.raises(ModelError, match="MODEL_API_KEY"):
        ChatCompletionsModel(no_key)


def test_cost_estimate_uses_listed_prices():
    assert estimate_cost("muse-spark-1.3", 1_000_000, 1_000_000) == pytest.approx(5.5)
    assert estimate_cost("something-else", 10, 10) is None


@pytest.fixture
def patched_cli(monkeypatch: pytest.MonkeyPatch):
    fake = FakeCircuita()
    monkeypatch.setattr(cli, "_client", lambda settings: fake.client())
    monkeypatch.setattr(cli, "ChatCompletionsModel", lambda settings: PolicyModel())
    monkeypatch.chdir(Path(__file__).parent.parent)
    return fake


def test_cli_check_does_not_need_a_model_key(patched_cli, capsys):
    assert cli.main(["check"]) == 0
    assert "1 known parts" in capsys.readouterr().out


def test_cli_run_prints_the_repair_trace(patched_cli, capsys, tmp_path):
    assert cli.main(["run", "an LED", "--export-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "propose_circuit -> 2 errors" in out and "edit_circuit -> 0 errors" in out
    assert out.count("OK:") == 1


def test_cli_single_mode_fails_without_repair(patched_cli, capsys):
    assert cli.main(["run", "an LED", "--mode", "single"]) == 1
    assert "NOT OK" in capsys.readouterr().out


def test_cli_benchmark_writes_a_report(patched_cli, capsys, tmp_path):
    out = tmp_path / "r.json"
    assert cli.main(["benchmark", "--limit", "2", "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["summary"]["single"]["cases"] == 2 and report["rows"]
    assert "| mode |" in capsys.readouterr().out


def test_cli_run_save_writes_a_run_the_viewer_can_open(patched_cli, tmp_path):
    saved = tmp_path / "runs" / "run.json"
    code = cli.main(["run", "an LED", "--export-dir", str(tmp_path), "--save", str(saved)])
    assert code == 0
    record = json.loads(saved.read_text())
    assert record["simulated"] is False and record["result"]["success"] is True
    html = tmp_path / "run.html"
    assert cli.main(["view", str(saved), "--out", str(html), "--no-open"]) == 0
    assert "an LED" in html.read_text(encoding="utf-8")
