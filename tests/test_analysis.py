"""Parser and slicer tests (implementation.md 2.3): one test per construct."""

from src.analysis.analyze import analyze, analyze_program
from tests.conftest import FIXTURES, TXNCHK

DATA = """
01 WS-IN.
   05 AGE        PIC 9(3).
      88 MINOR   VALUE 0 THRU 17.
   05 AMT        PIC 9(7)V99.
   05 CODE-X     PIC X(2).
   05 LIMIT-AMT  PIC 9(7)V99.
01 FLAG          PIC X VALUE 'N'.
01 FEE           PIC 9(5)V99 VALUE 0.
"""


def leaf(name, op, value=None, **extra):
    out = {"source_name": name, "op": op}
    if value is not None:
        out["value"] = value
    out.update(extra)
    return out


def only_condition(program, cond_text):
    path = program(DATA, f"    IF {cond_text}\n        MOVE 'Y' TO FLAG\n    END-IF")
    slices = analyze(path)
    assert len(slices) == 1, slices
    return slices[0].condition


# ---- golden reference program ------------------------------------------------

def test_txnchk_gives_exactly_one_slice():
    slices = analyze(TXNCHK)
    assert len(slices) == 1
    s = slices[0]
    assert (s.paragraph, s.line_start, s.line_end) == ("CHECK-TXN", 16, 18)
    assert s.condition == {"all": [leaf("CUST-AGE", "<", 18), leaf("TXN-AMOUNT", ">", 5000)]}
    assert s.actions == [{"type": "set", "source_name": "FLAG-REVIEW", "value": "Y"}]
    assert s.else_actions == [] and not s.unsupported and not s.external_dependency


# ---- conditions -----------------------------------------------------------------

def test_abbreviated_subject_and_operator(program):
    assert only_condition(program, "AGE > 17 AND < 65") == {"all": [leaf("AGE", ">", 17), leaf("AGE", "<", 65)]}
    assert only_condition(program, "CODE-X = 'A1' OR 'A2'") == {"any": [leaf("CODE-X", "==", "A1"), leaf("CODE-X", "==", "A2")]}


def test_abbreviated_with_negated_operator(program):
    assert only_condition(program, "AGE > 17 AND NOT < 30") == {"all": [leaf("AGE", ">", 17), leaf("AGE", ">=", 30)]}


def test_not_operators_and_word_operators(program):
    assert only_condition(program, "AGE NOT < 18") == {"all": [leaf("AGE", ">=", 18)]}
    assert only_condition(program, "AGE NOT = 18") == {"all": [leaf("AGE", "!=", 18)]}
    assert only_condition(program, "AMT IS GREATER THAN OR EQUAL TO 100") == {"all": [leaf("AMT", ">=", 100)]}
    assert only_condition(program, "AMT LESS THAN 5") == {"all": [leaf("AMT", "<", 5)]}
    assert only_condition(program, "AMT IS NOT GREATER THAN 5") == {"all": [leaf("AMT", "<=", 5)]}


def test_88_level_expands_to_field_comparison(program):
    assert only_condition(program, "MINOR") == {"all": [leaf("AGE", "between", [0, 17])]}
    assert only_condition(program, "NOT MINOR") == {"not": leaf("AGE", "between", [0, 17])}


def test_literal_on_left_is_flipped_and_fields_compare_by_ref(program):
    assert only_condition(program, "18 > AGE") == {"all": [leaf("AGE", "<", 18)]}
    assert only_condition(program, "AMT > LIMIT-AMT") == {"all": [leaf("AMT", ">", ref="LIMIT-AMT")]}


def test_parentheses_and_precedence(program):
    cond = only_condition(program, "(AGE < 18 OR AGE > 65) AND AMT > 100")
    assert cond == {"all": [{"any": [leaf("AGE", "<", 18), leaf("AGE", ">", 65)]}, leaf("AMT", ">", 100)]}


def test_class_and_sign_conditions(program):
    assert only_condition(program, "CODE-X IS NOT NUMERIC") == {"not": leaf("CODE-X", "is_numeric")}
    assert only_condition(program, "AMT IS POSITIVE") == {"all": [leaf("AMT", "is_positive")]}
    assert only_condition(program, "AMT = ZERO") == {"all": [leaf("AMT", "==", 0)]}


