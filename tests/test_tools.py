from pathlib import Path

from circuita_agent.tools import ToolRunner, parse_arguments
from tests.fakes import FakeCircuita

LED_CIRCUIT = {
    "components": [
        {"reference": "D1", "category": "led", "value": "red"},
        {"reference": "R1", "category": "resistor", "value": "220ohm"},
    ],
    "nets": [
        {
            "name": "R1_D1",
            "pins": [{"reference": "R1", "pin": "2"}, {"reference": "D1", "pin": "A"}],
        },
        {"name": "GND", "pins": [{"reference": "D1", "pin": "K"}]},
    ],
}


def test_lookup_without_query_is_a_compact_index():
    runner = ToolRunner(FakeCircuita().client())
    result = runner.run("lookup_parts", {})
    assert result["known_parts"] == [
        {"category": "module", "value": "esp32", "mpn": "ESP32-WROOM-32"}
    ]
    assert "led" in result["generic_categories"] and "pins" not in result["known_parts"][0]


def test_lookup_with_query_returns_real_pins_and_ignores_punctuation():
    runner = ToolRunner(FakeCircuita().client())
    assert runner.run("lookup_parts", {"query": "led"})["generic_pin_templates"]["led"] == [
        "1:K",
        "2:A",
    ]
    esp = runner.run("lookup_parts", {"query": "ESP-32"})["known_parts"][0]
    assert esp["pins"][3] == "4:GPIO25"
    assert "error" in runner.run("lookup_parts", {"query": "warp-drive"})


def test_catalog_is_fetched_once():
    fake = FakeCircuita()
    runner = ToolRunner(fake.client())
    runner.run("lookup_parts", {})
    runner.run("lookup_parts", {"query": "led"})
    assert [r for r in fake.requests if r[1] == "/parts"] == [("GET", "/parts")]


def test_propose_returns_readable_pins_and_counts_errors():
    runner = ToolRunner(FakeCircuita().client())
    result = runner.run("propose_circuit", LED_CIRCUIT)
    assert result["errors"] == 1 and result["warnings"] == 0
    assert {"name": "GND", "connections": ["D1.K"]} in result["nets"]
    assert result["issues"][0]["code"] == "NET_TOO_SMALL"
    assert runner.design is result


def test_backend_rejection_comes_back_as_an_error_not_an_exception():
    runner = ToolRunner(FakeCircuita().client())
    bad = {
        "components": LED_CIRCUIT["components"],
        "nets": [{"name": "X", "pins": [{"reference": "U9", "pin": "1"}]}],
    }
    result = runner.run("propose_circuit", bad)
    assert "nonexistent pin U9.1" in result["error"]
    assert runner.design is None


def test_unknown_tool_and_bad_arguments():
    runner = ToolRunner(FakeCircuita().client())
    assert "unknown tool" in runner.run("make_coffee", {})["error"]
    assert "bad arguments" in runner.run("propose_circuit", {"components": []})["error"]


def test_export_writes_a_file_and_rejects_path_tricks(tmp_path: Path):
    runner = ToolRunner(FakeCircuita().client(), tmp_path)
    design = runner.run("propose_circuit", LED_CIRCUIT)
    result = runner.run("export_kicad", {"design_id": design["design_id"]})
    assert (tmp_path / f"{design['design_id']}.kicad_sch").read_text().startswith("(kicad_sch")
    assert result["bytes"] > 0
    assert runner.run("export_kicad", {"design_id": "../../etc/passwd"}) == {
        "error": "invalid design_id"
    }


def test_parse_arguments():
    assert parse_arguments("") == {}
    assert parse_arguments('{"a": 1}') == {"a": 1}
    for bad in ("[1]", "{oops"):
        try:
            parse_arguments(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
