"""Execute a program through its extracted rules (the differential test's model of the program).

Control flow follows COBOL: the main flow starts at the first paragraph and falls
through; PERFORM runs a paragraph range (THRU, SECTION) and returns, with UNTIL,
VARYING and TIMES loops; GO TO jumps (inside or out of a PERFORM range); STOP RUN /
GOBACK end the run. Every decision statement is executed **only through its
extracted rules**: conditions are evaluated on the state at the decision, then
the firing rules' actions run in order, including the PERFORM / GO TO / STOP
actions inside them. Assignments outside decisions run natively.

So the compiled program and this model disagree whenever a rule's condition,
action or control transfer is wrong. Bounded: runaway loops raise SimulationLimit.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from src.analysis.analyze import Analysis
from src.analysis.ast import (
    AssignStmt, EvaluateStmt, GoToStmt, IfStmt, Lit, MoveStmt, PerformStmt, SearchStmt,
    SetStmt, Stmt,
)
from src.analysis.cfg import Paragraphs, is_terminator, perform_target
from src.verify.engine import apply_actions, base, eval_cond, eval_expr, store, to_num

MAX_STEPS = 200_000
MAX_LOOP = 10_000


class SimulationLimit(RuntimeError):
    pass


class Transfer(Exception):
    """Control leaves the current statement list: ('goto', target) or ('stop', None)."""

    def __init__(self, kind: str, target: Optional[str] = None):
        super().__init__(kind)
        self.kind, self.target = kind, target


class RuleInterpreter:
    def __init__(self, analysis: Analysis, rules: list[dict[str, Any]], fields: dict[str, dict]):
        self.a = analysis
        self.rules = rules
        self.fields = fields
        self.paras = Paragraphs(analysis.program)
        self.by_stmt: dict[int, list[int]] = defaultdict(list)
        for k, stmt in enumerate(analysis.slice_stmts):
            if k < len(rules):
                self.by_stmt[id(stmt)].append(k)
        self.native: dict[int, list[dict[str, Any]]] = {}
        from src.analysis.slicer import Slicer
        self._slicer = Slicer(analysis.program, analysis.source)

    # ---- public --------------------------------------------------------------------
    def run(self, state: dict[str, Any]) -> set[int]:
        """Mutates state like the program would; returns indexes of rules that fired."""
        self.state, self.fired, self.steps = state, set(), 0
        if not self.paras.list:
            return self.fired
        try:
            self._range(0, len(self.paras.list) - 1, main=True)
        except Transfer as t:
            if t.kind == "goto":                # GO TO out of everything: unknown target
                pass
        return self.fired

    # ---- control flow --------------------------------------------------------------
    def _tick(self) -> None:
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise SimulationLimit("step budget exceeded (runaway loop?)")

    def _range(self, start: int, end: int, main: bool = False) -> None:
        """Execute paragraphs start..end in order; GO TO inside the range jumps within it."""
        i = start
        while i <= end:
            self._tick()
            try:
                self._stmts(self.paras.list[i].statements)
            except Transfer as t:
                if t.kind == "goto":
                    j = self.paras.goto_index(t.target or "")
                    if j is not None and (main or start <= j <= end):
                        i = j
                        if main:
                            end = len(self.paras.list) - 1
                        continue
                raise
            i += 1

    def _perform(self, target: Optional[str], thru: Optional[str], body: Optional[list[Stmt]]) -> None:
        if body is not None:
            self._stmts(body)
            return
        idx = self.paras.resolve(target, thru)
        if not idx:
            return                              # unknown paragraph: nothing to run
        self._range(idx[0], idx[-1])

    def _perform_stmt(self, s: PerformStmt) -> None:
        run = lambda: self._perform(s.target, s.thru, s.body)   # noqa: E731
        if s.times is not None:
            n = int(to_num(s.times.value if isinstance(s.times, Lit) else self.state.get(base(s.times.text))) or 0)
            for _ in range(min(n, MAX_LOOP)):
                run()
            return
        if s.varying is not None:
            var, start, step = s.varying
            entry = self.fields.get(base(var.name))
            self.state[base(var.name)] = store(self._value(start), entry)
        if s.until is not None:
            for _ in range(MAX_LOOP):
                if not s.test_after and eval_cond(s.until, self.state):
                    return
                run()
                if s.varying is not None:
                    var, _, step = s.varying
                    name = base(var.name)
                    self.state[name] = store((to_num(self.state.get(name)) or 0) + (to_num(self._value(step)) or 0),
                                             self.fields.get(name))
                if s.test_after and eval_cond(s.until, self.state):
                    return
            raise SimulationLimit("PERFORM UNTIL did not finish")
        run()

    def _value(self, operand: Any) -> Any:
        if isinstance(operand, Lit):
            return operand.value
        text = getattr(operand, "text", str(operand))
        if base(text) in self.state:
            return self.state[base(text)]
        return eval_expr(text, self.state)

    # ---- statements ----------------------------------------------------------------
    def _stmts(self, stmts: list[Stmt]) -> None:
        for s in stmts:
            self._tick()
            if isinstance(s, (IfStmt, EvaluateStmt, SearchStmt)):
                self._decision(s)
            elif isinstance(s, PerformStmt):
                self._perform_stmt(s)
            elif isinstance(s, GoToStmt):
                if len(s.targets) == 1 and not s.depending:
                    raise Transfer("goto", s.targets[0])
                raise SimulationLimit("GO TO DEPENDING ON is not simulated")
            elif is_terminator(s):
                raise Transfer("stop")
            elif isinstance(s, (MoveStmt, AssignStmt, SetStmt)):
                acts = self.native.get(id(s))
                if acts is None:
                    acts = [{k: v for k, v in a.items() if k != "_stmt"} for a in self._slicer.statement_actions(s)]
                    self.native[id(s)] = acts
                apply_actions(acts, self.state, self.fields)

    def _decision(self, s: Stmt) -> None:
        ks = self.by_stmt.get(id(s), [])
        if not ks:
            return                               # decision without state effects (only I/O)
        snapshot = dict(self.state)              # all branch conditions see the state at the decision
        fire = {k for k in ks if eval_cond(self.rules[k]["conditions"], snapshot)}
        for k in ks:
            rule = self.rules[k]
            stmts_then, stmts_else = (self.a.action_stmts[k] if k < len(self.a.action_stmts) else ([], []))
            if k in fire:
                self.fired.add(k)
                self._actions(rule.get("actions", []), stmts_then)
            else:
                self._actions(rule.get("else_actions", []), stmts_else)

    def _actions(self, actions: list[dict[str, Any]], stmts: list[Any]) -> None:
        aligned = len(stmts) == len(actions)
        for n, a in enumerate(actions):
            kind = a["type"]
            if kind in ("set", "compute"):
                apply_actions([a], self.state, self.fields)
            elif kind == "perform":
                src = stmts[n] if aligned else None
                if isinstance(src, PerformStmt):
                    self._perform_stmt(src)
                else:
                    target, thru = perform_target(a.get("source_name") or "")
                    self._perform(target, thru, None)
            elif kind == "goto":
                raise Transfer("goto", a.get("source_name"))
            elif kind == "stop":
                raise Transfer("stop")
            # call: external program, no visible effect here
