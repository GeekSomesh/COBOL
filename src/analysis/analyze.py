"""Public entry point for Role A: ``analyze(program_path, copybook_dirs) -> list[Slice]``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from src.analysis.ast import Program
from src.analysis.lexer import tokenize
from src.analysis.parser import parse_program
from src.analysis.slicer import Slicer
from src.common.models import Slice
from src.ingest.source import Format, ResolvedSource, load_program

PARSER_VERSION = "0.1.0"


@dataclass
class Analysis:
    source: ResolvedSource
    program: Program
    slices: list[Slice]

    @property
    def diagnostics(self) -> list[str]:
        return self.program.diagnostics


def analyze_program(program_path: Path | str, copybook_dirs: Sequence[Path | str] = (),
                    fmt: Optional[Format] = None) -> Analysis:
    src = load_program(program_path, copybook_dirs, fmt)
    program = parse_program(tokenize(src.lines), src)
    return Analysis(src, program, Slicer(program, src).run())


def analyze(program_path: Path | str, copybook_dirs: Sequence[Path | str] = ()) -> list[Slice]:
    return analyze_program(program_path, copybook_dirs).slices
