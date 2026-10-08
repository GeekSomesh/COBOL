"""Generator <-> parser contract: for many random programs, the AST baseline
recovers exactly the gold rules, and a one-character change to the code is caught."""

import json
import random

import pytest

from src.common.models import Rule
from src.corpus.gold import gold_rules
from src.corpus.render import Renderer
from src.corpus.spec import sample_spec
from src.corpus.templates import SPLIT_OF_TEMPLATE, TEMPLATES
from src.pipeline import extract
from src.rules.match import match_rules
from tests.conftest import ROOT


def build(tmp_path, template, seed):
    r = random.Random(seed)
    spec = sample_spec(template, seed % 10000, r, seed)
    renderer = Renderer(spec, r)
    rendered = renderer.render()
    gold = gold_rules(spec, rendered.spans, lambda b, v: renderer.lit(b, v, plain=True))
    (tmp_path / spec["program_file"]).write_text(rendered.program_text, encoding="utf-8")
    if rendered.copybook_text is not None:
        (tmp_path / spec["copybook"]).write_text(rendered.copybook_text, encoding="utf-8")
    return spec, gold


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.id)
def test_baseline_recovers_gold_for_random_programs(tmp_path, template):
    for seed in range(8):                     # 8 random styles per template, 144 programs in all
        spec, gold = build(tmp_path, template, 1000 * seed + 17)
        ex = extract(tmp_path / spec["program_file"], [tmp_path])
        assert ex.analysis.diagnostics == []
        assert len(ex.analysis.slices) == len(gold), spec["program"]
        res = match_rules(ex.rules, gold)
        assert res.tp == len(gold), (spec["program"], res.examples)


def test_changed_operator_in_code_is_caught(tmp_path):
    template = next(t for t in TEMPLATES if t.id == "fraud_minor_high_value")
    spec, gold = build(tmp_path, template, 4242)
    path = tmp_path / spec["program_file"]
    text = path.read_text()
    age = next(i["name"] for i in spec["inputs"] if i["business"] == "customer_age")
    assert f"{age} < " in text
    path.write_text(text.replace(f"{age} < ", f"{age} <= ", 1))
    res = match_rules(extract(path, [tmp_path]).rules, gold)
    assert res.tp == len(gold) - 1 and res.failures["condition mismatch"] == 1


def test_splits_are_by_template_and_disjoint():
    splits = json.loads((ROOT / "data" / "splits.json").read_text())
    seen = {}
    for split in ("train", "val", "test"):
        for prog in splits[split]:
            doc = json.loads((ROOT / "data" / "gold" / f"{prog}.json").read_text())
            assert SPLIT_OF_TEMPLATE[doc["template"]] == split
            seen.setdefault(doc["template"], set()).add(split)
    assert all(len(s) == 1 for s in seen.values())
    assert set(splits["templates"]["test"]).isdisjoint(splits["templates"]["train"])


def test_gold_files_validate_and_have_enrichment_targets():
    for path in (ROOT / "data" / "gold").glob("*.json"):
        doc = json.loads(path.read_text())
        for r in doc["rules"]:
            rule = Rule.model_validate(r)
            assert rule.title and rule.intent and 1 <= len(rule.concepts) <= 5, rule.rule_id
