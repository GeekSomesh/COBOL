"""Exports keep rule semantics; concept search finds the right rules."""

import json
import xml.etree.ElementTree as ET

import pytest

from src.pipeline import extract
from src.rules.decide import conflicts, explain, score
from src.rules.export import rule_sets, to_pmml, to_tree_json
from src.rules.search import search
from src.verify.engine import apply_actions, eval_cond, is_numeric_entry, store
from src.verify.harness import build_grid, build_harness
from tests.conftest import FIXTURES, ROOT, TXNCHK

SYN = ROOT / "data" / "synthetic"


def run_tree(node, state):
    while node is not None and "leaf" not in node:
        node = node["true_branch"] if eval_cond(node["test"], state) else node["false_branch"]
    if node is not None:
        apply_actions(node["leaf"], state, {})


def run_rules(rules, state):
    for r in rules:
        d = r.model_dump(by_alias=True)
        apply_actions(d["actions"] if eval_cond(d["conditions"], state) else d["else_actions"], state, {})


def corpus_sample(n=24):
    splits = json.loads((ROOT / "data" / "splits.json").read_text())
    names = splits["test"][:8] + splits["val"][:8] + splits["train"][:8]
    return [SYN / f"{p}.cob" for p in names[:n]]


@pytest.mark.parametrize("path", corpus_sample() + [FIXTURES / "CONSTRUCTS.cob"], ids=lambda p: p.stem)
def test_decision_trees_match_rule_semantics(path):
    ex = extract(path, [path.parent])
    h = build_harness(ex.analysis)
    if h is None:                                  # CONSTRUCTS has no single input record harness
        from src.verify.checks import field_harness
        fields = {}
        for s in ex.analysis.slices:
            fields.update(s.fields)
        h = field_harness(fields)
    rows = build_grid(h, [s.condition for s in ex.analysis.slices], random_rows=60)
    initial = {n: store(e.get("value", 0) if is_numeric_entry(e) else e.get("value", ""), e)
               for n, e in ex.analysis.program.dictionary.entries().items()}
    trees = to_tree_json(ex.rules)
    groups = rule_sets(ex.rules)
    assert [t["rules"] for t in trees] == [[r.rule_id for r in g] for g in groups.values()]
    for t, group in zip(trees, groups.values()):
        for row in rows:
            a, b = {**initial, **row}, {**initial, **row}
            run_tree(t["tree"], a)
            run_rules(group, b)
            assert a == b, (t["rule_set"], row)


def test_pmml_is_well_formed_and_skips_multi_output_sets():
    ex = extract(FIXTURES / "CONSTRUCTS.cob")
    xml, skipped = to_pmml(ex.rules)
    root = ET.fromstring(xml)
    ns = {"p": "http://www.dmg.org/PMML-4_4"}
    assert root.tag.endswith("PMML") and root.findall(".//p:TreeModel", ns)
    assert any("EVAL-SUBJ" in s for s in skipped)


def test_search_ranks_by_concept_and_number():
    rules = [(r, "p") for p in [TXNCHK, SYN / "INT0003.cob", SYN / "PEN0003.cob"]
             for r in extract(p, [SYN]).rules]
    hits = search(rules, "customers under 18")
    assert hits[0][0].rule_id == "TXNCHK-R001"
    assert search(rules, "") and len(search(rules, "")) == len(rules)


def test_score_uses_defaults_and_explain_ranks_inputs():
    rules = extract(TXNCHK).rules
    assert score(rules, {"cust_age": 17, "txn_amount": 6000}, {"FLAG-REVIEW": "N"})["decision"] == {"flag_review": "Y"}
    assert score(rules, {"cust_age": 40, "txn_amount": 6000}, {"FLAG-REVIEW": "N"})["decision"] == {"flag_review": "N"}
    ex = explain(rules, defaults={"FLAG-REVIEW": "N"})
    assert ex["classes"] == ["N", "Y"] and {f["name"] for f in ex["features"]} == {"cust_age", "txn_amount"}


def test_conflicts_across_programs():
    a = extract(TXNCHK).rules[0]
    b = a.model_copy(deep=True)
    b.rule_id, b.trace.program = "OTHER-R001", "OTHER.cob"
    b.actions[0].value = "N"
    found = conflicts([a, b])
    assert found and found[0]["kind"] == "conflict" and found[0]["field"] == "flag_review"
    dup = a.model_copy(deep=True)
    dup.rule_id, dup.trace.program = "DUP-R001", "DUP.cob"
    assert any(c["kind"] == "duplicate" for c in conflicts([a, dup]))
