from src.analysis.analyze import analyze_program
from src.analysis.dictionary import parse_pic
from tests.conftest import FIXTURES


def test_pic_scale_and_sign():
    info = parse_pic("S9(7)V99")
    assert (info.category, info.int_digits, info.scale, info.signed, info.length) == ("numeric", 7, 2, True, 9)
    assert parse_pic("9V999").scale == 3
    assert parse_pic("X(10)").category == "alphanumeric" and parse_pic("X(10)").length == 10
    assert parse_pic("ZZ,ZZ9.99").category == "numeric-edited"
    assert parse_pic("A(5)").category == "alphabetic"


def test_dictionary_88_levels_redefines_occurs(program):
    path = program("""
01 WS-REC.
   05 TXN-DATE      PIC 9(8).
   05 TXN-DATE-R REDEFINES TXN-DATE.
      10 TXN-YYYY   PIC 9(4).
      10 TXN-MMDD   PIC 9(4).
   05 RATES         PIC 9V99 OCCURS 5 TIMES.
   *> account type code from the master file
   05 ACCT-TYPE     PIC X.
      88 ACCT-OK    VALUES 'S' 'C'.
      88 ACCT-BAD   VALUE 'X' THRU 'Z'.
   05 AMT           PIC S9(5)V99 COMP-3 VALUE ZERO.
""", "    CONTINUE")
    d = analyze_program(path).program.dictionary
    assert d.lookup("TXN-DATE-R").redefines == "TXN-DATE"
    assert d.lookup("TXN-YYYY").parent.name == "TXN-DATE-R"
    assert d.lookup("RATES").occurs == 5
    acct = d.lookup("ACCT-TYPE")
    assert acct.conditions88() == {"ACCT-OK": [["S"], ["C"]], "ACCT-BAD": [["X", "Z"]]}
    assert acct.comment == "account type code from the master file"
    amt = d.lookup("AMT")
    assert (amt.usage, amt.value, amt.picinfo.scale) == ("COMP-3", 0, 2)
    assert d.lookup("WS-REC").category == "group"


def test_slice_fields_carry_dictionary_entries():
    a = analyze_program(FIXTURES / "FIXEDPRG.cbl", [FIXTURES])
    fields = a.slices[0].fields
    assert fields["CUST-LIMIT"]["scale"] == 2
    assert fields["CUST-LIMIT"]["comment"] == "CREDIT LIMIT IN DOLLARS"
    assert fields["CUST-AGE"]["defined_in"] == {"file": "CUSTREC.cpy", "line": 2}
