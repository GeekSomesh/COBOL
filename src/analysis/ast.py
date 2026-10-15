"""AST node types for the supported COBOL subset."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

from src.analysis.dictionary import Dictionary


# ---- operands -------------------------------------------------------------

@dataclass
class Ident:
    name: str                       # base name, upper case
    text: str                       # as written, incl. subscripts / qualification
    qualifier: Optional[str] = None


@dataclass
class Lit:
    value: Union[int, float, str]
    kind: Literal["num", "str", "fig"]
    text: str


@dataclass
class Expr:
    text: str                       # normalised arithmetic expression
    idents: list[str]


Operand = Union[Ident, Lit, Expr]


def operand_text(op: Operand) -> str:
    if isinstance(op, Lit) and op.kind == "str":
        return "'" + str(op.value).replace("'", "''") + "'"
    return op.text


# ---- statements -----------------------------------------------------------

@dataclass
class Stmt:
    verb: str
    file: str
    line_start: int
    line_end: int


@dataclass
class IfStmt(Stmt):
    cond: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    then: list[Stmt] = field(default_factory=list)
    else_: Optional[list[Stmt]] = None
    else_line: Optional[int] = None


@dataclass
class WhenBranch:
    cond: Optional[dict[str, Any]]  # None for WHEN OTHER
    line: int
    stmts: list[Stmt]
    notes: list[str] = field(default_factory=list)


@dataclass
class EvaluateStmt(Stmt):
    whens: list[WhenBranch] = field(default_factory=list)


@dataclass
class SearchStmt(Stmt):
    whens: list[WhenBranch] = field(default_factory=list)
    table: str = ""                         # the OCCURS item searched
    binary: bool = False                    # SEARCH ALL
    at_end: Optional[list[Stmt]] = None     # AT END statements (None when absent)
    at_end_line: int = 0


@dataclass
class MoveStmt(Stmt):
    source: Optional[Operand] = None
    targets: list[Ident] = field(default_factory=list)


@dataclass
class AssignStmt(Stmt):
    """COMPUTE / ADD / SUBTRACT / MULTIPLY / DIVIDE, as target = expression."""
    assignments: list[tuple[Ident, Expr]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class SetStmt(Stmt):
    targets: list[Ident] = field(default_factory=list)
    value: Optional[Operand] = None         # SET x TO value
    truth: Optional[bool] = None            # SET cond-name TO TRUE/FALSE
    step: Optional[tuple[str, Operand]] = None   # SET x UP/DOWN BY n


@dataclass
class PerformStmt(Stmt):
    target: Optional[str] = None            # out-of-line paragraph
    thru: Optional[str] = None
    body: Optional[list[Stmt]] = None       # inline PERFORM ... END-PERFORM
    loop: Optional[str] = None              # TIMES / UNTIL / VARYING text (display)
    until: Optional[dict[str, Any]] = None  # parsed UNTIL condition
    times: Optional[Operand] = None         # n TIMES
    varying: Optional[tuple[Ident, Operand, Operand]] = None   # VARYING x FROM a BY b
    test_after: bool = False
    nested_varying: bool = False            # VARYING ... AFTER ... (not interpreted)


@dataclass
class CallStmt(Stmt):
    program: str = ""
    dynamic: bool = False                   # True when the target is a data item, not a literal
    using: list[str] = field(default_factory=list)
    handlers: list[tuple[str, list[Stmt]]] = field(default_factory=list)


@dataclass
class GoToStmt(Stmt):
    targets: list[str] = field(default_factory=list)
    depending: Optional[str] = None         # GO TO a b c DEPENDING ON x


@dataclass
class ExecStmt(Stmt):
    kind: str = ""                          # SQL, CICS, ...
    text: str = ""                          # statement body, e.g. "SELECT RATE INTO :WS-RATE FROM ..."


@dataclass
class OtherStmt(Stmt):
    text: str = ""
    handlers: list[tuple[str, list[Stmt]]] = field(default_factory=list)


# ---- program --------------------------------------------------------------

@dataclass
class Paragraph:
    name: Optional[str]
    section: Optional[str]
    file: str
    line_start: int
    line_end: int
    statements: list[Stmt] = field(default_factory=list)


@dataclass
class Program:
    program_id: str
    paragraphs: list[Paragraph]
    dictionary: Dictionary
    diagnostics: list[str] = field(default_factory=list)
    using: list[str] = field(default_factory=list)      # PROCEDURE DIVISION USING parameters
