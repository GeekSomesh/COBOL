from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "cobol"
TXNCHK = ROOT / "data" / "synthetic" / "TXNCHK.cob"


@pytest.fixture(scope="session")
def toolchain():
    from src.common.cobol import Toolchain, ToolchainError
    try:
        return Toolchain.detect()
    except ToolchainError as exc:
        pytest.skip(f"GnuCOBOL not available: {exc}")


@pytest.fixture
def program(tmp_path):
    """Write a small free-format program around the given DATA and PROCEDURE text."""
    def make(data: str, procedure: str, name: str = "SNIPPET") -> Path:
        text = (f"IDENTIFICATION DIVISION.\nPROGRAM-ID. {name}.\nDATA DIVISION.\n"
                f"WORKING-STORAGE SECTION.\n{data.strip()}\nPROCEDURE DIVISION.\nMAIN-PARA.\n"
                f"{procedure.rstrip()}\n    STOP RUN.\n")
        path = tmp_path / f"{name}.cob"
        path.write_text(text, encoding="utf-8")
        return path
    return make
