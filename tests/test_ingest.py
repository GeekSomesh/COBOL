from src.analysis.lexer import tokenize
from src.ingest.source import _apply_replacing, detect_format, load_program
from tests.conftest import FIXTURES, TXNCHK


def test_detects_free_and_fixed_format():
    assert detect_format(TXNCHK.read_text().splitlines()) == "free"
    assert detect_format((FIXTURES / "FIXEDPRG.cbl").read_text().splitlines()) == "fixed"


def test_copy_replacing_keeps_copybook_origin():
    src = load_program(FIXTURES / "FIXEDPRG.cbl", [FIXTURES])
    assert src.copybooks == ["CUSTREC.cpy"]
    age = next(l for l in src.lines if "CUST-AGE" in l.text and "PIC" in l.text)
    assert (age.file, age.line) == ("CUSTREC.cpy", 2)
    limit = next(l for l in src.lines if "CUST-LIMIT" in l.text and "PIC" in l.text)
    assert (limit.file, limit.line) == ("CUSTREC.cpy", 4)
    # the program's own lines keep their numbers after the inlined copybook
    if_line = next(l for l in src.lines if l.text.strip().startswith("IF CUST-AGE"))
    assert (if_line.file, if_line.line) == ("FIXEDPRG.cbl", 13)


def test_fixed_format_strips_sequence_area_and_comment_lines():
    src = load_program(FIXTURES / "FIXEDPRG.cbl", [FIXTURES])
    comment = next(l for l in src.lines if l.file == "FIXEDPRG.cbl" and l.line == 12)
    assert comment.text == "" and comment.comment == "MINORS WITH A HIGH LIMIT ARE REVIEWED"
    assert all(not l.text.lstrip()[:6].isdigit() for l in src.lines if l.text.strip())


def test_inline_comment_outside_literal_only():
    src_text = "IDENTIFICATION DIVISION.\nPROGRAM-ID. X.\n    DISPLAY '*> not a comment' *> real comment\n"
    from src.ingest.source import _normalise_lines
    lines = _normalise_lines("X.cob", src_text.splitlines(), "free")
    assert lines[2].text.strip() == "DISPLAY '*> not a comment'"
    assert lines[2].comment == "real comment"


def test_word_replacing_respects_word_boundaries():
    out = _apply_replacing("05 AMT PIC 9. 05 AMT-X PIC 9.", [("AMT", "TXN-AMT", False)])
    assert out == "05 TXN-AMT PIC 9. 05 AMT-X PIC 9."


def test_lexer_numbers_periods_and_pic():
    src = load_program(TXNCHK)
    toks = tokenize(src.lines)
    pics = [t.value for t in toks if t.kind == "PIC"]
    assert pics == ["9(3)", "9(7)V99", "X"]
    values = [(t.kind, t.value) for t in toks if t.line == 16]
    assert values == [("WORD", "IF"), ("WORD", "CUST-AGE"), ("OP", "<"), ("NUM", "18"),
                      ("WORD", "AND"), ("WORD", "TXN-AMOUNT"), ("OP", ">"), ("NUM", "5000")]
    assert [t.kind for t in toks if t.line == 18] == ["WORD", "PERIOD"]
