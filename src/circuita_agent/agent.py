"""The agent loop: the model proposes a circuit, circuita validates it, the model repairs it."""

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from circuita_agent.model import Model, ModelError, ModelTurn
from circuita_agent.prompts import NO_DESIGN_NUDGE, NUDGE, SYSTEM_PROMPT
from circuita_agent.tools import SINGLE_SHOT_TOOLS, TOOLS, ToolRunner, parse_arguments


@dataclass
class Step:
    turn: int
    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass
class AgentResult:
    mode: str
    task: str
    success: bool = False
    stopped: str = "completed"  # completed | max_turns | no_design | model_error
    final_text: str = ""
    design_id: str | None = None
    errors: int | None = None
    warnings: int | None = None
    turns: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    steps: list[Step] = field(default_factory=list)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _problems(design: dict[str, Any]) -> str:
    return "\n".join(
        f"- {i['code']}: {i['message']}" for i in design["issues"] if i["severity"] == "error"
    )


def _assistant_message(turn: ModelTurn) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": turn.content or "",
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in turn.tool_calls
        ],
    }


def _finish(result: AgentResult, runner: ToolRunner) -> AgentResult:
    design = runner.design
    if design is not None:
        result.design_id = design["design_id"]
        result.errors = design["errors"]
        result.warnings = design["warnings"]
    result.success = design is not None and design["errors"] == 0
    return result


def _run_calls(
    turn: ModelTurn, runner: ToolRunner, result: AgentResult, messages: list[Any]
) -> None:
    for call in turn.tool_calls:
        try:
            arguments = parse_arguments(call.arguments)
            output = runner.run(call.name, arguments)
        except ValueError as exc:  # invalid JSON from the model
            arguments, output = {}, {"error": f"arguments are not valid JSON: {exc}"}
        result.steps.append(Step(result.turns, call.name, arguments, output))
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(output, ensure_ascii=False),
            }
        )


def run_agent(
    task: str, model: Model, runner: ToolRunner, *, max_turns: int = 8, max_nudges: int = 1
) -> AgentResult:
    """Loop until the design has no validation errors, the model stops, or turns run out."""
    result = AgentResult(mode="agent", task=task)
    messages: list[dict[str, Any]] = [
        {"role": "developer", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    nudges = 0
    while result.turns < max_turns:
        result.turns += 1
        try:
            turn = model.complete(messages, TOOLS)
        except ModelError as exc:
            result.stopped, result.error_message = "model_error", str(exc)
            break
        result.prompt_tokens += turn.prompt_tokens
        result.completion_tokens += turn.completion_tokens
        if turn.tool_calls:
            messages.append(_assistant_message(turn))
            _run_calls(turn, runner, result, messages)
            continue
        result.final_text = turn.content or ""
        design = runner.design
        unfinished = design is None or design["errors"] > 0
        if unfinished and nudges < max_nudges and result.turns < max_turns:
            nudges += 1
            messages.append({"role": "assistant", "content": result.final_text})
            text = NO_DESIGN_NUDGE if design is None else NUDGE.format(problems=_problems(design))
            messages.append({"role": "user", "content": text})
            continue
        break
    else:
        result.stopped = "max_turns"
    if result.stopped == "completed" and runner.design is None:
        result.stopped = "no_design"
    return _finish(result, runner)


def run_single_shot(task: str, model: Model, runner: ToolRunner) -> AgentResult:
    """Baseline for the benchmark: one model call, one circuit, no feedback from the validator."""
    result = AgentResult(mode="single", task=task, turns=1)
    messages: list[dict[str, Any]] = [
        {"role": "developer", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    try:
        turn = model.complete(messages, SINGLE_SHOT_TOOLS)
    except ModelError as exc:
        result.stopped, result.error_message = "model_error", str(exc)
        return _finish(result, runner)
    result.prompt_tokens, result.completion_tokens = turn.prompt_tokens, turn.completion_tokens
    result.final_text = turn.content or ""
    proposals = [c for c in turn.tool_calls if c.name == "propose_circuit"]
    if proposals:
        _run_calls(ModelTurn(None, proposals[:1]), runner, result, [])
    if runner.design is None:
        result.stopped = "no_design"
    return _finish(result, runner)


def format_steps(result: AgentResult) -> str:
    """Readable trace of what the agent did, for the terminal and the demo."""
    lines = []
    for step in result.steps:
        out = step.result
        if "error" in out:
            outcome = f"error: {out['error']}"
        elif "errors" in out:
            outcome = f"{out['errors']} errors, {out['warnings']} warnings"
            problems = [i for i in out["issues"] if i["severity"] == "error"]
            outcome += "".join(f"\n      ! {i['code']}: {i['message']}" for i in problems)
        elif "exported" in out:
            outcome = f"saved {out['exported']}"
        else:
            outcome = "ok"
        lines.append(f"  [{step.turn}] {step.tool} -> {outcome}")
    return "\n".join(lines)
