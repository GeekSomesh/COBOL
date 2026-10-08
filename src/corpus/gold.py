"""Gold rules from a spec: the answer key, in the canonical Rule schema.

Conditions come from the spec's business-level logic (not from parsing the
generated code). Paths are flattened with the same structural convention as the
slicer (:func:`src.analysis.slicer.combine`), which docs/parser_subset.md defines.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.analysis.slicer import combine, negate
from src.common.models import Rule
from src.corpus.render import Span


def _names(spec: dict[str, Any]) -> dict[str, str]:
    return {f["business"]: f["name"] for f in spec["inputs"] + spec["outputs"]}


def to_schema_cond(c: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    if "all" in c:
        return {"all": [to_schema_cond(x, names) for x in c["all"]]}
    if "any" in c:
        return {"any": [to_schema_cond(x, names) for x in c["any"]]}
    if "not" in c:
        return {"not": to_schema_cond(c["not"], names)}
    return {"field": c["field"], "source_name": names[c["field"]], "op": c["op"], "value": c["value"]}


def to_schema_actions(acts: list[dict[str, Any]], names: dict[str, str], render_lit) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for a in acts:
        if "set" in a:
            base = {"type": "set", "target": a["set"], "source_name": names[a["set"]]}
            out.append(base | ({"expr": names[a["from"]]} if a.get("from") else {"value": a["value"]}))
        elif "compute" in a:
            expr = _expr(a["expr"], names)
            out.append({"type": "compute", "target": a["compute"], "source_name": names[a["compute"]], "expr": expr})
        elif "add" in a:
            tgt = names[a["add"]]
            out.append({"type": "compute", "target": a["add"], "source_name": tgt,
                        "expr": f"{tgt} + {render_lit(a['add'], a['value'])}"})
        elif "call" in a:
            out.append({"type": "call", "target": a["call"].lower(), "source_name": a["call"]})
    return out


def _expr(text: str, names: dict[str, str]) -> str:
    return re.sub(r"[a-z][a-z0-9_]*", lambda m: names.get(m.group(0), m.group(0)), text)


def _fields_in(c: Optional[dict[str, Any]]) -> set[str]:
    if c is None:
        return set()
    if "field" in c:
        return {c["field"]}
    out: set[str] = set()
    for k in ("all", "any"):
        for x in c.get(k, []):
            out |= _fields_in(x)
    if "not" in c:
        out |= _fields_in(c["not"])
    return out


def gold_rules(spec: dict[str, Any], spans: list[Span], render_lit) -> list[Rule]:
    names = _names(spec)
    inputs = {f["business"] for f in spec["inputs"]}
    rules: list[Rule] = []
    for n, span in enumerate(spans, start=1):
        block = spec["blocks"][span.block]
        else_actions: list[dict[str, Any]] = []
        if span.rule is None:                                  # nested outer ELSE
            rl = block["outer_else"]
            path = [negate(to_schema_cond(block["outer"], names))]
        else:
            rl = block["rules"][span.rule]
            if block["structure"] == "if":
                path = [to_schema_cond(rl["when"], names)]
                else_actions = to_schema_actions(rl.get("else", []), names, render_lit)
            else:
                earlier = [to_schema_cond(x["when"], names) for x in block["rules"][:span.rule] if x["when"] is not None]
                path = [negate(c) for c in earlier]
                if rl["when"] is not None:
                    path.append(to_schema_cond(rl["when"], names))
                if block["structure"] == "nested":
                    path = [to_schema_cond(block["outer"], names)] + path
        acts = rl["then"]
        used = set()
        for c in path:
            used |= _fields_in(c)
        for a in acts + rl.get("else", []):
            if a.get("from"):
                used.add(a["from"])
            if a.get("expr"):
                used |= set(re.findall(r"[a-z][a-z0-9_]*", a["expr"])) & set(names)
        copybooks = [spec["copybook"]] if spec["copybook"] and used & inputs else []
        rules.append(Rule.model_validate({
            "rule_id": f"{spec['program']}-R{n:03d}",
            "title": rl["title"],
            "intent": rl["intent"],
            "domain": spec["domain"],
            "conditions": combine(path),
            "actions": to_schema_actions(acts, names, render_lit),
            "else_actions": else_actions,
            "trace": {"program": spec["program_file"], "paragraph": span.paragraph,
                      "line_start": span.start, "line_end": span.end, "copybooks": copybooks},
            "concepts": rl["concepts"],
            "external_dependency": any("call" in a for a in acts),
            "status": "approved",
            "provenance": {"source": "gold:generator", "template": spec["template"],
                           "spec": f"data/specs/{spec['program']}.yaml", "seed": spec["seed"]},
        }))
    return rules