def test_arithmetic_in_condition_is_flagged(program):
    path = program(DATA, "    IF AMT + FEE > 100\n        MOVE 'Y' TO FLAG\n    END-IF")
    s = analyze(path)[0]
    assert s.condition == {"all": [leaf("AMT + FEE", ">", 100)]}
    assert "arithmetic expression in condition" in s.unsupported


# ---- statement structure ----------------------------------------------------------

def test_period_closes_all_open_ifs(program):
    proc = ("    IF AGE < 18\n        IF AMT > 100\n            MOVE 'Y' TO FLAG\n"
            "        ELSE\n            MOVE 'N' TO FLAG.\n    MOVE 1 TO FEE")
    a = analyze_program(program(DATA, proc))
    assert a.diagnostics == []
    assert [(s.line_start, s.line_end) for s in a.slices] == [(16, 17), (18, 19)]
    assert a.slices[1].condition == {"all": [leaf("AGE", "<", 18), {"not": leaf("AMT", ">", 100)}]}
    assert [s.verb for s in a.program.paragraphs[0].statements] == ["IF", "MOVE", "STOP RUN"]


def test_branch_with_actions_and_nested_decision(program):
    proc = ("    IF AGE < 18\n        MOVE 'Y' TO FLAG\n        IF AMT > 100\n"
            "            MOVE 10 TO FEE\n        END-IF\n    END-IF")
    slices = analyze(program(DATA, proc))
    assert [s.condition for s in slices] == [
        {"all": [leaf("AGE", "<", 18)]},
        {"all": [leaf("AGE", "<", 18), leaf("AMT", ">", 100)]},
    ]
    assert slices[0].actions == [{"type": "set", "source_name": "FLAG", "value": "Y"}]


def test_compute_add_multiply_actions(program):
    proc = ("    IF AGE < 18\n        COMPUTE FEE ROUNDED = AMT * 0.02 + 1\n        ADD 5 TO FEE\n"
            "        MULTIPLY 2 BY FEE\n        SUBTRACT 1 FROM AMT GIVING FEE\n    END-IF")
    acts = analyze(program(DATA, proc))[0].actions
    assert acts == [
        {"type": "compute", "source_name": "FEE", "expr": "AMT * 0.02 + 1"},
        {"type": "compute", "source_name": "FEE", "expr": "FEE + 5"},
        {"type": "compute", "source_name": "FEE", "expr": "FEE * 2"},
        {"type": "compute", "source_name": "FEE", "expr": "AMT - 1"},
    ]


def test_inline_perform_test_after_without_with(program):
    """Found in IBM's public SAM1.cbl: WITH is optional in PERFORM [WITH] TEST AFTER."""
    proc = ("    IF AGE < 18\n        MOVE 'Y' TO FLAG\n    ELSE\n        MOVE 0 TO FEE\n"
            "        PERFORM TEST AFTER VARYING AGE FROM 1 BY 1\n           UNTIL AGE > 12\n"
            "         MOVE 0 TO AMT\n        END-PERFORM\n        MOVE 'N' TO FLAG\n    END-IF")
    a = analyze_program(program(DATA, proc))
    assert a.diagnostics == []
    assert len(a.slices) == 1
    else_acts = a.slices[0].else_actions
    assert else_acts[0] == {"type": "set", "source_name": "FEE", "value": 0}
    assert else_acts[-1] == {"type": "set", "source_name": "FLAG", "value": "N"}
    assert "inline PERFORM loop" in a.slices[0].unsupported


def test_if_with_only_io_is_not_a_rule(program):
    proc = "    IF AGE < 18\n        DISPLAY 'MINOR'\n    ELSE\n        DISPLAY 'ADULT'\n    END-IF"
    assert analyze(program(DATA, proc)) == []


def test_unsupported_constructs_do_not_crash(program):
    proc = ("    IF AGE < 18\n        EXEC SQL UPDATE T SET X = 1 END-EXEC\n    END-IF\n"
            "    IF AMT > 5\n        GO TO MAIN-PARA\n    END-IF")
    slices = analyze(program(DATA, proc))
    assert slices[0].unsupported == ["EXEC SQL"] and slices[0].external_dependency
    assert slices[1].unsupported == ["GO TO MAIN-PARA"] and slices[1].actions == []


