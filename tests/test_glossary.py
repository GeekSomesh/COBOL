"""Business glossary: authoritative field names and abbreviation expansion."""

import json

from src.llm.enrich import Enrichment, merge
from src.llm.prompts import user_prompt
from src.pipeline import extract


def test_without_a_glossary_nothing_changes(program, tmp_path, monkeypatch):
    monkeypatch.setenv("COBOL_GLOSSARY", str(tmp_path / "none.json"))
    r = extract(program("01 WS-CSCR PIC 999.\n01 DC-CD PIC X.", "    IF WS-CSCR >= 740\n        MOVE 'A' TO DC-CD\n    END-IF")).rules[0]
    assert r.model_dump(by_alias=True)["conditions"]["all"][0]["field"] == "ws_cscr"


def test_glossary_names_fields_expands_terms_and_overrides_the_llm(program, tmp_path, monkeypatch):
    g = tmp_path / "glossary.json"
    g.write_text(json.dumps({"fields": {"DC-CD": "Decision"}, "terms": {"CSCR": "credit score", "WS": "working"}}))
    monkeypatch.setenv("COBOL_GLOSSARY", str(g))
    ex = extract(program("01 WS-CSCR PIC 999.\n01 DC-CD PIC X.", "    IF WS-CSCR >= 740\n        MOVE 'A' TO DC-CD\n    END-IF"))
    r, s = ex.rules[0], ex.analysis.slices[0]
    assert r.model_dump(by_alias=True)["conditions"]["all"][0]["field"] == "working_credit_score"
    assert r.actions[0].target == "decision"
    prompt = user_prompt(s)
    assert "GLOSSARY" in prompt and "CSCR = credit score" in prompt and "DC-CD = decision (use this name)" in prompt
    e = Enrichment(title="Approve", intent="Approve scores of at least 740.",
                   field_names={"WS-CSCR": "bureau_score", "DC-CD": "outcome"}, concepts=["credit"])
    merged = merge(r, e, s, "test-model")
    assert merged.actions[0].target == "decision"                       # glossary beats the model
    assert merged.model_dump(by_alias=True)["conditions"]["all"][0]["field"] == "bureau_score"
