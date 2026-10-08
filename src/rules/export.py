"""Exports: rules JSON, decision-tree JSON and PMML TreeModel (rules.md section 7).

A rule set is the rules of one paragraph (one decision block). First-match
chains are recognised by their ``not`` exclusions and become nested
test / true_branch / false_branch nodes; a test shared by every rule (for
example a nested IF's outer condition) becomes the root. PMML is produced only
for tree-shaped sets that write a single output field.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any, Optional
from xml.sax.saxutils import escape, quoteattr

from src.common.models import Rule, dump
from src.rules.canon import canon_cond


def _plain(c: Any) -> dict[str, Any]:
    return c if isinstance(c, dict) else c.model_dump(by_alias=True, exclude_none=True)


def _parts(cond: dict[str, Any]) -> list[dict[str, Any]]:
    return list(cond["all"]) if "all" in cond else [cond]


def _as_cond(parts: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else {"all": parts}


def _key(c: dict[str, Any]) -> str:
    return repr(canon_cond(c))


def _refs_earlier(rule: Rule, block: list[Rule]) -> bool:
    """True when ``rule`` excludes (``not``) or shares a test with a rule already in ``block``."""
    known: set[str] = set()
    for r in block:
        parts = _parts(_plain(r.conditions))
        known.update(_key(p) for p in parts)
        known.add(_key(_plain(r.conditions)))
    for p in _parts(_plain(rule.conditions)):
        if "not" in p and _key(p["not"]) in known:
            return True
    first = _parts(_plain(rule.conditions))[0]
    return _key(first) == _key(_parts(_plain(block[0].conditions))[0]) and "not" not in first


def rule_sets(rules: list[Rule]) -> "OrderedDict[str, list[Rule]]":
    """Decision blocks: consecutive rules of a paragraph that form one first-match chain or nested IF.
    Independent sequential IFs become separate sets."""
    groups: OrderedDict[str, list[Rule]] = OrderedDict()
    counters: dict[str, int] = {}
    current_key: Optional[str] = None
    for r in rules:
        para = f"{r.trace.program}:{r.trace.paragraph or '-'}"
        block = groups.get(current_key) if current_key else None
        if block and current_key.rsplit("#", 1)[0] == para and not block[0].else_actions and _refs_earlier(r, block):
            block.append(r)
            continue
        counters[para] = counters.get(para, 0) + 1
        current_key = f"{para}#{counters[para]}"
        groups[current_key] = [r]
    return groups


def _leaf(r: Rule, actions) -> dict[str, Any]:
    return {"leaf": [dump(a) for a in actions], "rule_id": r.rule_id}


def _strip(items: list[tuple[Rule, list[dict[str, Any]]]]) -> list[tuple[Rule, list[dict[str, Any]]]]:
    """Drop first-match exclusions: ``not X`` where X is an earlier rule's own test."""
    seen: set[str] = set()
    out = []
    for r, parts in items:
        own = [p for p in parts if not ("not" in p and _key(p["not"]) in seen)]
        out.append((r, own))
        cond = _as_cond(own)
        if cond is not None:
            seen.add(_key(cond))
            seen.update(_key(p) for p in own)
    return out


def build_tree(rules: list[Rule]) -> dict[str, Any]:
    """Nested test / true_branch / false_branch tree for one decision block."""
    if len(rules) == 1 and (rules[0].else_actions or True):
        r = rules[0]
        return {"test": _plain(r.conditions), "true_branch": _leaf(r, r.actions),
                "false_branch": _leaf(r, r.else_actions) if r.else_actions else None}
    items = [(r, _parts(_plain(r.conditions))) for r in rules]
    k0 = _key(items[0][1][0])
    inside = [(r, p[1:]) for r, p in items if p and _key(p[0]) == k0]
    if len(inside) >= 2 and all(p for _, p in inside):
        outside = [(r, [x for x in p if not ("not" in x and _key(x["not"]) == k0)])
                   for r, p in items if not (p and _key(p[0]) == k0)]
        return {"test": items[0][1][0], "true_branch": _chain(_strip(inside)),
                "false_branch": _chain(_strip(outside)) if outside else None}
    return _chain(_strip(items))


