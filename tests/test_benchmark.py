from pathlib import Path

from circuita_agent.benchmark import Case, format_table, load_cases, run_benchmark
from tests.fakes import FakeCircuita, PolicyModel

CASES = [
    Case("led", "an LED", ["led", "resistor"]),
    Case("led-needs-a-battery", "an LED", ["led", "battery"]),  # repaired design lacks a battery
]


def test_loop_beats_single_shot_and_expected_categories_are_enforced():
    report = run_benchmark(
        CASES, PolicyModel(), FakeCircuita().client(), model_name="muse-spark-1.3"
    )
    single, agent = report["summary"]["single"], report["summary"]["agent"]
    assert single["passed"] == 0
    assert agent["passed"] == 1 and agent["cases"] == 2
    assert "| agent | 1/2 | 50% |" in format_table(report)


def test_benchmark_stops_when_the_estimated_cost_reaches_the_cap():
    report = run_benchmark(
        CASES * 5,
        PolicyModel((1_000_000, 1_000_000)),
        FakeCircuita().client(),
        model_name="muse-spark-1.3",
        max_cost_usd=1.0,
    )
    assert report["stopped_by_budget"] and len(report["rows"]) == 1


def test_unknown_model_price_costs_nothing_to_estimate():
    report = run_benchmark(CASES[:1], PolicyModel(), FakeCircuita().client(), model_name="other")
    assert report["estimated_cost_usd"] == 0 and not report["stopped_by_budget"]


def test_shipped_benchmark_cases_are_well_formed():
    cases = load_cases(Path(__file__).parent.parent / "benchmarks" / "circuits.json")
    assert len(cases) == 20 and len({c.id for c in cases}) == 20
    assert all(c.prompt and c.expect_categories for c in cases)
