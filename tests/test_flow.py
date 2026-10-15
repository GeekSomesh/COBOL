"""Control flow across paragraphs: contexts, GO TO guards, derivations, dead code, interpreter."""

import copy
from pathlib import Path

import pytest

from src.analysis.analyze import analyze, analyze_program
from src.analysis.cfg import Paragraphs
from src.pipeline import extract
from src.rules.decide import input_fields, score
from tests.conftest import ROOT

FLOW = ROOT / "tests" / "fixtures" / "cobol" / "FLOWPRG.cob"


def leaf(name, op, value=None):
    out = {"source_name": name, "op": op}
    if value is not None:
        out["value"] = value
    return out


@pytest.fixture(scope="module")
def flow():
    a = analyze_program(FLOW)
    return a, {s.paragraph + ":" + str(s.line_start): s for s in a.slices}


def test_contexts_guards_and_callers(flow):
    a, by = flow
    assert a.diagnostics == []
    first, second = by["CHECK-INPUT:39"], by["CHECK-INPUT:43"]
    assert first.context is None and first.performed_from == ["MAIN-PARA"]
    # the second check only runs when the first one did not GO TO the exit
    assert second.context == {"not": leaf("CUST-AGE", "<", 18)}
    # the SCORING section is only PERFORMed when the input passed validation
    assert by["SCORE-DECIDE:58"].context == {"all": [leaf("WS-VALID", "==", "Y")]}
    assert by["SCORE-AMOUNT:64"].context == {"all": [leaf("WS-VALID", "==", "Y")]}


def test_derivations_and_dead_code(flow):
    a, by = flow
    assert by["SCORE-DECIDE:58"].derivations == {"WS-SCORE": "CUST-AGE * 2 + ACCT-YEARS"}
    # WS-VALID = 'Y' is only set after the guards, so it is not a derivation
    assert by["MAIN-PARA:24"].derivations == {}
    assert by["DEAD-CODE:53"].reachable is False
    assert all(s.reachable for k, s in by.items() if not k.startswith("DEAD-CODE"))
    assert a.flow.has_loops


def test_perform_ranges_resolve_sections_and_thru(flow):
    a, _ = flow
    p = Paragraphs(a.program)
    names = lambda idx: [a.program.paragraphs[i].name for i in idx]   # noqa: E731
    assert names(p.resolve("SCORING")) == ["SCORE-DECIDE", "SCORE-AMOUNT"]
    assert names(p.resolve("CHECK-INPUT", "CHECK-INPUT-EXIT")) == ["CHECK-INPUT", "CHECK-INPUT-EXIT"]
    assert p.resolve("NO-SUCH-PARA") == []


def test_go_to_depending_is_flagged(program):
    data = "01 WS-X PIC 9.\n01 FLAG PIC X VALUE 'N'."
    proc = "    IF WS-X > 0\n        GO TO MAIN-PARA MAIN-PARA DEPENDING ON WS-X\n    END-IF"
    s = analyze(program(data, proc))[0]
    assert s.unsupported == ["GO TO DEPENDING ON WS-X"]


def test_scoring_applies_context_and_derivations():
    rules = {r.trace.paragraph: r for r in extract(FLOW).rules}
    decide = rules["SCORE-DECIDE"]
    fields = input_fields([decide])
    assert {"cust_age", "acct_years", "ws_valid"} <= set(fields) and "ws_score" not in fields
    assert score([decide], {"cust_age": 60, "acct_years": 5, "ws_valid": "Y"})["decision"] == {"decision": "A"}
    assert score([decide], {"cust_age": 30, "acct_years": 5, "ws_valid": "Y"})["decision"] == {"decision": "C"}
    assert score([decide], {"cust_age": 60, "acct_years": 5, "ws_valid": "N"})["fired_rules"] == []


@pytest.mark.cobol
def test_interpreter_matches_compiled_program_and_catches_control_errors(toolchain):
    from src.verify.checks import differential
    exe = ROOT / "data" / "synthetic" / "bin" / "FLOWPRG"
    assert toolchain.compile(FLOW, exe).ok
    ex = extract(FLOW)
    good, rows = differential(ex.analysis, ex.rules, exe, toolchain)
    assert good.unsimulated == 0 and good.agreement == 1.0 and len(rows) > 50

    broken = copy.deepcopy(ex.rules)
    k = next(i for i, r in enumerate(broken) if r.trace.paragraph == "CHECK-INPUT")
    broken[k].actions = [a for a in broken[k].actions if a.type != "goto"]   # forget the early exit
    bad, _ = differential(ex.analysis, broken, exe, toolchain)
    assert bad.agreement < 1.0
