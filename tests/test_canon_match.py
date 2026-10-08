import copy
import json

from src.common.models import Rule
from src.rules.canon import canon_action, canon_cond
from src.rules.match import match_rules
from tests.conftest import ROOT


def L(name, op, value=None):
    leaf = {"field": name.lower(), "source_name": name, "op": op}
    if value is not None:
        leaf["value"] = value
    return leaf


def test_set_and_range_operators_expand():
    assert canon_cond({"all": [L("T", "in", ["P", "G"])]}) == canon_cond({"any": [L("T", "==", "G"), L("T", "==", "P")]})
    assert canon_cond({"all": [L("A", "between", [1, 6]), L("B", ">", 2)]}) == \
        canon_cond({"all": [L("B", ">", 2), L("A", ">=", 1), L("A", "<=", 6)]})


def test_negation_is_pushed_to_leaves():
    assert canon_cond({"not": L("AGE", "<", 18)}) == canon_cond({"all": [L("AGE", ">=", 18)]})
    assert canon_cond({"not": {"all": [L("A", "==", 1), L("B", ">", 2)]}}) == \
        canon_cond({"any": [L("A", "!=", 1), L("B", "<=", 2)]})
    assert canon_cond({"not": L("X", "is_numeric")}) != canon_cond({"all": [L("X", "is_numeric")]})


def test_literal_normalisation():
    assert canon_cond({"all": [L("AMT", ">", 5000)]}) == canon_cond({"all": [L("AMT", ">", 5000.00)]})
    assert canon_cond({"all": [L("F", "==", "Y ")]}) == canon_cond({"all": [L("F", "==", "Y")]})
    assert canon_action({"type": "compute", "source_name": "FEE", "expr": "AMT  * 0.02"}) == \
        canon_action({"type": "compute", "source_name": "FEE", "expr": "amt*0.02"})


def test_different_logic_is_not_equal():
    base = canon_cond({"all": [L("AGE", "<", 18), L("AMT", ">", 5000)]})
    assert base != canon_cond({"all": [L("AGE", "<=", 18), L("AMT", ">", 5000)]})
    assert base != canon_cond({"all": [L("AGE", "<", 18), L("AMT", ">", 5001)]})
    assert base != canon_cond({"any": [L("AGE", "<", 18), L("AMT", ">", 5000)]})


def _txnchk_gold():
    doc = json.loads((ROOT / "data" / "gold" / "TXNCHK.json").read_text())
    return Rule.model_validate(doc["rules"][0])


def test_broken_rule_is_caught():
    gold = _txnchk_gold()
    assert match_rules([gold], [gold]).tp == 1

    wrong_threshold = copy.deepcopy(gold)
    wrong_threshold.conditions.all[1].value = 4000
    res = match_rules([wrong_threshold], [gold])
    assert res.tp == 0 and res.failures["condition mismatch"] == 1

    wrong_action = copy.deepcopy(gold)
    wrong_action.actions[0].value = "N"
    assert match_rules([wrong_action], [gold]).failures["action mismatch"] == 1

    shifted = copy.deepcopy(gold)
    shifted.trace.line_start, shifted.trace.line_end = 30, 33
    res = match_rules([shifted], [gold])
    assert res.tp == 0 and res.logic_tp == 1 and res.failures["trace mismatch"] == 1


def test_extra_and_missing_predictions_count():
    gold = _txnchk_gold()
    res = match_rules([gold, gold], [gold])
    assert (res.tp, res.unmatched_pred, round(res.precision, 2), res.recall) == (1, 1, 0.5, 1.0)
    assert match_rules([], [gold]).failures["missing rule"] == 1
