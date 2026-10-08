import pytest

from src.common.cobol import parse_display
from tests.conftest import FIXTURES, ROOT, TXNCHK

pytestmark = pytest.mark.cobol


def test_txnchk_compiles_and_matches_its_gold_rule(toolchain, tmp_path):
    exe = ROOT / "data" / "synthetic" / "bin" / "TXNCHK"
    result = toolchain.compile(TXNCHK, exe)
    assert result.ok, result.output
    # age (3 digits) + amount (9 digits, 2 implied decimals)
    cases = {"017000750000": "Y", "018000750000": "N", "017000500000": "N", "017000500001": "Y"}
    outputs = toolchain.run_many(exe, list(cases))
    assert [parse_display(o)["FLAG-REVIEW"] for o in outputs] == list(cases.values())


def test_fixtures_compile(toolchain):
    bin_dir = ROOT / "data" / "synthetic" / "bin"
    assert toolchain.compile(FIXTURES / "CONSTRUCTS.cob", bin_dir / "CONSTRUCTS").ok
    fixed = toolchain.compile(FIXTURES / "FIXEDPRG.cbl", bin_dir / "FIXEDPRG", [FIXTURES], free=False)
    assert fixed.ok, fixed.output
    assert toolchain.run_many(bin_dir / "FIXEDPRG", ["0170150000", "0180150000"]) == ["WS-FLAG=Y", "WS-FLAG=N"]
