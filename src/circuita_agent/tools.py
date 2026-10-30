"""Tool definitions the model sees, and the runner that executes them against circuita."""

import json
import re
from pathlib import Path
from typing import Any

from circuita_agent.circuita_client import CircuitaClient, CircuitaError

CATEGORIES = [
    "resistor", "capacitor", "inductor", "diode", "led", "transistor", "mosfet", "bjt",
    "connector", "switch", "crystal", "fuse", "regulator", "ic", "sensor", "module",
    "battery", "motor", "speaker", "potentiometer",
]  # fmt: skip

_PIN_REF = {
    "type": "object",
    "properties": {
        "reference": {"type": "string", "description": "Component reference, e.g. U1, R1."},
        "pin": {"type": "string", "description": "Pin name (preferred) or number."},
    },
    "required": ["reference", "pin"],
}

_COMPONENT = {
    "type": "object",
    "properties": {
        "reference": {"type": "string", "description": "Unique reference such as U1, R1, D1."},
        "category": {"type": "string", "enum": CATEGORIES},
        "value": {"type": ["string", "null"], "description": "Value, e.g. 220ohm, esp32, red."},
        "pin_count": {"type": ["integer", "null"], "description": "Pins (see the system rules)."},
    },
    "required": ["reference", "category"],
}

_OPERATION = {
    "type": "object",
    "description": "One edit. Which fields apply depends on `op`.",
    "properties": {
        "op": {
            "type": "string",
            "enum": [
                "add_component",
                "remove_component",
                "set_value",
                "connect_pins",
                "disconnect_pins",
            ],
        },
        "reference": {
            "type": "string",
            "description": "For add_component, remove_component, set_value.",
        },
        "category": {"type": "string", "enum": CATEGORIES, "description": "For add_component."},
        "value": {"type": ["string", "null"], "description": "For add_component and set_value."},
        "pin_count": {"type": ["integer", "null"], "description": "For add_component."},
        "pins": {
            "type": "array",
            "items": _PIN_REF,
            "description": "For connect_pins and disconnect_pins.",
        },
        "net_name": {"type": ["string", "null"], "description": "For connect_pins."},
    },
    "required": ["op"],
}


def _function(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


LOOKUP_PARTS = _function(
    "lookup_parts",
    "List the parts circuita knows, or with `query` (a category or part name) get their exact "
    "value string, real pin numbers and names, and required pins.",
    {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "e.g. esp32, bme280, led."}},
    },
)
PROPOSE_CIRCUIT = _function(
    "propose_circuit",
    "Create a NEW circuit from components and nets. It is compiled, validated and saved; you "
    "get back a design_id, the resolved pins, the nets and the validation issues.",
    {
        "type": "object",
        "properties": {
            "components": {"type": "array", "items": _COMPONENT},
            "nets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": ["string", "null"], "description": "Descriptive name."},
                        "pins": {"type": "array", "items": _PIN_REF},
                    },
                    "required": ["pins"],
                },
            },
        },
        "required": ["components", "nets"],
    },
)
EDIT_CIRCUIT = _function(
    "edit_circuit",
    "Apply edit operations to an existing design. All operations apply together or none do. "
    "Returns the updated design and its validation issues.",
    {
        "type": "object",
        "properties": {
            "design_id": {"type": "string"},
            "operations": {"type": "array", "items": _OPERATION},
        },
        "required": ["design_id", "operations"],
    },
)
EXPORT_KICAD = _function(
    "export_kicad",
    "Export a design with no validation errors to a KiCad .kicad_sch file.",
    {
        "type": "object",
        "properties": {
            "design_id": {"type": "string"},
            "allow_unresolved": {
                "type": "boolean",
                "description": "Export parts without a real KiCad symbol as generic blocks.",
            },
        },
        "required": ["design_id"],
    },
)

TOOLS: list[dict[str, Any]] = [LOOKUP_PARTS, PROPOSE_CIRCUIT, EDIT_CIRCUIT, EXPORT_KICAD]
SINGLE_SHOT_TOOLS: list[dict[str, Any]] = [PROPOSE_CIRCUIT]

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_ISSUES = 20


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch not in " -_\t")


def _pin_labels(pins: list[dict[str, Any]]) -> list[str]:
    return [f"{p['number']}:{p.get('name') or p['number']}" for p in pins]


