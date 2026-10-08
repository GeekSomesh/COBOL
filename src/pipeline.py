"""End-to-end extraction: parse -> slice -> AST rule -> LLM enrichment -> verification -> confidence.

``extract`` is the AST-only baseline. ``run_pipeline`` adds the optional LLM
stage and the trust stage (symbolic check, differential test against the
compiled program, self-consistency, naming clarity, confidence routing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from src.analysis.analyze import Analysis, analyze_program
from src.common.models import Rule
from src.rules.baseline import baseline_rule

ROOT = Path(__file__).resolve().parent.parent
Progress = Callable[[str, int, int], None]


@dataclass
class Extraction:
    analysis: Analysis
    rules: list[Rule]
    report: dict[str, Any] = field(default_factory=dict)


def defaults(ex: Extraction) -> dict[str, Any]:
    """Initial VALUE of every field the rules write: the decision when no rule fires (rules.md 3.9)."""
    d = ex.analysis.program.dictionary
    out: dict[str, Any] = {}
    for s in ex.analysis.slices:
        for a in s.actions + s.else_actions:
            item = d.lookup(a.get("source_name", "")) if a["type"] in ("set", "compute") else None
            if item is not None and item.value is not None:
                v = item.value
                out[item.name] = "" if v == " " else v
            elif item is not None and item.category == "numeric":
                out[item.name] = 0
    return out


def extract(program_path: Path | str, copybook_dirs: Sequence[Path | str] = ()) -> Extraction:
    analysis = analyze_program(program_path, copybook_dirs)
    rules = [baseline_rule(s, analysis.source.sha256) for s in analysis.slices]
    return Extraction(analysis, rules)


@dataclass
class PipelineConfig:
    model: Optional[str] = None          # Ollama model for enrichment; None = AST-only names
    fewshot: int = 0                     # few-shot examples (prompted base models); 0 for the fine-tuned model
    consistency_runs: int = 0            # extra sampled enrichments for self-consistency; 0 = not measured
    verify: bool = True
    differential: bool = True
    exe: Optional[Path] = None           # prebuilt executable (else compiled on demand when possible)
    cache_dir: Path = ROOT / "data" / "cache"


def _compile(program_path: Path, analysis: Analysis, copy_dirs: Sequence[Path | str], toolchain, cache: Path) -> Optional[Path]:
    exe = cache / "bin" / f"{analysis.program.program_id}-{analysis.source.sha256[7:19]}"
    if exe.exists():
        return exe
    dirs = [Path(d) for d in copy_dirs] or [Path(program_path).parent]
    result = toolchain.compile(Path(program_path), exe, dirs, free=analysis.source.fmt == "free")
    return exe if result.ok else None


def run_pipeline(program_path: Path | str, copybook_dirs: Sequence[Path | str] = (),
                 cfg: Optional[PipelineConfig] = None, toolchain=None, client=None,
                 progress: Optional[Progress] = None) -> Extraction:
    from src.llm.enrich import enrich
    from src.verify.checks import (consistency_score, differential, field_harness, naming_score,
                                   structural)
    from src.verify.confidence import apply_confidence
    from src.verify.harness import build_grid, build_harness

    cfg = cfg or PipelineConfig()
    tick = progress or (lambda stage, done, total: None)
    ex = extract(program_path, copybook_dirs)
    slices, rules = ex.analysis.slices, list(ex.rules)
    report: dict[str, Any] = {"program": ex.analysis.program.program_id, "slices": len(slices),
                              "diagnostics": ex.analysis.diagnostics, "defaults": defaults(ex)}
    tick("parse", len(slices), len(slices))

    enrichments: list[Any] = [None] * len(rules)
    if cfg.model and client is not None:
        from src.llm.fewshot import fewshot_examples
        examples = fewshot_examples(cfg.fewshot)
        for k, s in enumerate(slices):
            rules[k], enrichments[k] = enrich(s, rules[k], client, examples)
            tick("enrich", k + 1, len(slices))
        report["enriched"] = sum(e is not None for e in enrichments)

    if not cfg.verify:
        return Extraction(ex.analysis, rules, report)

    harness = build_harness(ex.analysis)
    diff = None
    if cfg.differential and harness is not None and toolchain is not None:
        exe = cfg.exe or _compile(Path(program_path), ex.analysis, copybook_dirs, toolchain, cfg.cache_dir)
        if exe is not None:
            diff, rows = differential(ex.analysis, rules, exe, toolchain, harness)
            report["differential"] = {"rows": diff.rows, "agreement": round(diff.agreement, 4),
                                      "mismatches": diff.mismatches}
    tick("differential", 1, 1)

    if harness is not None:
        grid_all = build_grid(harness, [s.condition for s in slices]) if slices else []
    out: list[Rule] = []
    for k, (s, r) in enumerate(zip(slices, rules)):
        grid = grid_all if harness is not None else build_grid(field_harness(s.fields), [s.condition])
        comps: dict[str, Optional[float]] = {
            "structural": structural(r, s, grid),
            "differential": None,
            "consistency": None,
            "naming": naming_score(r, s),
        }
        extra: dict[str, Any] = {}
        if diff is not None:
            per_rule = diff.rule_agreement(k)
            comps["differential"] = round(per_rule if per_rule is not None else diff.agreement, 4)
            extra["differential_rows"] = len(diff.per_rule.get(k, []))
        if cfg.consistency_runs and cfg.model and client is not None:
            from src.llm.fewshot import fewshot_examples
            samples = [enrichments[k]] + [
                enrich(s, ex.rules[k], client, fewshot_examples(cfg.fewshot), retries=0,
                       temperature=0.7, seed=100 + n)[1] for n in range(cfg.consistency_runs)]
            comps["consistency"] = consistency_score(samples)
        if r.intent:
            from src.llm.evaluation import grounded
            extra["intent_grounded"] = grounded(r.intent, s)
        out.append(apply_confidence(r, comps, extra))
        tick("verify", k + 1, len(slices))
    return Extraction(ex.analysis, out, report)
