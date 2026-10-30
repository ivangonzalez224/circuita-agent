"""Command line: check the setup, run the agent on one request, or run the benchmark."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from circuita_agent.agent import AgentResult, format_steps, run_agent, run_single_shot
from circuita_agent.benchmark import MODES, format_table, load_cases, run_benchmark
from circuita_agent.circuita_client import CircuitaClient, CircuitaError
from circuita_agent.config import Settings
from circuita_agent.model import ChatCompletionsModel, ModelError, estimate_cost
from circuita_agent.tools import ToolRunner

DEFAULT_CASES = Path("benchmarks/circuits.json")


def _client(settings: Settings) -> CircuitaClient:
    return CircuitaClient(settings.circuita_url, settings.circuita_api_key)


def cmd_check(settings: Settings) -> int:
    """Check the configuration. Calls circuita, never the model, so it costs nothing."""
    print(f"circuita:  {settings.circuita_url}")
    print(f"model:     {settings.model_name} @ {settings.model_base_url}")
    print(f"MODEL_API_KEY set: {'yes' if settings.model_api_key else 'NO'}")
    try:
        client = _client(settings)
        client.health()
        parts = client.parts()["known_parts"]
    except CircuitaError as exc:
        print(f"circuita unreachable or rejected the request: {exc.detail}")
        return 1
    print(f"circuita OK, {len(parts)} known parts")
    return 0


def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    client = _client(settings)
    runner = ToolRunner(client, Path(args.export_dir))
    try:
        model = ChatCompletionsModel(settings)
        if args.mode == "single":
            result = run_single_shot(args.prompt, model, runner)
        else:
            result = run_agent(args.prompt, model, runner, max_turns=args.max_turns)
    except ModelError as exc:
        print(f"model error: {exc}", file=sys.stderr)
        return 2
    _print_result(result, settings, as_json=args.json)
    return 0 if result.success else 1


def _print_result(result: AgentResult, settings: Settings, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return
    print(format_steps(result))
    cost = estimate_cost(settings.model_name, result.prompt_tokens, result.completion_tokens)
    cost_text = f", ~${cost:.4f}" if cost is not None else ""
    status = "OK" if result.success else f"NOT OK ({result.stopped})"
    print(
        f"\n{status}: design {result.design_id}, errors={result.errors}, "
        f"warnings={result.warnings}, turns={result.turns}{cost_text}"
    )
    if result.error_message:
        print(f"error: {result.error_message}")
    if result.final_text:
        print(f"\n{result.final_text}")


def cmd_benchmark(settings: Settings, args: argparse.Namespace) -> int:
    cases = load_cases(Path(args.cases))[: args.limit]
    modes = tuple(m for m in args.modes.split(",") if m in MODES)
    try:
        model = ChatCompletionsModel(settings)
    except ModelError as exc:
        print(f"model error: {exc}", file=sys.stderr)
        return 2
    report = run_benchmark(
        cases,
        model,
        _client(settings),
        model_name=settings.model_name,
        modes=modes,
        max_cost_usd=args.max_cost_usd,
        max_turns=args.max_turns,
        progress=print,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print("\n" + format_table(report))
    if report["stopped_by_budget"]:
        print("Stopped early: the estimated cost reached --max-cost-usd.")
    print(f"Full results: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="circuita-agent", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="check configuration and circuita (no model call)")
    run = sub.add_parser("run", help="design a circuit from a description")
    run.add_argument("prompt")
    run.add_argument("--mode", choices=["agent", "single"], default="agent")
    run.add_argument("--max-turns", type=int, default=8)
    run.add_argument("--export-dir", default="out")
    run.add_argument("--json", action="store_true")
    bench = sub.add_parser("benchmark", help="compare single-shot against the repair loop")
    bench.add_argument("--cases", default=str(DEFAULT_CASES))
    bench.add_argument("--limit", type=int, default=100)
    bench.add_argument("--modes", default="single,agent")
    bench.add_argument("--max-turns", type=int, default=8)
    bench.add_argument("--max-cost-usd", type=float, default=1.0)
    bench.add_argument("--out", default="benchmarks/results/latest.json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    if args.command == "check":
        return cmd_check(settings)
    if args.command == "run":
        return cmd_run(settings, args)
    return cmd_benchmark(settings, args)