def summarize_design(response: dict[str, Any]) -> dict[str, Any]:
    """Compact view of a DesignResponse: what the model needs to check and fix a design."""
    canvas = response["canvas"]
    label: dict[str, str] = {}
    components = []
    for comp in canvas["components"]:
        names = [pin.get("name") for pin in comp["pins"] if pin.get("name")]
        for pin in comp["pins"]:
            name = pin.get("name")
            # A name shared by several pins (the GND pins of an ESP32) cannot identify one pin.
            ident = name if name and names.count(name) == 1 else pin["number"]
            label[pin["id"]] = f"{comp['reference']}.{ident}"
        components.append(
            {
                "reference": comp["reference"],
                "category": comp["category"],
                "value": comp.get("value"),
                "mpn": comp.get("mpn"),
                "pins": _pin_labels(comp["pins"]),
            }
        )
    nets = [
        {
            "name": net.get("name") or net["id"],
            "connections": [label.get(c, c) for c in net["connections"]],
        }
        for net in canvas["nets"]
    ]
    raw = sorted(response["validation_issues"], key=lambda i: i["severity"] != "error")
    issues = [{k: i[k] for k in ("severity", "code", "message")} for i in raw[:MAX_ISSUES]]
    errors = sum(1 for i in raw if i["severity"] == "error")
    return {
        "design_id": response["design_id"],
        "components": components,
        "nets": nets,
        "issues": issues,
        "errors": errors,
        "warnings": len(raw) - errors,
    }


class ToolRunner:
    """Executes tool calls. Never raises: failures come back as {"error": ...} for the model."""

    def __init__(self, client: CircuitaClient, output_dir: Path | None = None) -> None:
        self._client = client
        self._output_dir = output_dir or Path("out")
        self._catalog: dict[str, Any] | None = None
        self.design: dict[str, Any] | None = None  # summary of the latest design

    def run(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "lookup_parts": self._lookup_parts,
            "propose_circuit": self._propose_circuit,
            "edit_circuit": self._edit_circuit,
            "export_kicad": self._export_kicad,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"error": f"unknown tool {name!r}"}
        try:
            return handler(arguments)
        except CircuitaError as exc:
            return {"error": exc.detail}
        except (KeyError, TypeError, ValueError) as exc:
            return {"error": f"bad arguments: {exc!r}"}

    def _parts(self) -> dict[str, Any]:
        if self._catalog is None:
            self._catalog = self._client.parts()
        return self._catalog

    def _lookup_parts(self, args: dict[str, Any]) -> dict[str, Any]:
        data = self._parts()
        known = data.get("known_parts", [])
        generic = data.get("generic_pin_templates", {})
        query = _norm(str(args.get("query") or ""))
        if not query:
            return {
                "known_parts": [
                    {"category": p["category"], "value": p["value"], "mpn": p["mpn"]} for p in known
                ],
                "generic_categories": sorted(generic),
                "hint": "Call again with query=<name> to see the pins.",
            }
        matches = [
            {
                "category": p["category"],
                "value": p["value"],
                "mpn": p["mpn"],
                "pins": _pin_labels(p["pins"]),
                "required_pins": p["required_pins"],
                "optional_pin_prefixes": p["optional_pin_prefixes"],
            }
            for p in known
            if query in _norm(f"{p['category']} {p['value']} {p['mpn']}")
        ]
        templates = {c: _pin_labels(p) for c, p in generic.items() if query in _norm(c)}
        if not matches and not templates:
            return {"error": f"no part matches {query!r}; call lookup_parts with no query"}
        return {"known_parts": matches, "generic_pin_templates": templates}

    def _propose_circuit(self, args: dict[str, Any]) -> dict[str, Any]:
        response = self._client.apply_synthesis(list(args["components"]), list(args["nets"]))
        self.design = summarize_design(response)
        return self.design

    def _edit_circuit(self, args: dict[str, Any]) -> dict[str, Any]:
        design_id = str(args["design_id"])
        response = self._client.apply_edit(design_id, list(args["operations"]))
        self.design = summarize_design(response)
        return self.design

    def _export_kicad(self, args: dict[str, Any]) -> dict[str, Any]:
        design_id = str(args["design_id"])
        if not _SAFE_ID.match(design_id):
            return {"error": "invalid design_id"}
        text = self._client.export_kicad(design_id, bool(args.get("allow_unresolved", False)))
        self._output_dir.mkdir(parents=True, exist_ok=True)
        path = self._output_dir / f"{design_id}.kicad_sch"
        path.write_text(text)
        return {"exported": str(path), "bytes": len(text.encode())}


def parse_arguments(raw: str) -> dict[str, Any]:
    """Tool arguments arrive as a JSON string; a model can send invalid JSON."""
    value = json.loads(raw) if raw.strip() else {}
    if not isinstance(value, dict):
        raise ValueError("arguments must be a JSON object")
    return value