def _chain(items: list[tuple[Rule, list[dict[str, Any]]]]) -> Optional[dict[str, Any]]:
    if not items:
        return None
    (r, own), rest = items[0], items[1:]
    cond = _as_cond(own)
    if cond is None:                                        # default branch (WHEN OTHER / final ELSE)
        return _leaf(r, r.actions)
    return {"test": cond, "true_branch": _leaf(r, r.actions), "false_branch": _chain(rest)}


def to_tree_json(rules: list[Rule]) -> list[dict[str, Any]]:
    return [{"rule_set": key, "rules": [r.rule_id for r in group], "tree": build_tree(group)}
            for key, group in rule_sets(rules).items()]


# ---- PMML ------------------------------------------------------------------------------

PMML_OPS = {"==": "equal", "!=": "notEqual", "<": "lessThan", "<=": "lessOrEqual",
            ">": "greaterThan", ">=": "greaterOrEqual"}


def _targets(node: Optional[dict[str, Any]], out: set[str]) -> set[str]:
    if node is None:
        return out
    if "leaf" in node:
        for a in node["leaf"]:
            if a["type"] == "set" and "value" in a:
                out.add(a.get("target") or a["source_name"])
            else:
                out.add("<non-literal>")
        return out
    _targets(node.get("true_branch"), out)
    _targets(node.get("false_branch"), out)
    return out


def _fields(c: dict[str, Any], out: "OrderedDict[str, str]") -> None:
    for k in ("all", "any"):
        for x in c.get(k) or []:
            _fields(x, out)
    if c.get("not"):
        _fields(c["not"], out)
    if c.get("field"):
        vals = c.get("value") if isinstance(c.get("value"), list) else [c.get("value")]
        numeric = all(isinstance(v, (int, float)) for v in vals if v is not None)
        out.setdefault(c["field"], "continuous" if numeric else "categorical")


def _predicate(c: dict[str, Any], ind: str) -> str:
    if "all" in c or "any" in c:
        op = "and" if "all" in c else "or"
        items = c["all"] if "all" in c else c["any"]
        if not items:
            return f"{ind}<True/>"
        inner = "\n".join(_predicate(x, ind + "  ") for x in items)
        return f'{ind}<CompoundPredicate booleanOperator="{op}">\n{inner}\n{ind}</CompoundPredicate>'
    if "not" in c:
        inner = c["not"]
        if "op" in inner and inner["op"] in PMML_OPS and "value" in inner:
            neg = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}[inner["op"]]
            return _predicate(dict(inner, op=neg), ind)
        # NOT(a AND b) == (NOT a) OR (NOT b): PMML has no unary not for compound predicates
        if "all" in inner or "any" in inner:
            flip = "any" if "all" in inner else "all"
            items = inner["all"] if "all" in inner else inner["any"]
            return _predicate({flip: [{"not": x} for x in items]}, ind)
        return f'{ind}<SimpleSetPredicate field={quoteattr(inner["field"])} booleanOperator="isNotIn"><Array type="string">{escape(" ".join(map(str, inner.get("value") or [])))}</Array></SimpleSetPredicate>'
    f, op, v = c["field"], c["op"], c.get("value")
    if op in PMML_OPS:
        return f"{ind}<SimplePredicate field={quoteattr(f)} operator=\"{PMML_OPS[op]}\" value={quoteattr(str(v))}/>"
    if op in ("in", "not_in"):
        arr = " ".join(f'"{x}"' if isinstance(x, str) else str(x) for x in v)
        typ = "string" if any(isinstance(x, str) for x in v) else "real"
        bop = "isIn" if op == "in" else "isNotIn"
        return f'{ind}<SimpleSetPredicate field={quoteattr(f)} booleanOperator="{bop}"><Array n="{len(v)}" type="{typ}">{escape(arr)}</Array></SimpleSetPredicate>'
    if op == "between":
        return _predicate({"all": [dict(c, op=">=", value=v[0]), dict(c, op="<=", value=v[1])]}, ind)
    raise ValueError(f"operator {op} has no PMML predicate")


