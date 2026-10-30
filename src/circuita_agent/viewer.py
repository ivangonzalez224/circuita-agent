"""Turn a saved agent run into a single self-contained HTML page that replays it."""

import json
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from circuita_agent.agent import AgentResult

PLACEHOLDER = "__RUN_JSON__"


def build_record(
    result: AgentResult, *, model: str, cost_usd: float | None, simulated: bool = False
) -> dict[str, Any]:
    """The file written by `run --save` and read by the viewer."""
    return {
        "version": 1,
        "simulated": simulated,
        "model": model,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "cost_usd": cost_usd,
        "result": result.to_dict(),
    }


def sample_record() -> dict[str, Any]:
    text = resources.files("circuita_agent").joinpath("sample_run.json").read_text("utf-8")
    record: dict[str, Any] = json.loads(text)
    return record


def render_viewer(record: dict[str, Any]) -> str:
    """Embed the run in the page. `<` is escaped so the data cannot close the script tag."""
    template = resources.files("circuita_agent").joinpath("viewer.html").read_text("utf-8")
    data = json.dumps(record, ensure_ascii=False).replace("<", "\\u003c")
    marker = f'<script id="run-data" type="application/json">{PLACEHOLDER}</script>'
    return template.replace(
        marker, f'<script id="run-data" type="application/json">{data}</script>'
    )


def load_record(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text("utf-8"))
    if not isinstance(loaded, dict) or "result" not in loaded and "steps" not in loaded:
        raise ValueError(f"{path} is not a saved run")
    return loaded
