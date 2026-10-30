"""Benchmark: the same circuits solved in one shot (no feedback) and with the repair loop.

Success means the design compiled and the validator found zero errors, and every category
listed in `expect_categories` is present (so an empty design cannot pass).
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from circuita_agent.agent import AgentResult, run_agent, run_single_shot
from circuita_agent.circuita_client import CircuitaClient
from circuita_agent.model import Model, estimate_cost
from circuita_agent.tools import ToolRunner

MODES = ("single", "agent")


@dataclass
class Case:
    id: str
    prompt: str
    expect_categories: list[str]


def load_cases(path: Path) -> list[Case]:
    return [
        Case(c["id"], c["prompt"], c.get("expect_categories", []))
        for c in json.loads(path.read_text())
    ]


def has_expected_categories(runner: ToolRunner, case: Case) -> bool:
    if runner.design is None:
        return False
    present = {c["category"] for c in runner.design["components"]}
    return set(case.expect_categories) <= present


def run_benchmark(
    cases: list[Case],
    model: Model,
    client: CircuitaClient,
    *,
    model_name: str,
    modes: tuple[str, ...] = MODES,
    max_cost_usd: float = 1.0,
    max_turns: int = 8,
    progress: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    """Run every case in every mode. Stops early when the estimated spend reaches the cap."""
    rows: list[dict[str, Any]] = []
    spent = 0.0
    stopped_by_budget = False
    for case in cases:
        for mode in modes:
            if spent >= max_cost_usd:
                stopped_by_budget = True
                break
            runner = ToolRunner(client)
            outcome: AgentResult
            if mode == "single":
                outcome = run_single_shot(case.prompt, model, runner)
            else:
                outcome = run_agent(case.prompt, model, runner, max_turns=max_turns)
            outcome.success = outcome.success and has_expected_categories(runner, case)
            spent += (
                estimate_cost(model_name, outcome.prompt_tokens, outcome.completion_tokens) or 0.0
            )
            rows.append({"case": case.id, "mode": mode, **outcome.to_dict()})
            progress(f"{case.id:<24} {mode:<7} {'PASS' if outcome.success else 'fail'}")
        if stopped_by_budget:
            break
    return {
        "model": model_name,
        "estimated_cost_usd": round(spent, 4),
        "stopped_by_budget": stopped_by_budget,
        "summary": summarize(rows),
        "rows": rows,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for mode in MODES:
        subset = [r for r in rows if r["mode"] == mode]
        if not subset:
            continue
        passed = sum(1 for r in subset if r["success"])
        summary[mode] = {
            "cases": len(subset),
            "passed": passed,
            "pass_rate": round(passed / len(subset), 3),
            "avg_turns": round(sum(r["turns"] for r in subset) / len(subset), 2),
        }
    return summary


def format_table(report: dict[str, Any]) -> str:
    lines = ["| mode | passed | pass rate | avg turns |", "|---|---|---|---|"]
    for mode, s in report["summary"].items():
        lines.append(
            f"| {mode} | {s['passed']}/{s['cases']} | {s['pass_rate']:.0%} | {s['avg_turns']} |"
        )
    lines.append(f"\nEstimated cost: ${report['estimated_cost_usd']}")
    return "\n".join(lines)
