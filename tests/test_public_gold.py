"""Parser vs hand-drafted (unverified) expected rules for IBM's SAM1/SAM2 samples."""

import sys

from src.pipeline import extract
from src.rules.match import match_rules
from tests.conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
from eval_public import SAMPLES, load   # noqa: E402


def test_drafted_rules_load_and_follow_the_schema():
    for program, n in (("SAM1", 26), ("SAM2", 19)):
        doc, gold = load(program)
        assert len(gold) == n and doc["verified"] is False
        assert all(g.provenance["source"] == "gold:hand-drafted" for g in gold)


def test_parser_matches_the_drafted_rules():
    for program in ("SAM1", "SAM2"):
        doc, gold = load(program)
        m = match_rules(extract(SAMPLES / doc["program_file"]).rules, gold)
        assert m.tp == m.n_gold == m.n_pred, (program, dict(m.failures))
