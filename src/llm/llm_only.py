"""LLM-only baseline (evaluation.md section 4): the model reads the raw program, no parser.

The model must find the decisions, write conditions with COBOL field names,
operators and values, actions and line ranges itself. Output is constrained to
a flat JSON schema (one level of all/any per rule), then converted to the
canonical Rule schema and scored like any other system.
"""

from __future__ import annotations

import json
from typing import Any

from src.common.models import Rule
from src.llm.client import OllamaClient

SYSTEM = (
    "You extract business decision rules from COBOL programs. Read the program and return ONLY JSON: "
    '{"rules": [{"title": str, "intent": str, "line_start": int, "line_end": int, '
    '"logic": "all" | "any", "conditions": [{"field": COBOL-NAME, "op": one of == != < <= > >= in between, '
    '"value": number | string | list}], "actions": [{"type": "set" | "compute", "target": COBOL-NAME, '
    '"value": number | string | null, "expr": string | null}], "else_actions": [same as actions]}]}. '
    "One rule per decision branch. Use exact COBOL field names, operators and literal values from the code. "
    "Line numbers are the numbers shown at the start of each line."
)

SCHEMA = {
    "type": "object",
    "properties": {"rules": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "title": {"type": "string"}, "intent": {"type": "string"},
            "line_start": {"type": "integer"}, "line_end": {"type": "integer"},
            "logic": {"type": "string", "enum": ["all", "any"]},
            "conditions": {"type": "array", "items": {"type": "object", "properties": {
                "field": {"type": "string"}, "op": {"type": "string"}, "value": {}},
                "required": ["field", "op", "value"]}},
            "actions": {"type": "array", "items": {"type": "object", "properties": {
                "type": {"type": "string"}, "target": {"type": "string"}, "value": {}, "expr": {}},
                "required": ["type", "target"]}},
            "else_actions": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["title", "intent", "line_start", "line_end", "logic", "conditions", "actions"]}}},
    "required": ["rules"],
}
OPS = {"==", "!=", "<", "<=", ">", ">=", "in", "not_in", "between"}
OP_ALIASES = {"=": "==", "<>": "!=", "equal": "==", "not equal": "!=", "eq": "==", "ne": "!=", "lt": "<",
              "le": "<=", "gt": ">", "ge": ">="}


def _actions(items: list[Any]) -> list[dict[str, Any]]:
    out = []
    for a in items or []:
        if not isinstance(a, dict) or not a.get("target"):
            continue
        t = a.get("type") if a.get("type") in ("set", "compute") else ("compute" if a.get("expr") else "set")
        b = {"type": t, "target": str(a["target"]).lower().replace("-", "_"), "source_name": str(a["target"]).upper()}
        if t == "compute" and a.get("expr"):
            b["expr"] = str(a["expr"])
        elif a.get("expr") and a.get("value") is None:
            b["expr"] = str(a["expr"])
        elif isinstance(a.get("value"), (int, float, str)):
            b["value"] = a["value"]
        out.append(b)
    return out


def to_rules(program: str, program_file: str, payload: dict[str, Any]) -> list[Rule]:
    rules: list[Rule] = []
    for k, r in enumerate(payload.get("rules", []), start=1):
        try:
            leaves = []
            for c in r.get("conditions") or []:
                op = OP_ALIASES.get(str(c.get("op", "")).strip().lower(), str(c.get("op", "")).strip())
                if op not in OPS or not c.get("field"):
                    continue
                leaves.append({"field": str(c["field"]).lower().replace("-", "_"),
                               "source_name": str(c["field"]).upper(), "op": op, "value": c.get("value")})
            logic = r.get("logic") if r.get("logic") in ("all", "any") else "all"
            rules.append(Rule.model_validate({
                "rule_id": f"{program}-L{k:03d}", "title": str(r.get("title", "")), "intent": str(r.get("intent", "")),
                "conditions": {logic: leaves},
                "actions": _actions(r.get("actions")), "else_actions": _actions(r.get("else_actions")),
                "trace": {"program": program_file, "line_start": int(r.get("line_start") or 0),
                          "line_end": int(r.get("line_end") or 0)},
                "provenance": {"extractor": "llm-only"},
            }))
        except Exception:
            continue                     # an unusable rule is simply a miss
    return rules


def extract_llm_only(program: str, program_file: str, source: str, client: OllamaClient) -> tuple[list[Rule], bool]:
    numbered = "\n".join(f"{n:>4} {line}" for n, line in enumerate(source.splitlines(), start=1))
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"PROGRAM {program_file}:\n{numbered}"}]
    try:
        content = client.chat(msgs, schema=SCHEMA)
        return to_rules(program, program_file, json.loads(content)), True
    except Exception:
        return [], False
