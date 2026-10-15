"""Confidence score and review routing (rules.md section 5).

score = 0.40 structural + 0.30 differential + 0.20 consistency + 0.10 naming

When a component cannot be measured (no runnable program for differential
testing, no LLM for consistency), its weight is spread over the measured ones
and the component is recorded as null. Routing: candidate only with
score >= 0.90, structural == 1.0 and measured behavioural agreement >= 0.95,
and never for unsupported constructs or external dependencies; everything else
goes to review. An intent that cites a number absent from the code also goes to
review, and so does a rule whose condition the SMT solver proves can never be
true, and an intent whose comparison words contradict the rule ("over 740" for >= 740). Only a reviewer can set ``approved``.
"""

from __future__ import annotations

from typing import Any, Optional

from src.common.models import Rule

WEIGHTS = {"structural": 0.40, "differential": 0.30, "consistency": 0.20, "naming": 0.10}
CANDIDATE_MIN = 0.90
REVIEW_MIN = 0.70
DIFFERENTIAL_MIN = 0.95      # behavioural agreement target (requirement NFR-03)


def combine(components: dict[str, Optional[float]]) -> float:
    measured = {k: v for k, v in components.items() if v is not None and k in WEIGHTS}
    total_w = sum(WEIGHTS[k] for k in measured)
    if not total_w:
        return 0.0
    return round(sum(WEIGHTS[k] * v for k, v in measured.items()) / total_w, 4)


def route(rule: Rule, score: float, components: dict[str, Optional[float]],
          extra_flags: Optional[dict[str, Any]] = None) -> tuple[str, list[str]]:
    extra_flags = extra_flags or {}
    warnings: list[str] = []
    if rule.unsupported:
        warnings.append("unsupported construct: " + ", ".join(rule.unsupported))
    if rule.external_dependency:
        warnings.append("external dependency (CALL/EXEC): behaviour not fully visible")
    if not rule.reachable:
        warnings.append("unreachable paragraph (dead code): no path from the program entry runs this rule")
    if rule.provenance.get("enrichment_error"):
        warnings.append("LLM enrichment failed; baseline names only")
    if components.get("structural") != 1.0:
        warnings.append("rule differs from the parsed source condition")
    diff = components.get("differential")
    if diff is None:
        warnings.append("not behaviourally verified (no runnable harness: ACCEPT/DISPLAY program or CALLed subprogram)")
    elif diff < 1.0:
        warnings.append("disagrees with compiled COBOL on some inputs")
    if score < REVIEW_MIN:
        warnings.append(f"low confidence ({score:.2f})")
    elif score < CANDIDATE_MIN:
        weakest = min(((k, v) for k, v in components.items() if v is not None), key=lambda kv: kv[1], default=None)
        warnings.append(f"confidence below {CANDIDATE_MIN:.2f} ({score:.2f}; weakest: {weakest[0]} {weakest[1]:.2f})"
                        if weakest else f"confidence below {CANDIDATE_MIN:.2f} ({score:.2f})")
    ungrounded = extra_flags.get("intent_grounded") is False
    if ungrounded:
        warnings.append("intent mentions a number that is not in the code")
    wording = extra_flags.get("intent_comparisons") or []
    warnings.extend(wording)              # e.g. intent says "over 740" but the rule tests >= 740
    impossible = extra_flags.get("satisfiable") is False
    if impossible:
        warnings.append("condition can never be true (proven by the SMT solver): the rule never fires")
    ok = (score >= CANDIDATE_MIN and components.get("structural") == 1.0 and not ungrounded and not impossible
          and not wording
          and diff is not None and diff >= DIFFERENTIAL_MIN
          and not rule.unsupported and not rule.external_dependency and rule.reachable
          and not rule.provenance.get("enrichment_error"))
    return ("candidate" if ok else "needs_review"), warnings


_CALIBRATION: Optional[list[list[float]]] = None


def p_correct(score: float) -> Optional[float]:
    """Calibrated probability that a rule with this score is correct (data/calibration.json,
    fitted on fault-injection cases by ``scripts/evaluate.py calibrate``); None if not fitted."""
    global _CALIBRATION
    if _CALIBRATION is None:
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parents[2] / "data" / "calibration.json"
        _CALIBRATION = json.loads(path.read_text(encoding="utf-8"))["points"] if path.exists() else []
    pts = _CALIBRATION
    if not pts:
        return None
    if score <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if score <= x1:
            return round(y0 + (y1 - y0) * (score - x0) / (x1 - x0), 4) if x1 > x0 else y1
    return pts[-1][1]


def apply_confidence(rule: Rule, components: dict[str, Optional[float]], extra: Optional[dict[str, Any]] = None) -> Rule:
    score = combine(components)
    status, warnings = route(rule, score, components, extra)
    out = rule.model_copy(deep=True)
    out.confidence = {"score": score, "components": components, "warnings": warnings, **(extra or {})}
    p = p_correct(score)
    if p is not None:
        out.confidence["p_correct"] = p
    if out.status not in ("approved", "rejected", "superseded"):
        out.status = status
    return out
