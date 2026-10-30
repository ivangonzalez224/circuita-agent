import json
from pathlib import Path

from circuita_agent.agent import AgentResult
from circuita_agent.cli import main
from circuita_agent.viewer import build_record, load_record, render_viewer, sample_record


def test_sample_run_is_marked_simulated_and_ends_clean():
    record = sample_record()
    assert record["simulated"] is True
    assert record["result"]["success"] is True
    errors = [s["result"]["errors"] for s in record["result"]["steps"] if "errors" in s["result"]]
    assert errors == [2, 1, 0]


def test_render_embeds_the_run_and_cannot_break_out_of_the_script_tag():
    record = sample_record()
    record["result"]["task"] = "</script><script>alert(1)</script>"
    page = render_viewer(record)
    assert "__RUN_JSON__" not in page.split('id="run-data"')[1].split("</script>")[0]
    assert "</script><script>alert(1)" not in page
    data = page.split('id="run-data" type="application/json">')[1].split("</script>")[0]
    assert json.loads(data)["result"]["task"] == "</script><script>alert(1)</script>"


def test_build_record_wraps_the_result():
    record = build_record(AgentResult(mode="agent", task="t"), model="m", cost_usd=0.01)
    assert record["version"] == 1 and record["simulated"] is False
    assert record["result"]["task"] == "t"


def test_load_record_rejects_other_json(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]")
    try:
        load_record(bad)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_view_sample_writes_html(tmp_path: Path):
    out = tmp_path / "run.html"
    assert main(["view", "--sample", "--out", str(out), "--no-open"]) == 0
    assert "Simulated run" in out.read_text(encoding="utf-8")


def test_view_without_a_run_fails(tmp_path: Path):
    assert main(["view", "--out", str(tmp_path / "x.html"), "--no-open"]) == 2
