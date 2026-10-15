"""Public entry point for Role A: ``analyze(program_path, copybook_dirs) -> list[Slice]``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from src.analysis.ast import Program, Stmt
from src.analysis.cfg import FlowInfo, analyze_flow
from src.analysis.lexer import tokenize
from src.analysis.parser import parse_program
from src.analysis.slicer import Slicer
from src.common.models import Slice
from src.ingest.source import Format, ResolvedSource, load_program

PARSER_VERSION = "0.2.0"


@dataclass
class Analysis:
    source: ResolvedSource
    program: Program
    slices: list[Slice]
    flow: Optional[FlowInfo] = None
    slice_stmts: list[Stmt] = field(default_factory=list)          # top-level statement of each slice
    action_stmts: list[tuple[list[Any], list[Any]]] = field(default_factory=list)  # per slice: (then, else)
    trails: list[list[tuple[Any, Any, int]]] = field(default_factory=list)  # per slice: (decision, branch, items) by level

    @property
    def diagnostics(self) -> list[str]:
        return self.program.diagnostics


def analyze_program(program_path: Path | str, copybook_dirs: Sequence[Path | str] = (),
                    fmt: Optional[Format] = None) -> Analysis:
    src = load_program(program_path, copybook_dirs, fmt)
    program = parse_program(tokenize(src.lines), src)
    slicer = Slicer(program, src)
    slices = slicer.run()
    flow = analyze_flow(program, slicer.statement_actions)
    stmts = [rec["stmt"] for rec in slicer.records]
    for sl, rec in zip(slices, slicer.records):
        ctx, _many = flow.context_of(rec["stmt"])
        derived = flow.derivations.get(id(rec["stmt"]), {})
        sl.context = ctx
        sl.derivations = {k: v for k, v in derived.items() if k in sl.fields}
        sl.performed_from = sorted(flow.performed_from.get(rec["para_idx"], set()))
        sl.reachable = flow.truncated or rec["para_idx"] in flow.reachable
    actions = [([a.get("_stmt") for a in rec["actions"]], [a.get("_stmt") for a in rec["else_actions"]])
               for rec in slicer.records]
    trails = [rec["trail"] for rec in slicer.records]
    return Analysis(src, program, slices, flow, stmts, actions, trails)


def analyze(program_path: Path | str, copybook_dirs: Sequence[Path | str] = ()) -> list[Slice]:
    return analyze_program(program_path, copybook_dirs).slices
