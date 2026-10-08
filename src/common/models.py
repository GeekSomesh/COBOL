"""Frozen interfaces shared by every pipeline stage (docs/implementation.md, section 1.4).

Changes from the draft in implementation.md, made when freezing, so the models
match the canonical JSON in docs/rules.md:

- ``Cond.not_`` is serialised as ``"not"`` (alias) and a Cond must set exactly
  one of ``all`` / ``any`` / ``not``. An empty ``all`` means "always true".
- ``Leaf.ref`` supports field-to-field comparisons (``IF BAL > LIMIT``); then
  ``value`` is None.
- ``Rule`` gains ``version`` and ``unsupported``; ``Trace`` gains ``file`` for
  rules whose lines live in a copybook.
- ``Slice`` gains ``unsupported``, ``external_dependency``, ``copybooks`` and
  ``program_file`` so the baseline extractor needs nothing else.

Serialise with :func:`dump` so aliases are used and empty optionals are dropped.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Op = Literal[
    "==", "!=", "<", "<=", ">", ">=", "in", "not_in", "between",
    "is_numeric", "is_alphabetic", "is_positive", "is_negative", "is_zero",
]
Scalar = Union[int, float, str]
Status = Literal["candidate", "needs_review", "approved", "rejected", "superseded"]


class Leaf(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str                      # business name (snake_case)
    source_name: str                # COBOL name
    op: Op
    value: Optional[Union[Scalar, list[Any]]] = None
    ref: Optional[str] = None       # COBOL name of the right-hand field, if not a literal


class Cond(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    all: Optional[list[Union[Cond, Leaf]]] = None
    any: Optional[list[Union[Cond, Leaf]]] = None
    not_: Optional[Union[Cond, Leaf]] = Field(default=None, alias="not")

    @model_validator(mode="after")
    def _exactly_one(self) -> Cond:
        present = [k for k in ("all", "any", "not_") if getattr(self, k) is not None]
        if len(present) != 1:
            raise ValueError(f"Cond needs exactly one of all/any/not, got {present or 'none'}")
        return self


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["set", "compute", "perform", "call"]
    target: Optional[str] = None        # business name of the target field / paragraph / program
    source_name: Optional[str] = None   # COBOL name of the same
    value: Optional[Scalar] = None      # literal for "set"
    expr: Optional[str] = None          # COBOL expression for "compute" (or identifier for "set")


class Trace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program: str                    # program file name, e.g. TXNCHK.cob
    file: Optional[str] = None      # file holding the lines when it is not the program (a copybook)
    paragraph: Optional[str] = None
    line_start: int
    line_end: int
    copybooks: list[str] = []


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    version: int = 1
    title: str = ""
    intent: str = ""
    domain: str = ""
    conditions: Cond
    actions: list[Action]
    else_actions: list[Action] = []
    trace: Trace
    concepts: list[str] = []
    external_dependency: bool = False
    unsupported: list[str] = []     # constructs the slicer could not model, e.g. ["GO TO"]
    confidence: dict[str, Any] = {}
    status: Status = "needs_review"
    provenance: dict[str, Any] = {}


class Slice(BaseModel):
    """One decision branch cut out of a program, before any naming or LLM work.

    ``condition`` has the same shape as :class:`Cond` but its leaves carry only
    COBOL names: ``{"source_name", "op", "value"}`` (or ``"ref"``). Literal
    values are typed by how they were written; the baseline coerces them by PIC.
    ``actions`` entries look like :class:`Action` without business names.
    """

    model_config = ConfigDict(extra="forbid")

    slice_id: str
    program: str                    # PROGRAM-ID
    program_file: str               # file name of the program
    source_file: str                # file holding the lines (program or copybook)
    paragraph: Optional[str]
    line_start: int
    line_end: int
    source_text: str
    condition: dict[str, Any]
    actions: list[dict[str, Any]]
    else_actions: list[dict[str, Any]] = []
    fields: dict[str, dict[str, Any]]   # source_name -> {"pic", "usage", "values88", ...}
    copybooks: list[str] = []
    external_dependency: bool = False
    unsupported: list[str] = []


Cond.model_rebuild()
Rule.model_rebuild()


def dump(model: BaseModel) -> dict[str, Any]:
    """JSON-ready dict using schema aliases (``not``) and without null optionals."""
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)
