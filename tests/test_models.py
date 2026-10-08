import json

import pytest
from pydantic import ValidationError

from src.common.models import Cond, Rule, dump
from tests.conftest import ROOT


def test_gold_txnchk_validates_and_round_trips():
    gold = json.loads((ROOT / "data" / "gold" / "TXNCHK.json").read_text())
    rule = Rule.model_validate(gold["rules"][0])
    assert rule.rule_id == "TXNCHK-R001"
    assert rule.trace.line_start == 16 and rule.trace.line_end == 18
    again = Rule.model_validate(dump(rule))
    assert again == rule


def test_not_uses_schema_alias():
    cond = Cond.model_validate({"not": {"field": "a", "source_name": "A", "op": "<", "value": 1}})
    assert dump(cond) == {"not": {"field": "a", "source_name": "A", "op": "<", "value": 1}}


def test_cond_requires_exactly_one_key():
    with pytest.raises(ValidationError):
        Cond.model_validate({})
    with pytest.raises(ValidationError):
        Cond.model_validate({"all": [], "any": []})
    assert Cond.model_validate({"all": []}).all == []


def test_leaf_rejects_unknown_operator():
    with pytest.raises(ValidationError):
        Cond.model_validate({"all": [{"field": "a", "source_name": "A", "op": "~", "value": 1}]})