def _nodes(node: Optional[dict[str, Any]], target: str, ind: str, counter: list[int]) -> str:
    """Flatten test/true/false into PMML's first-true-child node list."""
    if node is None:
        return ""
    if "leaf" in node:
        value = next((a["value"] for a in node["leaf"] if (a.get("target") or a["source_name"]) == target), None)
        counter[0] += 1
        return f'{ind}<Node id="{counter[0]}" score={quoteattr(str(value))}>\n{ind}  <True/>\n{ind}</Node>'
    counter[0] += 1
    nid = counter[0]
    true_part = node["true_branch"]
    true_xml = _nodes(true_part, target, ind + "  ", counter) if true_part and "leaf" not in true_part else ""
    score = ""
    if true_part and "leaf" in true_part:
        v = next((a["value"] for a in true_part["leaf"] if (a.get("target") or a["source_name"]) == target), None)
        score = f" score={quoteattr(str(v))}"
    out = [f'{ind}<Node id="{nid}"{score}>', _predicate(_plain(node["test"]), ind + "  ")]
    if true_xml:
        out.append(true_xml)
    out.append(f"{ind}</Node>")
    rest = _nodes(node.get("false_branch"), target, ind, counter)
    return "\n".join(out) + ("\n" + rest if rest else "")


def to_pmml(rules: list[Rule]) -> tuple[str, list[str]]:
    """PMML 4.4 with one TreeModel per eligible rule set; returns (xml, skipped reasons)."""
    models, skipped = [], []
    fields: OrderedDict[str, str] = OrderedDict()
    targets: OrderedDict[str, str] = OrderedDict()
    for entry in to_tree_json(rules):
        tree = entry["tree"]
        outs = _targets(tree, set())
        if len(outs) != 1 or "<non-literal>" in outs:
            skipped.append(f"{entry['rule_set']}: needs exactly one literal output field (has {sorted(outs)})")
            continue
        target = outs.pop()
        local: OrderedDict[str, str] = OrderedDict()
        stack = [tree]
        while stack:
            n = stack.pop()
            if n and "test" in n:
                _fields(_plain(n["test"]), local)
                stack += [n.get("true_branch"), n.get("false_branch")]
        fields.update(local)
        targets[target] = "categorical"
        body = _nodes(tree, target, "      ", [0])
        mining = "\n".join(f'      <MiningField name={quoteattr(f)}/>' for f in local) + \
            f'\n      <MiningField name={quoteattr(target)} usageType="target"/>'
        models.append(
            f'  <TreeModel modelName={quoteattr(entry["rule_set"])} functionName="classification" '
            f'splitCharacteristic="multiSplit" noTrueChildStrategy="returnLastPrediction">\n'
            f'    <MiningSchema>\n{mining}\n    </MiningSchema>\n'
            f'    <Node id="0">\n      <True/>\n{body}\n    </Node>\n  </TreeModel>')
    dd = "\n".join(
        f'    <DataField name={quoteattr(f)} optype="{t}" dataType="{"double" if t == "continuous" else "string"}"/>'
        for f, t in list(fields.items()) + [(k, v) for k, v in targets.items() if k not in fields])
    n_fields = len(fields) + len([k for k in targets if k not in fields])
    if len(models) > 1:
        segs = "\n".join(f'    <Segment id="{i + 1}">\n      <True/>\n' + m.replace("\n", "\n    ") + "\n    </Segment>"
                         for i, m in enumerate(models))
        body = (f'  <MiningModel functionName="classification">\n    <MiningSchema/>\n'
                f'    <Segmentation multipleModelMethod="modelChain">\n{segs}\n    </Segmentation>\n  </MiningModel>')
    else:
        body = models[0] if models else "  <!-- no tree-shaped rule set with a single output field -->"
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<PMML version="4.4" xmlns="http://www.dmg.org/PMML-4_4">\n'
           '  <Header description="Decision rules extracted from COBOL by cobol-to-decision"/>\n'
           f'  <DataDictionary numberOfFields="{n_fields}">\n{dd}\n  </DataDictionary>\n'
           f"{body}\n</PMML>\n")
    return xml, skipped


def to_json(rules: list[Rule]) -> str:
    return json.dumps([dump(r) for r in rules], indent=2)
