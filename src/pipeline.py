"""End-to-end extraction. Phase 3: parse, slice, AST baseline rule.

Later phases slot in here: ``enrich`` (LLM) and ``verify`` (symbolic +
differential checks, confidence).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from src.analysis.analyze import Analysis, analyze_program
from src.common.models import Rule
from src.rules.baseline import baseline_rule


@dataclass
class Extraction:
    analysis: Analysis
    rules: list[Rule]


def extract(program_path: Path | str, copybook_dirs: Sequence[Path | str] = ()) -> Extraction:
    analysis = analyze_program(program_path, copybook_dirs)
    rules = [baseline_rule(s, analysis.source.sha256) for s in analysis.slices]
    return Extraction(analysis, rules)
