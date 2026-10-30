"""Test doubles: a fake circuita API (httpx transport) and a scripted model. No network."""

import json
from collections.abc import Callable
from typing import Any

import httpx

from circuita_agent.circuita_client import CircuitaClient
from circuita_agent.model import ModelTurn, ToolCall


def _pins(*names: str) -> list[dict[str, str]]:
    return [
        {"number": str(i), "name": n, "electrical_type": "passive"} for i, n in enumerate(names, 1)
    ]


CATALOG: dict[str, Any] = {
    "known_parts": [
        {
            "category": "module",
            "value": "esp32",
            "mpn": "ESP32-WROOM-32",
            "manufacturer": "Espressif",
            "pins": _pins("EN", "3V3", "GND", "GPIO25"),
            "required_pins": [],
            "optional_pin_prefixes": ["GPIO"],
        }
    ],
    "generic_pin_templates": {
        "led": _pins("K", "A"),
        "resistor": _pins("1", "2"),
        "battery": _pins("+", "-"),
    },
}


class FakeCircuita:
    """Mimics the parts of the circuita API the agent uses, with one validation rule:
    a net with fewer than two pins is an error (NET_TOO_SMALL)."""

    def __init__(self) -> None:
        self.designs: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str]] = []
        self.headers: list[httpx.Headers] = []

    # -- helpers ---------------------------------------------------------------------------
    def _components(self, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for spec in specs:
            ref, cat, value = spec["reference"], spec["category"], spec.get("value")
            known = next(
                (p for p in CATALOG["known_parts"] if (p["category"], p["value"]) == (cat, value)),
                None,
            )
            pins = (
                known["pins"]
                if known
                else CATALOG["generic_pin_templates"].get(cat)
                or _pins(*[str(i) for i in range(1, (spec.get("pin_count") or 2) + 1)])
            )
            out.append(
                {
                    "id": ref.lower(),
                    "reference": ref,
                    "category": cat,
                    "value": value,
                    "mpn": known["mpn"] if known else None,
                    "pins": [{**p, "id": f"{ref.lower()}.{p['number']}"} for p in pins],
                }
            )
        return out

    def _pin_id(self, comps: list[dict[str, Any]], ref: str, pin: str) -> str:
        for comp in comps:
            if comp["reference"] == ref:
                for p in comp["pins"]:
                    if pin in (p["number"], p["name"]):
                        return str(p["id"])
        raise KeyError(f"Net references nonexistent pin {ref}.{pin}")

    def _response(self, design_id: str) -> dict[str, Any]:
        design = self.designs[design_id]
        issues = [
            {
                "severity": "error",
                "code": "NET_TOO_SMALL",
                "message": f"Net '{n['name']}' connects only one pin",
                "component_ids": [],
                "net_ids": [n["id"]],
            }
            for n in design["nets"]
            if len(n["connections"]) < 2
        ]
        return {
            "design_id": design_id,
            "canvas": {"components": design["components"], "nets": design["nets"]},
            "validation_issues": issues,
        }

    # -- the HTTP surface ------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        self.headers.append(request.headers)
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/parts":
            return httpx.Response(200, json=CATALOG)
        body = json.loads(request.content) if request.content else {}
        if path == "/proposals/apply":
            synth = body["synthesis"]
            comps = self._components(synth["components"])
            try:
                nets = [
                    {
                        "id": f"net_{i}",
                        "name": n.get("name"),
                        "connections": [
                            self._pin_id(comps, p["reference"], p["pin"]) for p in n["pins"]
                        ],
                    }
                    for i, n in enumerate(synth["nets"])
                ]
            except KeyError as exc:
                return httpx.Response(422, json={"detail": exc.args[0]})
            design_id = f"design-{len(self.designs) + 1:04d}"
            self.designs[design_id] = {"components": comps, "nets": nets}
            return httpx.Response(200, json=self._response(design_id))
        if path.endswith("/proposals/apply"):
            design_id = path.split("/")[2]
            if design_id not in self.designs:
                return httpx.Response(404, json={"detail": f"Design '{design_id}' not found"})
            design = self.designs[design_id]
            for op in body["operations"]:
                if op["op"] == "connect_pins":
                    ids = [
                        self._pin_id(design["components"], p["reference"], p["pin"])
                        for p in op["pins"]
                    ]
                    existing = next(
                        (n for n in design["nets"] if n["name"] == op.get("net_name")), None
                    )
                    if existing:
                        existing["connections"] = sorted(set(existing["connections"] + ids))
                    else:
                        design["nets"].append(
                            {
                                "id": f"net_{len(design['nets'])}",
                                "name": op.get("net_name"),
                                "connections": ids,
                            }
                        )
            return httpx.Response(200, json=self._response(design_id))
        if path.endswith("/export/kicad"):
            return httpx.Response(200, text="(kicad_sch (version 20211123))")
        return httpx.Response(404, json={"detail": "not found"})

    def client(self, api_key: str | None = None) -> CircuitaClient:
        return CircuitaClient(
            "http://circuita.test", api_key, transport=httpx.MockTransport(self.handle)
        )


def call(name: str, arguments: dict[str, Any], call_id: str = "c1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


class ScriptedModel:
    """Returns the prepared turns in order and records every request it receives."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self._turns = list(turns)
        self.requests: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        self.requests.append((json.loads(json.dumps(messages)), tools))
        return self._turns.pop(0)


class PolicyModel:
    """Proposes a circuit with a one-pin net first; repairs it when it sees the error."""

    def __init__(self, tokens: tuple[int, int] = (1000, 500)) -> None:
        self.tokens = tokens
        self.calls = 0

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        self.calls += 1
        p, c = self.tokens
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        if not tool_msgs:
            bad = {
                "components": [
                    {"reference": "D1", "category": "led", "value": "red"},
                    {"reference": "R1", "category": "resistor", "value": "220ohm"},
                ],
                "nets": [
                    {"name": "R1_D1", "pins": [{"reference": "R1", "pin": "2"}]},
                    {"name": "GND", "pins": [{"reference": "D1", "pin": "K"}]},
                ],
            }
            return ModelTurn(None, [call("propose_circuit", bad)], p, c)
        last = json.loads(tool_msgs[-1]["content"])
        if last.get("errors", 0) > 0:
            fix = {
                "design_id": last["design_id"],
                "operations": [
                    {
                        "op": "connect_pins",
                        "net_name": "R1_D1",
                        "pins": [{"reference": "D1", "pin": "A"}],
                    },
                    {
                        "op": "connect_pins",
                        "net_name": "GND",
                        "pins": [{"reference": "R1", "pin": "1"}],
                    },
                ],
            }
            return ModelTurn(None, [call("edit_circuit", fix, "c2")], p, c)
        return ModelTurn("Done: LED with resistor.", [], p, c)


Handler = Callable[[httpx.Request], httpx.Response]
