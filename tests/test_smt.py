"""Exact reasoning over rule conditions with z3: equivalence, conflicts, impossible rules."""

import pytest

from src.verify import smt

pytestmark = pytest.mark.skipif(not smt.available(), reason="z3-solver not installed")


def leaf(name, op, value=None, ref=None):
    out = {"source_name": name, "op": op}
    if value is not None:
        out["value"] = value
    if ref is not None:
        out["ref"] = ref
    return out


def test_equivalence_is_proven_or_refuted_with_an_input():
    a = {"all": [leaf("AGE", ">=", 18), leaf("AMT", "<", 5000)]}
    de_morgan = {"not": {"any": [leaf("AGE", "<", 18), leaf("AMT", ">=", 5000)]}}
    assert smt.equivalent(a, de_morgan) == (True, None)
    off_by_one = {"all": [leaf("AGE", ">", 18), leaf("AMT", "<", 5000)]}
    same, example = smt.equivalent(a, off_by_one)
    assert same is False and example["AGE"] == 18          # the one age where they differ


def test_text_fields_ignore_trailing_spaces_and_sets_expand():
    assert smt.equivalent({"all": [leaf("CODE", "==", "ADD   ")]}, {"all": [leaf("CODE", "==", "ADD")]})[0]
    assert smt.equivalent({"all": [leaf("CODE", "in", ["A", "B"])]},
                          {"any": [leaf("CODE", "==", "A"), leaf("CODE", "==", "B")]})[0]
    assert smt.equivalent({"all": [leaf("N", "between", [1, 5])]},
                          {"all": [leaf("N", ">=", 1), leaf("N", "<=", 5)]})[0]


def test_witness_handles_arithmetic_refs_and_class_tests():
    ok, m = smt.witness([{"all": [leaf("TXN-AMT + TXN-FEE", ">", 5000), leaf("TXN-AMT", "<", 100)]}])
    assert ok and m["TXN-AMT"] + m["TXN-FEE"] > 5000 and m["TXN-AMT"] < 100
    ok, m = smt.witness([{"all": [{"not": leaf("DATA", "is_numeric")}, leaf("KEY", "<", ref="PREV")]}])
    assert ok and not m["DATA"].strip().isdigit() and m["KEY"] < m["PREV"]
    assert smt.witness([{"all": [leaf("AGE", ">", 5)]}, {"all": [leaf("AGE", "<", 3)]}]) == (False, None)


def test_unsupported_mixes_are_reported_not_guessed():
    with pytest.raises(smt.Unsupported):
        smt.witness([{"all": [leaf("X", "<", "ABC")]}, {"all": [leaf("X + 1", ">", 2)]}])


def test_impossible_rule_is_routed_to_review(program):
    from src.pipeline import PipelineConfig, run_pipeline
    data = "01 AGE PIC 99.\n01 FLAG PIC X VALUE 'N'."
    proc = "    IF AGE > 50\n        IF AGE < 30\n            MOVE 'Y' TO FLAG\n        END-IF\n    END-IF"
    ex = run_pipeline(program(data, proc), cfg=PipelineConfig(model=None, differential=False))
    r = ex.rules[0]
    assert r.confidence["satisfiable"] is False and r.status == "needs_review"
    assert any("can never be true" in w for w in r.confidence["warnings"])


def test_conflicts_use_the_solver():
    from src.common.models import Rule
    from src.rules.decide import conflicts

    def rule(rid, para, cond, value):
        return Rule.model_validate({
            "rule_id": rid, "conditions": cond,
            "actions": [{"type": "set", "target": "decision", "source_name": "DC", "value": value}],
            "trace": {"program": "P.cob", "paragraph": para, "line_start": 1, "line_end": 2}})

    a = rule("P-R1", "A", {"all": [{"field": "score", "source_name": "S", "op": ">", "value": 700},
                                   {"field": "income", "source_name": "I", "op": ">=", "value": 9000}]}, "A")
    b = rule("P-R2", "B", {"all": [{"field": "score", "source_name": "S", "op": "<", "value": 701}]}, "D")
    c = rule("P-R3", "C", {"all": [{"field": "score", "source_name": "S", "op": "<", "value": 700}]}, "D")
    found = conflicts([a, b, c])
    assert [f["rules"] for f in found] == [["P-R1", "P-R2"]]      # only score 700 < x < 701 overlaps
    ex = found[0]["example_inputs"]
    assert found[0]["method"] == "smt" and 700 < ex["score"] < 701 and ex["income"] >= 9000
