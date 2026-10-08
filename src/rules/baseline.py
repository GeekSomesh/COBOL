"""AST-only baseline: ``baseline_rule(slice) -> Rule`` (implementation.md section 4).

Everything structural (operators, literals, actions, trace) comes from the parser.
Business names are a mechanical rename of the COBOL name; intent and concepts
stay empty. This is the fallback whenever the LLM stage fails, and the
"AST-only" row in the evaluation table.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from src.analysis.analyze import PARSER_VERSION
from src.common.models import Rule, Slice


def business_name(source_name: str) -> str:
    """CUST-AGE -> cust_age; TBL-AMT(I) -> tbl_amt_i."""
    return re.sub(r"[^a-z0-9]+", "_", source_name.lower()).strip("_")


def _coerce(value: Any, entry: Optional[dict[str, Any]]) -> Any:
    """Type a literal by the field's PIC: numbers for numeric fields, text otherwise."""
    if isinstance(value, list):
        return [_coerce(v, entry) for v in value]
    if entry is None or value is None:
        return value
    numeric = entry.get("category") in ("numeric", "numeric-edited")
    if numeric:
        if isinstance(value, str):
            text = value.strip()
            if text == "":
                return 0
            try:
                num = float(text)
            except ValueError:
                return value               # e.g. HIGH-VALUES: keep as written
            return int(num) if num.is_integer() and "." not in text else num
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return value


def _field(slice_: Slice, name: str) -> Optional[dict[str, Any]]:
    return slice_.fields.get(name.split("(")[0].strip())


def _cond(c: dict[str, Any], slice_: Slice) -> dict[str, Any]:
    if "all" in c:
        return {"all": [_cond(x, slice_) for x in c["all"]]}
    if "any" in c:
        return {"any": [_cond(x, slice_) for x in c["any"]]}
    if "not" in c:
        return {"not": _cond(c["not"], slice_)}
    leaf: dict[str, Any] = {"field": business_name(c["source_name"]), "source_name": c["source_name"], "op": c["op"]}
    if "value" in c:
        leaf["value"] = _coerce(c["value"], _field(slice_, c["source_name"]))
    if c.get("ref"):
        leaf["ref"] = c["ref"]
    return leaf


def _actions(acts: list[dict[str, Any]], slice_: Slice) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for a in acts:
        b = {"type": a["type"], "target": business_name(a["source_name"]), "source_name": a["source_name"]}
        if "value" in a:
            b["value"] = _coerce(a["value"], _field(slice_, a["source_name"]))
        if a.get("expr"):
            b["expr"] = a["expr"]
        out.append(b)
    return out


def baseline_rule(slice_: Slice, source_hash: str = "") -> Rule:
    rule_id = re.sub(r"-S(\d+)$", r"-R\1", slice_.slice_id)
    return Rule.model_validate({
        "rule_id": rule_id,
        "conditions": _cond(slice_.condition, slice_),
        "actions": _actions(slice_.actions, slice_),
        "else_actions": _actions(slice_.else_actions, slice_),
        "trace": {
            "program": slice_.program_file,
            "file": slice_.source_file if slice_.source_file != slice_.program_file else None,
            "paragraph": slice_.paragraph,
            "line_start": slice_.line_start,
            "line_end": slice_.line_end,
            "copybooks": slice_.copybooks,
        },
        "external_dependency": slice_.external_dependency,
        "unsupported": slice_.unsupported,
        "status": "needs_review",
        "provenance": {
            "extractor": "ast-baseline",
            "parser_version": PARSER_VERSION,
            "slice_id": slice_.slice_id,
            "source_hash": source_hash,
            "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    })
