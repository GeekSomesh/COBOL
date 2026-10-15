"""Byte-level storage (REDEFINES / OCCURS) and differential testing of CALLed subprograms."""

from decimal import Decimal
from pathlib import Path

import pytest

from src.analysis.analyze import analyze_program
from src.pipeline import extract
from src.verify.storage import Storage
from src.verify.subprogram import build_subprogram_harness, driver_source
from tests.conftest import ROOT

SAM2 = ROOT / "data" / "public" / "ibm-zopeneditor-sample" / "SAM2.cbl"


@pytest.fixture(scope="module")
def sam2():
    return analyze_program(SAM2)


def test_layout_matches_the_copybooks(sam2):
    st = Storage(sam2.program.dictionary, roots=sam2.program.using)
    assert st.sizes == {"CUST-REC": 387, "TRANSACTION-RECORD": 80, "TRAN-OK": 1, "TRAN-MSG": 50}
    c = st.cells
    assert (c["TRAN-UPDATE-DATA"].off, c["TRAN-UPDATE-NUM"].off, c["TRAN-UPDATE-NUM-HH"].off) == (44, 44, 51)
    assert c["TRAN-UPDATE-NUM"].alias and not c["TRAN-UPDATE-DATA"].alias
    assert (c["CUST-MONTH(1)"].off, c["CUST-MONTH(12)"].off) == (84, 183)
    assert c["CRUNCH-CPU-LOOPS"].off == 14 and "TRAN-ACTION" in st.overlaps["CRUNCH-CPU-LOOPS"]


def test_writes_keep_redefines_views_in_step(sam2):
    st = Storage(sam2.program.dictionary, roots=["TRANSACTION-RECORD"])
    state = {"TRAN-UPDATE-DATA": "-00012345"}
    st.reset(state, ["TRANSACTION-RECORD"])
    assert state["TRAN-UPDATE-NUMX1"] == "-" and state["TRAN-UPDATE-NUM"] == "-00012345"   # not numeric
    state["TRAN-UPDATE-NUMX1"] = "0"                       # SAM2: MOVE '0' TO TRAN-UPDATE-NUMX1
    st.sync(state, ["TRAN-UPDATE-NUMX1"])
    assert state["TRAN-UPDATE-NUM"] == Decimal("123.45") and state["TRAN-UPDATE-NUM-HH"] == Decimal(45)
    assert state["TRAN-UPDATE-DATA"] == "000012345"


def test_signed_display_uses_trailing_overpunch():
    from src.analysis.dictionary import DataItem, Dictionary
    d = Dictionary()
    d.add(DataItem(1, "AMT", "X.cob", 1, pic="S9(3)V99"))
    st = Storage(d)
    cell = st.cells["AMT"]
    assert st.encode(cell, Decimal("-1.23")) == "0012s" and st.decode(cell, "0012s") == Decimal("-1.23")
    assert st.encode(cell, Decimal("1.23")) == "00123"


def test_harness_and_driver_for_sam2(sam2):
    h = build_subprogram_harness(sam2)
    assert h.records == ["CUST-REC", "TRANSACTION-RECORD", "TRAN-OK", "TRAN-MSG"]
    assert h.outputs == h.records                      # every parameter record is written by SAM2
    assert "CRUNCH-CPU-LOOPS" in h.loop_bounds and "TRAN-UPDATE-NUM" in h.raw_tests
    src = driver_source(sam2, h, SAM2.read_text(encoding="utf-8"))
    assert "COPY CUSTCOPY REPLACING ==:TAG:== BY ==CUST==." in src
    assert "CALL 'SAM2' USING" in src and src.count("ACCEPT ") == 4


@pytest.mark.cobol
def test_sam2_rules_match_the_compiled_subprogram(toolchain, tmp_path):
    """Real IBM code: the extracted rules reproduce SAM2 byte for byte on every parameter."""
    from src.verify.checks import differential
    from src.verify.subprogram import build_driver
    ex = extract(SAM2)
    h = build_subprogram_harness(ex.analysis)
    exe = build_driver(ex.analysis, h, SAM2, [SAM2.parent], toolchain, tmp_path)
    assert exe is not None
    res, rows = differential(ex.analysis, ex.rules, exe, toolchain, h)
    assert res.rows >= 300 and res.agreement == 1.0, res.mismatches[:2]
    # rows where SAM2 compares a loop count holding spaces are undefined in COBOL: excluded, not guessed
    assert res.notes <= {"undefined: CRUNCH-CPU-LOOPS holds non-digit bytes in a comparison"}

    # a wrong rule is caught: forget that a leading minus makes the amount negative
    import copy
    broken = copy.deepcopy(ex.rules)
    k = next(i for i, r in enumerate(broken) if any(a.source_name == "WS-UPDATE-NUM" and a.type == "compute"
                                                     for a in r.actions))
    broken[k].actions = []
    bad, _ = differential(ex.analysis, broken, exe, toolchain, h)
    assert bad.agreement < 1.0
