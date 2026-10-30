import json

from circuita_agent.agent import format_steps, run_agent, run_single_shot
from circuita_agent.model import ModelError, ModelTurn, ToolCall
from circuita_agent.tools import ToolRunner
from tests.fakes import FakeCircuita, PolicyModel, ScriptedModel, call
from tests.test_tools import LED_CIRCUIT


def test_agent_repairs_a_design_using_validator_feedback():
    runner = ToolRunner(FakeCircuita().client())
    result = run_agent("an LED with a resistor", PolicyModel(), runner)
    assert result.success and result.stopped == "completed"
    assert [s.tool for s in result.steps] == ["propose_circuit", "edit_circuit"]
    assert result.steps[0].result["errors"] == 2 and result.errors == 0
    assert result.turns == 3 and result.final_text.startswith("Done")
    assert "NET_TOO_SMALL" in format_steps(result)


def test_tool_results_are_replayed_to_the_model_with_matching_ids():
    model = ScriptedModel(
        [
            ModelTurn(None, [call("propose_circuit", LED_CIRCUIT, "abc")]),
            ModelTurn("stop"),
            ModelTurn("stop again"),
        ]
    )
    run_agent("x", model, ToolRunner(FakeCircuita().client()), max_nudges=0)
    second_request = model.requests[1][0]
    assistant = next(m for m in second_request if m["role"] == "assistant")
    tool = next(m for m in second_request if m["role"] == "tool")
    assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"] == "abc"
    assert second_request[0]["role"] == "developer"


def test_single_shot_gets_no_feedback_and_makes_one_model_call():
    model = PolicyModel()
    runner = ToolRunner(FakeCircuita().client())
    result = run_single_shot("an LED with a resistor", model, runner)
    assert model.calls == 1 and result.turns == 1
    assert not result.success and result.errors == 2


def test_single_shot_only_offers_the_propose_tool():
    model = ScriptedModel([ModelTurn("no tools used")])
    result = run_single_shot("x", model, ToolRunner(FakeCircuita().client()))
    assert [t["function"]["name"] for t in model.requests[0][1]] == ["propose_circuit"]
    assert result.stopped == "no_design" and not result.success


def test_agent_is_nudged_once_when_it_stops_with_errors():
    model = ScriptedModel(
        [
            ModelTurn(None, [call("propose_circuit", LED_CIRCUIT)]),
            ModelTurn("I am done."),  # but errors remain
            ModelTurn("Still done."),
        ]
    )
    result = run_agent("x", model, ToolRunner(FakeCircuita().client()))
    nudge = model.requests[2][0][-1]
    assert nudge["role"] == "user" and "NET_TOO_SMALL" in nudge["content"]
    assert not result.success and result.final_text == "Still done."


def test_agent_stops_at_max_turns():
    turns = [ModelTurn(None, [call("lookup_parts", {}, f"c{i}")]) for i in range(5)]
    result = run_agent("x", ScriptedModel(turns), ToolRunner(FakeCircuita().client()), max_turns=3)
    assert result.stopped == "max_turns" and result.turns == 3 and not result.success


def test_invalid_json_arguments_are_reported_to_the_model_not_raised():
    model = ScriptedModel(
        [ModelTurn(None, [ToolCall("c1", "propose_circuit", "{oops")]), ModelTurn("ok")]
    )
    result = run_agent("x", model, ToolRunner(FakeCircuita().client()), max_nudges=0)
    tool_message = next(m for m in model.requests[1][0] if m["role"] == "tool")
    assert "not valid JSON" in json.loads(tool_message["content"])["error"]
    assert result.stopped == "no_design"


def test_model_failure_is_reported_in_the_result():
    class Broken:
        def complete(self, messages, tools):
            raise ModelError("HTTP 503")

    result = run_agent("x", Broken(), ToolRunner(FakeCircuita().client()))
    assert result.stopped == "model_error" and "503" in (result.error_message or "")
    assert (
        run_single_shot("x", Broken(), ToolRunner(FakeCircuita().client())).stopped == "model_error"
    )
