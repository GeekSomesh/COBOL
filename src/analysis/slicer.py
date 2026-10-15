"""Decision slicer: one Slice per decision branch (implementation.md 2.2 steps 5-7).

Conventions (shared with the generator's gold rules, documented in docs/rules.md 2.1):

* A top-level IF whose branches contain no further decisions is ONE slice with
  ``actions`` (THEN) and ``else_actions`` (ELSE). Lines: the whole IF statement.
* Anything else is flattened into leaf paths. Each branch that performs actions
  becomes a slice whose condition is ``all`` of the path; ELSE adds ``not`` of
  the IF condition. Lines: from the branch's opening keyword (IF / ELSE / WHEN)
  to its last statement.
* EVALUATE and ELSE-IF chains keep first-match semantics: branch *i* adds
  ``not`` of every earlier branch condition. WHEN OTHER is ``all`` of those nots.
* Constructs the slicer cannot model (EXEC, SEARCH, inline PERFORM loops,
  GO TO DEPENDING ON, REDEFINES overlaps) are listed in ``unsupported`` instead of
  failing. GO TO and STOP RUN inside a branch become ``goto`` / ``stop`` actions;
  the control-flow layer (src/analysis/cfg.py) models where they lead.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.analysis.ast import (
    AssignStmt, CallStmt, EvaluateStmt, ExecStmt, Expr, GoToStmt, Ident, IfStmt, Lit,
    MoveStmt, OtherStmt, PerformStmt, Program, SearchStmt, SetStmt, Stmt,
)
from src.common.models import Slice
from src.ingest.source import ResolvedSource

DECISIONS = (IfStmt, EvaluateStmt)
_WORD_RE = re.compile(r"[A-Z0-9][A-Z0-9_-]*")
SUPPORTED_NOTES = {"arithmetic expression in condition"}     # the evaluator computes these now


def _notes(notes: list[str]) -> list[str]:
    return [n for n in notes if n not in SUPPORTED_NOTES]


def negate(cond: dict[str, Any]) -> dict[str, Any]:
    return cond["not"] if set(cond) == {"not"} else {"not": cond}


def combine(path: list[dict[str, Any]]) -> dict[str, Any]:
    """AND a path of conditions into one tree; a single leaf is wrapped in ``all``."""
    if len(path) == 1:
        c = path[0]
        return c if set(c) in ({"all"}, {"any"}, {"not"}) else {"all": [c]}
    flat: list[dict[str, Any]] = []
    for c in path:
        flat.extend(c["all"] if set(c) == {"all"} else [c])
    return {"all": flat}


def _has_decision(stmts: list[Stmt]) -> bool:
    for s in stmts:
        if isinstance(s, DECISIONS):
            return True
        if isinstance(s, PerformStmt) and s.body and _has_decision(s.body):
            return True
    return False


class Slicer:
    def __init__(self, program: Program, src: ResolvedSource):
        self.program = program
        self.src = src
        self.dict = program.dictionary
        self.records: list[dict[str, Any]] = []
        self.paragraph: Optional[str] = None
        self.stmt: Optional[Stmt] = None            # top-level statement the current rules come from
        self.redefined = {i.redefines for i in self.dict.items if i.redefines}

    # ---- walking ----------------------------------------------------------
    def run(self) -> list[Slice]:
        for i, para in enumerate(self.program.paragraphs):
            self.paragraph = para.name
            self.para_idx = i
            self._walk_top(para.statements)
        return [self._to_slice(n, rec) for n, rec in enumerate(self.records, start=1)]

    def statement_actions(self, s: Stmt) -> list[dict[str, Any]]:
        """Actions of one statement (used by the control-flow layer for assignments)."""
        return self._actions([s])[0]

    def _walk_top(self, stmts: list[Stmt]) -> None:
        for s in stmts:
            self.stmt = s
            if isinstance(s, DECISIONS):
                self._decision(s, [])
            elif isinstance(s, PerformStmt) and s.body:
                self._walk_top(s.body)
            elif isinstance(s, SearchStmt):
                for w in s.whens:
                    acts, unsupported, external = self._actions(w.stmts)
                    self._emit(w.cond or {"all": []}, acts, [], w.line,
                               max([w.line] + [x.line_end for x in w.stmts]), s.file,
                               ["SEARCH"] + _notes(w.notes) + unsupported, external)

    def _decision(self, s: Stmt, path: list[dict[str, Any]]) -> None:
        if isinstance(s, IfStmt):
            else_ = s.else_ or []
            if not path and not _has_decision(s.then) and not _has_decision(else_):
                acts, u1, e1 = self._actions(s.then)
                else_acts, u2, e2 = self._actions(else_)
                if acts or else_acts or u1 or u2:    # an IF that only does I/O is not a rule
                    self._emit(combine([s.cond]), acts, else_acts, s.line_start, s.line_end, s.file,
                               _notes(s.notes) + u1 + u2, e1 or e2)
                return
            self._branch(s.then, path + [s.cond], s.line_start, s.file, _notes(s.notes))
            if s.else_ is not None:
                self._branch(s.else_, path + [negate(s.cond)], s.else_line or s.line_start, s.file, _notes(s.notes))
        elif isinstance(s, EvaluateStmt):
            earlier: list[dict[str, Any]] = []
            for w in s.whens:
                conds = [negate(c) for c in earlier]
                if w.cond is not None:
                    conds.append(w.cond)
                    earlier.append(w.cond)
                self._branch(w.stmts, path + conds, w.line, s.file, _notes(w.notes))

    def _branch(self, stmts: list[Stmt], path: list[dict[str, Any]], open_line: int,
                file: str, notes: list[str]) -> None:
        direct = [s for s in stmts if not isinstance(s, DECISIONS)]
        acts, unsupported, external = self._actions(direct)
        if acts or unsupported:
            end = max([open_line] + [s.line_end for s in stmts])
            self._emit(combine(path) if path else {"all": []}, acts, [], open_line, end, file,
                       notes + unsupported, external)
        for s in stmts:
            if isinstance(s, DECISIONS):
                self._decision(s, path)
            elif isinstance(s, PerformStmt) and s.body:
                for inner in s.body:
                    if isinstance(inner, DECISIONS):
                        self._decision(inner, path)

    # ---- actions ------------------------------------------------------------
    def _actions(self, stmts: list[Stmt]) -> tuple[list[dict[str, Any]], list[str], bool]:
        acts: list[dict[str, Any]] = []
        unsupported: list[str] = []
        external = False
        for s in stmts:
            first = len(acts)
            if isinstance(s, MoveStmt):
                for tgt in s.targets:
                    if isinstance(s.source, Lit):
                        acts.append({"type": "set", "source_name": tgt.text, "value": s.source.value})
                    elif isinstance(s.source, Ident):
                        acts.append({"type": "set", "source_name": tgt.text, "expr": s.source.text})
                    elif isinstance(s.source, Expr):
                        acts.append({"type": "compute", "source_name": tgt.text, "expr": s.source.text})
            elif isinstance(s, AssignStmt):
                unsupported.extend(s.notes)
                for tgt, expr in s.assignments:
                    acts.append({"type": "compute", "source_name": tgt.text, "expr": expr.text})
            elif isinstance(s, SetStmt):
                acts.extend(self._set_actions(s, unsupported))
            elif isinstance(s, PerformStmt):
                if s.body is not None:
                    unsupported.append("inline PERFORM loop")
                    inner, u, e = self._actions([x for x in s.body if not isinstance(x, DECISIONS)])
                    acts.extend(inner)
                    unsupported.extend(u)
                    external = external or e
                else:
                    target = s.target + (f" THRU {s.thru}" if s.thru else "")
                    acts.append({"type": "perform", "source_name": target})
            elif isinstance(s, CallStmt):
                acts.append({"type": "call", "source_name": s.program})
                external = True
            elif isinstance(s, GoToStmt):
                if s.depending or len(s.targets) != 1:
                    unsupported.append("GO TO DEPENDING ON " + (s.depending or ""))
                else:
                    acts.append({"type": "goto", "source_name": s.targets[0]})
            elif isinstance(s, OtherStmt) and (s.verb in ("STOP RUN", "GOBACK") or s.text == "EXIT PROGRAM"):
                acts.append({"type": "stop", "source_name": s.text})
            elif isinstance(s, ExecStmt):
                acts.append({"type": "call", "source_name": f"EXEC {s.kind}", "expr": s.text[:500]})
                unsupported.append(f"EXEC {s.kind}")
                external = True
            elif isinstance(s, SearchStmt):
                unsupported.append("SEARCH")
            elif isinstance(s, OtherStmt) and s.verb == "ALTER":
                unsupported.append("ALTER")
            for a in acts[first:]:
                a.setdefault("_stmt", s)          # source statement, kept out of the Slice (see _to_slice)
        return acts, unsupported, external

    def _set_actions(self, s: SetStmt, unsupported: list[str]) -> list[dict[str, Any]]:
        acts: list[dict[str, Any]] = []
        for tgt in s.targets:
            item = self.dict.lookup(tgt.name)
            if s.truth is not None:
                if item is not None and item.is_condition and s.truth and item.values88:
                    parent = item.parent.name if item.parent is not None else item.name
                    acts.append({"type": "set", "source_name": parent, "value": item.values88[0][0]})
                else:
                    unsupported.append(f"SET {tgt.name} TO {'TRUE' if s.truth else 'FALSE'}")
            elif s.value is not None:
                if isinstance(s.value, Lit):
                    acts.append({"type": "set", "source_name": tgt.text, "value": s.value.value})
                else:
                    acts.append({"type": "set", "source_name": tgt.text, "expr": s.value.text})
            elif s.step is not None:
                sign = "+" if s.step[0] == "UP" else "-"
                step = s.step[1]
                text = str(step.value) if isinstance(step, Lit) else step.text
                acts.append({"type": "compute", "source_name": tgt.text, "expr": f"{tgt.text} {sign} {text}"})
        return acts

    # ---- output -------------------------------------------------------------
    def _emit(self, cond: dict[str, Any], acts: list, else_acts: list, start: int, end: int,
              file: str, unsupported: list[str], external: bool) -> None:
        self.records.append(dict(cond=cond, actions=acts, else_actions=else_acts, start=start,
                                 end=end, file=file, paragraph=self.paragraph, stmt=self.stmt,
                                 para_idx=getattr(self, "para_idx", 0),
                                 unsupported=list(dict.fromkeys(unsupported)), external=external))

    def _names(self, rec: dict[str, Any]) -> list[str]:
        names: list[str] = []

        def walk(c: dict[str, Any]) -> None:
            for key in ("all", "any"):
                if key in c:
                    for x in c[key]:
                        walk(x)
            if "not" in c:
                walk(c["not"])
            if "source_name" in c:
                names.append(c["source_name"])
                if c.get("ref"):
                    names.append(c["ref"])

        walk(rec["cond"])
        for a in rec["actions"] + rec["else_actions"]:
            if a["type"] in ("set", "compute"):
                names.append(a["source_name"])
            if a.get("expr"):
                names.extend(_WORD_RE.findall(a["expr"].upper()))
        seen: list[str] = []
        for n in names:
            base = n.split("(")[0].strip()
            if base not in seen and base in self.dict:
                seen.append(base)
        return seen

    def _to_slice(self, n: int, rec: dict[str, Any]) -> Slice:
        fields: dict[str, dict[str, Any]] = {}
        copybooks: list[str] = []
        unsupported = list(rec["unsupported"])
        for name in self._names(rec):
            item = self.dict.lookup(name)
            fields[name] = item.entry()
            if item.file != self.src.program_file and item.file not in copybooks:
                copybooks.append(item.file)
            if self._overlaps(item):
                unsupported.append(f"REDEFINES overlap on {name}")
        if rec["file"] != self.src.program_file and rec["file"] not in copybooks:
            copybooks.append(rec["file"])
        return Slice(
            slice_id=f"{self.program.program_id}-S{n:03d}",
            program=self.program.program_id,
            program_file=self.src.program_file,
            source_file=rec["file"],
            paragraph=rec["paragraph"],
            line_start=rec["start"],
            line_end=rec["end"],
            source_text=self.src.raw_text(rec["file"], rec["start"], rec["end"]),
            condition=rec["cond"],
            actions=[{k: v for k, v in a.items() if k != "_stmt"} for a in rec["actions"]],
            else_actions=[{k: v for k, v in a.items() if k != "_stmt"} for a in rec["else_actions"]],
            fields=fields,
            copybooks=copybooks,
            external_dependency=rec["external"],
            unsupported=list(dict.fromkeys(unsupported)),
        )

    def _overlaps(self, item: Any) -> bool:
        """The field shares storage with another field (it, or a group above it, is or has a REDEFINES)."""
        p = item
        while p is not None:
            if p.redefines or p.name in self.redefined:
                return True
            p = p.parent
        return False