# ---- the construct fixture: every mapping in rules.md section 2 -------------------

def test_constructs_fixture():
    a = analyze_program(FIXTURES / "CONSTRUCTS.cob")
    assert a.diagnostics == []
    by_id = {s.slice_id.split("-")[1]: s for s in a.slices}
    assert len(by_id) == 16
    # nested IF: one slice per leaf path
    assert by_id["S002"].condition == {"all": [leaf("ACCT-TYPE", "==", "S"), {"not": leaf("BALANCE", ">=", 10000)}]}
    # ELSE-IF chain: later branches carry not of earlier ones
    assert by_id["S005"].condition == {"all": [{"not": leaf("DAYS-LATE", ">", 90)}, {"not": leaf("DAYS-LATE", ">", 30)}]}
    assert (by_id["S005"].line_start, by_id["S005"].line_end) == (47, 48)
    # EVALUATE TRUE: first-match, WHEN OTHER is the default
    s7 = by_id["S007"]
    assert s7.condition["all"][0] == {"not": {"all": [leaf("ACCT-TYPE", "==", "S"), leaf("BALANCE", ">=", 10000)]}}
    assert by_id["S008"].actions == [{"type": "set", "source_name": "INT-RATE", "value": 0.01}]
    # EVALUATE subject ALSO subject with THRU, ANY and stacked WHENs
    assert by_id["S009"].condition == {"all": [leaf("DAYS-LATE", "between", [0, 10])]}
    assert by_id["S010"].condition["all"][1]["any"][1] == {
        "all": [leaf("DAYS-LATE", "between", [11, 30]), leaf("ACCT-TYPE", "==", "P")]}
    assert (by_id["S010"].line_start, by_id["S010"].line_end) == (64, 66)
    assert by_id["S011"].actions == [{"type": "compute", "source_name": "PENALTY", "expr": "PENALTY + 25"}]
    # 88-levels, NOT 88 with several values, SET cond-name TO TRUE
    assert by_id["S012"].condition == {"all": [leaf("CUST-AGE", "between", [0, 17]),
                                                {"not": leaf("ACCT-TYPE", "in", ["P", "G"])}]}
    assert by_id["S012"].actions == [{"type": "set", "source_name": "REVIEW-FLAG", "value": "Y"}]
    # abbreviated condition follows COBOL precedence (AND before OR)
    assert by_id["S013"].condition == {"any": [
        {"all": [leaf("CUST-AGE", ">", 17), leaf("CUST-AGE", "<", 65), leaf("RISK-CODE", "==", "A1")]},
        leaf("RISK-CODE", "==", "A2")]}
    # IF/ELSE at top level is one rule with else_actions
    assert by_id["S014"].else_actions == [{"type": "set", "source_name": "FEE-AMT", "value": 0}]
    # CALL is an external dependency, GO TO is flagged
    assert by_id["S015"].external_dependency and by_id["S015"].unsupported == ["GO TO OTHER-EXIT"]
    assert by_id["S016"].actions == [{"type": "perform", "source_name": "ABBREV"}]


def test_fixed_format_program_with_copybook():
    s = analyze(FIXTURES / "FIXEDPRG.cbl", [FIXTURES])[0]
    assert (s.line_start, s.line_end) == (13, 16)
    assert s.copybooks == ["CUSTREC.cpy"]
    assert s.condition == {"all": [leaf("CUST-AGE", "<", 18), leaf("CUST-LIMIT", ">", 1000)]}


# ---- public code we did not write (IBM Z Open Editor samples, Apache-2.0) ----------

PUBLIC = FIXTURES.parent.parent.parent / "data" / "public" / "ibm-zopeneditor-sample"


def test_public_ibm_samples_parse_cleanly():
    for program, expected in (("SAM1.cbl", 26), ("SAM2.cbl", 19)):
        a = analyze_program(PUBLIC / program, [PUBLIC])
        assert a.source.fmt == "fixed"
        assert a.diagnostics == [], program
        assert len(a.slices) == expected, program
    sam1 = analyze(PUBLIC / "SAM1.cbl", [PUBLIC])
    call = next(s for s in sam1 if s.external_dependency)
    assert {"type": "call", "source_name": "SAM2"} in call.actions
