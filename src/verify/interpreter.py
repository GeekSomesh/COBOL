"""Execute a program through its extracted rules (the differential test's model of the program).

Control flow follows COBOL: the main flow starts at the first paragraph and falls
through; PERFORM runs a paragraph range (THRU, SECTION) and returns, with UNTIL,
VARYING and TIMES loops; GO TO jumps (inside or out of a PERFORM range); STOP RUN /
GOBACK end the run. Every decision statement is executed **only through its
extracted rules**, including the PERFORM / GO TO / STOP actions inside them.
Assignments outside decisions run natively.

A rule flattened from nested decisions (``all`` of the path) is checked level by
level: the part of its condition that comes from each enclosing IF / EVALUATE is
evaluated on the state *at that decision*, and branch statements run in source
order. So ``ELSE PERFORM VALIDATE  IF TRAN-OK = 'Y' ...`` tests TRAN-OK after
VALIDATE ran, as the program does. Single-level rules (a plain IF, a SEARCH row)
are evaluated on the state at their decision.

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
from src.verify.engine import apply_actions, base, eval_cond, eval_expr, is_numeric_entry, resolve, store, to_num

MAX_STEPS = 200_000
MAX_LOOP = 10_000


class SimulationLimit(RuntimeError):
    pass


class Undefined(SimulationLimit):
    """The run compared a numeric field holding non-digit bytes: undefined in COBOL (GnuCOBOL
    then compares the bytes as text), so the row says nothing about the rules."""


class Transfer(Exception):
    """Control leaves the current statement list: ('goto', target) or ('stop', None)."""

    def __init__(self, kind: str, target: Optional[str] = None):
        super().__init__(kind)
        self.kind, self.target = kind, target


class RuleInterpreter:
    def __init__(self, analysis: Analysis, rules: list[dict[str, Any]], fields: dict[str, dict],
                 storage: Any = None, records: Optional[list[str]] = None):
        self.a = analysis
        self.storage = storage                  # byte model: REDEFINES views / OCCURS stay in step
        self.records = records
        self.rules = rules
        self.fields = fields
        self.paras = Paragraphs(analysis.program)
        self.by_stmt: dict[int, list[int]] = defaultdict(list)
        for k, stmt in enumerate(analysis.slice_stmts):
            if k < len(rules):
                self.by_stmt[id(stmt)].append(k)
        # nested decisions: rules by (depth, decision), each rule's condition split by level
        self.trails = [t for t in analysis.trails[:len(rules)]] if analysis.trails else []
        self.at: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.own: dict[tuple[int, Any, int], int] = {}
        self.levels: dict[int, Optional[list[dict[str, Any]]]] = {}
        for k, trail in enumerate(self.trails):
            if not trail or trail[0][1] in ("if", "search"):
                continue
            for depth, (stmt, branch, _) in enumerate(trail):
                self.at[(depth, id(stmt))].append(k)
            stmt, branch, _ = trail[-1]
            self.own[(id(stmt), branch, len(trail) - 1)] = k
            self.levels[k] = self._split(rules[k]["conditions"], [n for _, _, n in trail])
        self.snaps: list[dict[str, Any]] = []
        self.native: dict[int, list[dict[str, Any]]] = {}
        from src.analysis.slicer import Slicer
        self._slicer = Slicer(analysis.program, analysis.source)

    # ---- public --------------------------------------------------------------------
    def run(self, state: dict[str, Any]) -> set[int]:
        """Mutates state like the program would; returns indexes of rules that fired."""
        self.state, self.fired, self.steps, self.snaps = state, set(), 0, []
        self.effective: set[int] = set()        # rules whose actions changed a value
        self._rule: Optional[int] = None
        if self.storage is not None:
            self.storage.reset(state, self.records)
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
        saved, self.snaps = self.snaps, []      # a PERFORMed paragraph starts its own decision nesting
        try:
            self._range(idx[0], idx[-1])
        finally:
            self.snaps = saved

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
            self._sync([base(var.name)])
        if s.until is not None:
            for _ in range(MAX_LOOP):
                if not s.test_after and self._cond(s.until, self.state):
                    return
                run()
                if s.varying is not None:
                    var, _, step = s.varying
                    name = base(var.name)
                    self.state[name] = store((to_num(self.state.get(name)) or 0) + (to_num(self._value(step)) or 0),
                                             self.fields.get(name))
                    self._sync([name])
                if s.test_after and self._cond(s.until, self.state):
                    return
            raise SimulationLimit("PERFORM UNTIL did not finish")
        run()

    def _apply(self, actions: list[dict[str, Any]]) -> None:
        before = {resolve(a.get("source_name") or "", self.state): self.state.get(
            resolve(a.get("source_name") or "", self.state)) for a in actions if a["type"] in ("set", "compute")}
        written = apply_actions(actions, self.state, self.fields)
        if self._rule is not None and any(self.state.get(k) != before.get(k) for k in written):
            self.effective.add(self._rule)
        self._sync(written)

    def _sync(self, keys: list[str]) -> None:
        if self.storage is not None and keys:
            self.storage.sync(self.state, keys)

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
                self._apply(acts)

    @staticmethod
    def _split(cond: dict[str, Any], sizes: list[int]) -> Optional[list[dict[str, Any]]]:
        """The rule's condition as one part per decision level, or None when it no longer has
        the slicer's shape (edited by hand): then it is tested whole at the outer decision."""
        if len(sizes) == 1:
            return [cond]
        items = cond.get("all")
        if items is None or len(items) != sum(sizes):
            return None
        out, k = [], 0
        for n in sizes:
            out.append({"all": items[k:k + n]})
            k += n
        return out

    def _cond(self, c: dict[str, Any], state: dict[str, Any]) -> bool:
        """eval_cond, left to right like GnuCOBOL, refusing undefined comparisons."""
        if c.get("all") is not None:
            return all(self._cond(x, state) for x in c["all"])
        if c.get("any") is not None:
            return any(self._cond(x, state) for x in c["any"])
        inner = c.get("not") if c.get("not") is not None else c.get("not_")
        if inner is not None:
            return not self._cond(inner, state)
        if c["op"] not in ("is_numeric", "is_alphabetic"):
            for name in (c["source_name"], c.get("ref")):
                if name and is_numeric_entry(self.fields.get(base(name))):
                    v = state.get(resolve(name, state))
                    if isinstance(v, str):       # numeric fields hold Decimals; text here is raw bytes
                        raise Undefined(f"undefined: {base(name)} holds non-digit bytes in a comparison")
        return eval_cond(c, state)

    def _holds(self, k: int, upto: int) -> bool:
        parts = self.levels.get(k)
        if parts is None:
            return self._cond(self.rules[k]["conditions"], self.snaps[0])
        return all(self._cond(parts[d], self.snaps[d]) for d in range(min(upto, len(parts) - 1) + 1))

    def _decision(self, s: Stmt) -> None:
        depth = len(self.snaps)
        nested = self.at.get((depth, id(s)))
        if nested and isinstance(s, (IfStmt, EvaluateStmt)):
            self._nested(s, depth, nested)
            return
        ks = self.by_stmt.get(id(s), []) if depth == 0 else []
        if not ks:
            return                               # decision without state effects (only I/O)
        snapshot = dict(self.state)              # all branch conditions see the state at the decision
        fire = {k for k in ks if self._cond(self.rules[k]["conditions"], snapshot)}
        for k in ks:
            rule = self.rules[k]
            stmts_then, stmts_else = (self.a.action_stmts[k] if k < len(self.a.action_stmts) else ([], []))
            if k in fire:
                self.fired.add(k)
                self._rule = k
                self._actions(rule.get("actions", []), stmts_then)
            else:
                self._rule = k
                self._actions(rule.get("else_actions", []), stmts_else)
            self._rule = None

    def _nested(self, s: Stmt, depth: int, ks: list[int]) -> None:
        """Run an IF / EVALUATE whose rules were flattened from nested decisions."""
        self.snaps.append(dict(self.state))
        try:
            if isinstance(s, IfStmt):
                branches = [(0, s.then), (1, s.else_ or [])]
            else:
                branches = [(i, w.stmts) for i, w in enumerate(s.whens)]
            for b, body in branches:
                sub = [k for k in ks if self.trails[k][depth][1] == b]
                if not sub or not any(self._holds(k, depth) for k in sub):
                    continue
                own = self.own.get((id(s), b, depth))
                fires = own is not None and self._holds(own, depth)
                if fires:
                    self.fired.add(own)
                    acts = self.rules[own].get("actions", [])
                    srcs = self.a.action_stmts[own][0] if own < len(self.a.action_stmts) else []
                    by_src: dict[int, list[int]] = defaultdict(list)
                    for n, src in enumerate(srcs[:len(acts)]):
                        by_src[id(src)].append(n)
                    leftover = [n for n in range(len(acts)) if n >= len(srcs)]
                for st in body:
                    self._tick()
                    if isinstance(st, (IfStmt, EvaluateStmt, SearchStmt)):
                        self._decision(st)
                    elif isinstance(st, PerformStmt) and st.body is not None:
                        self._perform_stmt(st)   # inline loop inside a branch: its body runs natively
                    elif fires:
                        ns = by_src.get(id(st), [])
                        self._rule = own
                        self._actions([acts[n] for n in ns], [srcs[n] for n in ns])
                        self._rule = None
                if fires and leftover:
                    self._rule = own
                    self._actions([acts[n] for n in leftover], [])
                    self._rule = None
        finally:
            self.snaps.pop()

    def _actions(self, actions: list[dict[str, Any]], stmts: list[Any]) -> None:
        aligned = len(stmts) == len(actions)
        for n, a in enumerate(actions):
            kind = a["type"]
            if kind in ("set", "compute"):
                self._apply([a])
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
