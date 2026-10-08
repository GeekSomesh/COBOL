"""Verification: evaluator semantics, harness encoding, and the 'broken rule is caught' gate."""

import copy
from decimal import Decimal
from pathlib import Path

import pytest

from src.analysis.analyze import analyze_program
from src.pipeline import PipelineConfig, extract, run_pipeline
from src.verify.checks import consistency_score, differential, naming_score, structural
from src.verify.confidence import apply_confidence, combine
from src.verify.engine import eval_cond, eval_expr, store
from src.verify.harness import build_grid, build_harness, decode_display, encode_record
from tests.conftest import ROOT, TXNCHK

NUM_72 = {"category": "numeric", "int_digits": 7, "scale": 2, "signed": False}


def test_store_truncates_like_cobol():
    assert store(Decimal("1234.567"), NUM_72) == Decimal("1234.56")
    assert store(Decimal("123456789.5"), NUM_72) == Decimal("3456789.50")   # high-order digits lost
    assert store(-5, NUM_72) == Decimal("5.00")                              # unsigned field
    assert store("YES", {"category": "alphanumeric", "length": 1}) == "Y"


def test_expressions_and_conditions():
    state = {"A": Decimal("10"), "B": Decimal("4")}
    assert eval_expr("(A - 2) * 0.50 + B / 4", state) == Decimal("5.0")
    assert eval_expr("A ** 2", state) == 100
    row = {"AGE": Decimal(17), "T": "G"}
    assert eval_cond({"all": [{"source_name": "AGE", "op": "between", "value": [0, 17]},
                              {"source_name": "T", "op": "in", "value": ["G", "P"]}]}, row)
    assert not eval_cond({"not": {"source_name": "AGE", "op": "<", "value": 18}}, row)
    assert eval_cond({"source_name": "T", "op": "==", "value": "G  "}, row)     # COBOL space padding


def test_harness_reads_layout_and_outputs_from_the_program():
    h = build_harness(analyze_program(TXNCHK))
    assert [f["name"] for f in h.layout] == ["CUST-AGE", "TXN-AMOUNT"] and h.outputs == ["FLAG-REVIEW"]
    assert encode_record(h, {"CUST-AGE": 17, "TXN-AMOUNT": Decimal("7500.00")}) == "017000750000"
    assert decode_display("000750000", h.fields["TXN-AMOUNT"]) == Decimal("7500.00")
    assert h.initial["FLAG-REVIEW"] == "N"


def test_grid_hits_both_sides_of_every_threshold():
    a = analyze_program(TXNCHK)
    rows = build_grid(build_harness(a), [s.condition for s in a.slices])
    ages = {r["CUST-AGE"] for r in rows}
    assert {Decimal(17), Decimal(18), Decimal(19)} <= ages
    assert any(eval_cond(a.slices[0].condition, r) for r in rows)
    assert any(not eval_cond(a.slices[0].condition, r) for r in rows)


def test_naming_prefers_documented_names():
    ex = extract(ROOT / "tests" / "fixtures" / "cobol" / "FIXEDPRG.cbl", [ROOT / "tests" / "fixtures" / "cobol"])
    rule, s = ex.rules[0], ex.analysis.slices[0]
    good = copy.deepcopy(rule)
    good.conditions.all[1].field = "credit_limit"           # matches comment CREDIT LIMIT IN DOLLARS
    bad = copy.deepcopy(rule)
    bad.conditions.all[1].field = "zebra_count"
    assert naming_score(good, s) > naming_score(bad, s)


def test_confidence_routing():
    assert combine({"structural": 1.0, "differential": None, "consistency": None, "naming": 0.5}) == pytest.approx(0.9)
    ex = extract(TXNCHK)
    ok = apply_confidence(ex.rules[0], {"structural": 1.0, "differential": 1.0, "consistency": 1.0, "naming": 1.0})
    assert ok.status == "candidate" and ok.confidence["score"] == 1.0
    weak = apply_confidence(ex.rules[0], {"structural": 1.0, "differential": 0.5, "consistency": 1.0, "naming": 1.0})
    assert weak.status == "needs_review"


def test_consistency_score():
    from src.llm.enrich import Enrichment
    e1 = Enrichment(title="t", intent="Flag minors over 5000", field_names={"A": "age"}, concepts=["age"])
    e2 = Enrichment(title="t", intent="Flag minors over 5000", field_names={"A": "age"}, concepts=["age"])
    e3 = Enrichment(title="t", intent="Something else entirely", field_names={"A": "years"}, concepts=["x"])
    assert consistency_score([e1, e2]) == 1.0
    assert consistency_score([e1, e2, e3]) < consistency_score([e1, e2])


@pytest.mark.cobol
def test_broken_rule_is_caught(toolchain):
    exe = ROOT / "data" / "synthetic" / "bin" / "TXNCHK"
    assert toolchain.compile(TXNCHK, exe).ok
    good = run_pipeline(TXNCHK, cfg=PipelineConfig(exe=exe), toolchain=toolchain)
    assert good.report["differential"]["agreement"] == 1.0
    assert good.rules[0].status == "candidate"

    ex = extract(TXNCHK)
    broken = copy.deepcopy(ex.rules[0])
    broken.conditions.all[0].value = 21                       # age threshold 18 -> 21
    diff, rows = differential(ex.analysis, [broken], exe, toolchain)
    assert diff.agreement < 1.0 and diff.mismatches
    s = structural(broken, ex.analysis.slices[0], rows)
    assert s < 1.0
    routed = apply_confidence(broken, {"structural": s, "differential": diff.rule_agreement(0),
                                       "consistency": None, "naming": 1.0})
    assert routed.status == "needs_review"
    assert "disagrees with compiled COBOL on some inputs" in routed.confidence["warnings"]


@pytest.mark.cobol
def test_public_program_without_harness_still_gets_confidence(toolchain):
    public = ROOT / "data" / "public" / "ibm-zopeneditor-sample"
    ex = run_pipeline(public / "SAM2.cbl", [public], toolchain=toolchain)
    assert "differential" not in ex.report                    # file I/O program: no ACCEPT/DISPLAY harness
    assert all(r.confidence["components"]["differential"] is None for r in ex.rules)
    assert all(r.confidence["components"]["structural"] == 1.0 for r in ex.rules)
