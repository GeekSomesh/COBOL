"""Hard-coded lookup tables: decoding VALUE storage through OCCURS, and SEARCH expanded per row."""

from decimal import Decimal

import pytest

from src.analysis.analyze import analyze, analyze_program
from src.analysis.tables import decode
from src.pipeline import extract
from src.rules.decide import input_fields, score
from tests.conftest import ROOT

RATETAB = ROOT / "tests" / "fixtures" / "cobol" / "RATETAB.cob"

TABLE = """01 RATE-VALUES.
   05 FILLER PIC X(5) VALUE 'G0150'.
   05 FILLER PIC X(5) VALUE 'S0250'.
01 RATE-TABLE REDEFINES RATE-VALUES.
   05 RATE-ENTRY OCCURS 2 TIMES INDEXED BY RT-IDX.
      10 RATE-TIER PIC X.
      10 RATE-PCT PIC 9V999.
01 CUST-TIER PIC X.
01 WS-RATE PIC 9V999 VALUE 0."""

SEARCH = """    SET RT-IDX TO 1
    SEARCH RATE-ENTRY
        AT END MOVE 0 TO WS-RATE
        WHEN RATE-TIER (RT-IDX) = CUST-TIER
            MOVE RATE-PCT (RT-IDX) TO WS-RATE
    END-SEARCH"""


def test_decode_reads_value_storage_through_the_occurs():
    a = analyze_program(RATETAB)
    t = decode(a.program.dictionary, "RATE-ENTRY")
    assert t.columns == ["RATE-TIER", "RATE-PCT", "RATE-MIN-FEE"]
    assert t.rows[1] == {"RATE-TIER": "S", "RATE-PCT": Decimal("0.025"), "RATE-MIN-FEE": Decimal("5.00")}
    assert t.lines == [[7], [8], [9]]
    assert decode(a.program.dictionary, "RATE-TIER") is None          # not an OCCURS item


def test_search_becomes_one_rule_per_row_plus_at_end():
    rules = extract(RATETAB).rules
    rows = [r for r in rules if r.lookup]
    assert [r.lookup["row"] for r in rows] == [1, 2, 3, None]
    first, second, at_end = rows[0], rows[1], rows[3]
    assert first.model_dump(by_alias=True)["conditions"]["all"][0]["value"] == "G"
    assert {a.source_name: a.value for a in second.actions} == {"WS-RATE": 0.025, "WS-MIN-FEE": 5, "WS-FOUND": "Y"}
    assert at_end.lookup["at_end"] and not any(r.unsupported for r in rows)
    # first match wins: row 2 only applies when row 1 did not match
    assert len(second.model_dump(by_alias=True)["conditions"]["all"]) == 2


def test_scoring_walks_the_table_and_the_minimum_fee():
    rules = extract(RATETAB).rules
    assert set(input_fields(rules)) == {"cust_tier", "txn_amt"}
    assert score(rules, {"cust_tier": "S", "txn_amt": 1000})["decision"]["ws_fee"] == 25.0
    assert score(rules, {"cust_tier": "G", "txn_amt": 100})["decision"]["ws_fee"] == 2.5      # minimum fee
    out = score(rules, {"cust_tier": "Z", "txn_amt": 100})["decision"]
    assert out["ws_found"] == "N" and out["ws_rate"] == 0.05                                   # AT END


def test_search_is_flagged_when_the_table_is_not_static(program):
    filled = TABLE.replace("VALUE 'G0150'", "").replace("VALUE 'S0250'", "")
    s = analyze(program(filled, SEARCH))
    assert s and all("not VALUE-initialised" in s[0].unsupported[0] for _ in s)
    written = analyze(program(TABLE, "    MOVE 'X' TO RATE-TIER (1)\n" + SEARCH))
    assert written[0].unsupported[0] == "SEARCH over a table the program modifies"
    no_set = analyze(program(TABLE, SEARCH.replace("    SET RT-IDX TO 1\n", "")))
    assert no_set[0].unsupported[0].startswith("SEARCH without SET")
    ok = analyze(program(TABLE, SEARCH))
    assert [s.lookup["row"] for s in ok] == [1, 2, None] and not any(s.unsupported for s in ok)


def test_redefines_is_flagged_only_when_one_rule_reads_two_layouts(program):
    data = """01 REC.
   05 REC-CODE PIC X(3).
   05 FILLER REDEFINES REC-CODE.
      10 REC-KIND PIC X.
      10 FILLER PIC XX.
01 OUT-FLAG PIC X VALUE 'N'."""
    one = analyze(program(data, "    IF REC-CODE = 'ABC'\n        MOVE 'Y' TO OUT-FLAG\n    END-IF"))
    assert one[0].unsupported == []
    both = analyze(program(data, "    IF REC-CODE = 'ABC' AND REC-KIND = 'A'\n        MOVE 'Y' TO OUT-FLAG\n    END-IF"))
    assert both[0].unsupported == ["REDEFINES: REC-CODE and REC-KIND share storage (REC-CODE)"]


@pytest.mark.cobol
def test_table_rules_match_the_compiled_program(toolchain):
    from src.verify.checks import differential
    exe = ROOT / "data" / "synthetic" / "bin" / "RATETAB"
    assert toolchain.compile(RATETAB, exe).ok
    ex = extract(RATETAB)
    res, rows = differential(ex.analysis, ex.rules, exe, toolchain)
    assert res.unsimulated == 0 and res.agreement == 1.0 and len(rows) > 20
