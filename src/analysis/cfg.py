"""Control flow across paragraphs: PERFORM (THRU, SECTION, loops), GO TO, fall-through, STOP RUN.

Static analysis over every path from the program entry, giving for each decision:

* **context**: the conditions under which callers reach it. A rule in a paragraph
  that is PERFORMed only inside ``IF TRAN-CODE = 'U'`` carries that condition. An
  ``IF bad GO TO P-EXIT`` guard adds ``NOT bad`` to the statements after it.
* **reachability**: paragraphs that no path from the entry reaches (dead code).
* **derivations**: fields computed unconditionally before the decision, expressed
  in terms of earlier fields and inputs (``WS-RATIO = (DEBT-AMT / INCOME-AMT) * 100``).

Rules keep their local ``conditions`` (they still trace to the IF lines); the
context is reported next to them. Exploration is bounded; very large programs
are marked ``truncated`` rather than slowed down.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from src.analysis.ast import (
    AssignStmt, EvaluateStmt, GoToStmt, IfStmt, MoveStmt, OtherStmt, Paragraph, PerformStmt,
    Program, SearchStmt, SetStmt, Stmt,
)

MAX_VISITS = 20000
MAX_CONTEXT_PATHS = 8
TERMINATORS = ("STOP RUN", "GOBACK")
_IDENT = re.compile(r"(?<![A-Z0-9-])([A-Z][A-Z0-9-]*[A-Z0-9]|[A-Z])(?![A-Z0-9-])")


def negate(cond: dict[str, Any]) -> dict[str, Any]:
    return cond["not"] if set(cond) == {"not"} else {"not": cond}


def combine_path(path: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not path:
        return None
    if len(path) == 1:
        c = path[0]
        return c if set(c) in ({"all"}, {"any"}, {"not"}) else {"all": [c]}
    flat: list[dict[str, Any]] = []
    for c in path:
        flat.extend(c["all"] if set(c) == {"all"} else [c])
    return {"all": flat}


def is_terminator(s: Stmt) -> bool:
    return isinstance(s, OtherStmt) and (s.verb in TERMINATORS or s.text == "EXIT PROGRAM")


class Paragraphs:
    """Name / section lookup and PERFORM range resolution in source order."""

    def __init__(self, program: Program):
        self.list: list[Paragraph] = program.paragraphs
        self.index: dict[str, int] = {}
        self.sections: dict[str, list[int]] = defaultdict(list)
        for i, p in enumerate(self.list):
            if p.name and p.name not in self.index:
                self.index[p.name] = i
            if p.section:
                self.sections[p.section].append(i)

    def name(self, i: int) -> str:
        p = self.list[i]
        return p.name or (f"{p.section} SECTION" if p.section else "(entry)")

    def resolve(self, target: Optional[str], thru: Optional[str] = None) -> list[int]:
        """Paragraph indices executed by PERFORM target [THRU thru]; [] when unknown."""
        if not target:
            return []

        def bounds(name: str) -> Optional[tuple[int, int]]:
            if name in self.sections:
                idx = self.sections[name]
                return idx[0], idx[-1]
            if name in self.index:
                return self.index[name], self.index[name]
            return None

        a = bounds(target)
        if a is None:
            return []
        if thru:
            b = bounds(thru)
            if b is None or b[1] < a[0]:
                return []
            return list(range(a[0], b[1] + 1))
        return list(range(a[0], a[1] + 1))

    def goto_index(self, target: str) -> Optional[int]:
        if target in self.index:
            return self.index[target]
        if target in self.sections:
            return self.sections[target][0]
        return None


def perform_target(source_name: str) -> tuple[str, Optional[str]]:
    """'P1 THRU P2' -> ('P1', 'P2')."""
    parts = source_name.split(" THRU ")
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else None)


@dataclass
class FlowInfo:
    contexts: dict[int, list[list[dict[str, Any]]]] = field(default_factory=lambda: defaultdict(list))
    derivations: dict[int, dict[str, str]] = field(default_factory=dict)
    performed_from: dict[int, set[str]] = field(default_factory=lambda: defaultdict(set))
    reachable: set[int] = field(default_factory=set)
    has_loops: bool = False
    truncated: bool = False
    inputs: set[str] = field(default_factory=set)

    def context_of(self, stmt: Stmt) -> tuple[Optional[dict[str, Any]], bool]:
        """(combined context or None when unconditional, too_many_paths)."""
        paths = self.contexts.get(id(stmt), [])
        uniq: list[list[dict[str, Any]]] = []
        seen: set[str] = set()
        for p in paths:
            key = repr(p)
            if key not in seen:
                seen.add(key)
                uniq.append(p)
        if not uniq or any(not p for p in uniq):
            return None, False
        if len(uniq) > MAX_CONTEXT_PATHS:
            return None, True
        combined = [combine_path(p) for p in uniq]
        return (combined[0] if len(combined) == 1 else {"any": combined}), False


class FlowAnalyzer:
    def __init__(self, program: Program, statement_actions):
        self.program = program
        self.paras = Paragraphs(program)
        self.actions = statement_actions          # Stmt -> list of action dicts (shared with the slicer)
        self.info = FlowInfo()
        self.visits = 0
        self.record_fields = self._record_fields()

    def _record_fields(self) -> dict[str, set[str]]:
        d = self.program.dictionary
        out: dict[str, set[str]] = {}
        for item in d.items:
            if item.level in (1, 77) and item.name != "FILLER":
                names: set[str] = set()
                stack = [item]
                while stack:
                    x = stack.pop()
                    names.add(x.name)
                    stack.extend(x.children)
                out[item.name] = names
        return out

    # ---- public --------------------------------------------------------------------
    def run(self) -> FlowInfo:
        if self.paras.list:
            self._flow(0, len(self.paras.list) - 1, [], (), {}, caller=None)
        return self.info

    # ---- walking -------------------------------------------------------------------
    def _tick(self) -> bool:
        self.visits += 1
        if self.visits > MAX_VISITS:
            self.info.truncated = True
            return False
        return True

    def _flow(self, start: int, end: int, path: list, stack: tuple, env: dict, caller: Optional[str]) -> str:
        """Run paragraphs start..end (falling through).

        Returns 'stop' (STOP RUN), 'goto' (control left the range for good) or 'fall'
        (the range completed, possibly through a GO TO to a later paragraph of the range).
        """
        i = start
        while i <= end:
            if not self._tick():
                return "stop"
            self.info.reachable.add(i)
            if caller:
                self.info.performed_from[i].add(caller)
            if i in stack:                     # recursive PERFORM: stop exploring this path
                return "fall"
            result = self._stmts(self.paras.list[i].statements, path, stack + (i,), env, i, end)
            if result in ("stop", "goto"):
                return result
            if result == "jump":                 # the explored GO TO target finished the range
                return "fall"
            i += 1
        return "fall"

    def _stmts(self, stmts: list[Stmt], path: list, stack: tuple, env: dict, para: int, range_end: int) -> str:
        guards: list[dict[str, Any]] = []
        for s in stmts:
            if not self._tick():
                return "stop"
            cur = path + guards
            if isinstance(s, (IfStmt, EvaluateStmt, SearchStmt)):
                self.info.contexts[id(s)].append(cur)
                self._note_derivations(s, env)
                outcome = self._decision(s, cur, stack, env, para, range_end)
                if outcome == "both":
                    return "stop"
                if isinstance(s, IfStmt) and outcome == "then":
                    guards.append(negate(s.cond))
                elif isinstance(s, IfStmt) and outcome == "else":
                    guards.append(s.cond)
            elif isinstance(s, PerformStmt):
                if s.until is not None or s.times is not None or s.varying is not None:
                    self.info.has_loops = True
                if s.body is not None:
                    r = self._stmts(s.body, cur, stack, env, para, range_end)
                else:
                    idx = self.paras.resolve(s.target, s.thru)
                    # env is shared: fields computed by the performed paragraph stay known afterwards
                    r = self._flow(idx[0], idx[-1], cur, stack, env, self.paras.name(para)) if idx else "fall"
                if r in ("stop", "goto"):
                    return r
            elif isinstance(s, GoToStmt):
                inside = True
                for t in s.targets:
                    inside = self._goto(t, cur, stack, env, para, range_end) and inside
                return "jump" if inside else "goto"
            elif is_terminator(s):
                return "stop"
            elif isinstance(s, (MoveStmt, AssignStmt, SetStmt)):
                if guards:                       # after an early-exit guard: only conditionally assigned
                    for a in self.actions(s):
                        if a.get("source_name"):
                            env.pop(a["source_name"].split("(")[0], None)
                else:
                    self._assign(s, env)
            elif isinstance(s, OtherStmt) and s.verb in ("ACCEPT", "READ"):
                words = s.text.split()
                for name in words[1:]:
                    for f in self.record_fields.get(name, {name}):
                        env.pop(f, None)
                        self.info.inputs.add(f)
        return "fall"

    def _decision(self, s: Stmt, cur: list, stack: tuple, env: dict, para: int, range_end: int) -> str:
        """Explore branches; returns which branches always leave the paragraph: none/then/else/both."""
        assigned: set[str] = set()

        def branch(stmts: list[Stmt], p: list) -> bool:
            benv = dict(env)
            r = self._stmts(stmts, p, stack, benv, para, range_end)
            assigned.update(k for k in benv if benv.get(k) != env.get(k))
            assigned.update(k for k in env if k not in benv)
            return r in ("stop", "goto", "jump")

        if isinstance(s, IfStmt):
            t = branch(s.then, cur + [s.cond])
            e = branch(s.else_ or [], cur + [negate(s.cond)]) if s.else_ is not None else False
            outcome = "both" if t and e else "then" if t else "else" if e else "none"
        elif isinstance(s, EvaluateStmt):
            earlier: list[dict[str, Any]] = []
            ends = []
            for w in s.whens:
                p = cur + [negate(c) for c in earlier] + ([w.cond] if w.cond is not None else [])
                if w.cond is not None:
                    earlier.append(w.cond)
                ends.append(branch(w.stmts, p))
            has_other = any(w.cond is None for w in s.whens)
            outcome = "both" if ends and all(ends) and has_other else "none"
        else:  # SEARCH
            for w in s.whens:
                branch(w.stmts, cur + ([w.cond] if w.cond else []))
            if s.at_end:
                branch(s.at_end, cur)
            outcome = "none"
        for k in assigned:                       # conditionally assigned: no longer a known derivation
            env.pop(k, None)
        return outcome

    def _goto(self, target: str, cur: list, stack: tuple, env: dict, para: int, range_end: int) -> bool:
        """Explore a GO TO; True when the target lies inside the current PERFORM range."""
        idx = self.paras.goto_index(target)
        if idx is None:
            return False
        self.info.performed_from[idx].add(self.paras.name(para))
        inside = para <= idx <= range_end
        end = range_end if inside else len(self.paras.list) - 1
        self._flow(idx, end, cur, stack, dict(env), caller=None)
        return inside

    # ---- data flow -----------------------------------------------------------------
    def _assign(self, s: Stmt, env: dict) -> None:
        for a in self.actions(s):
            if a["type"] not in ("set", "compute"):
                continue
            target = a["source_name"].split("(")[0]
            if a.get("expr"):
                env[target] = self._substitute(a["expr"], env)
            elif "value" in a:
                v = a["value"]
                env[target] = f"'{v}'" if isinstance(v, str) else str(v)

    def _substitute(self, expr: str, env: dict) -> str:
        def repl(m: re.Match) -> str:
            name = m.group(1)
            if name in env and env[name] != name:
                inner = env[name]
                return inner if _IDENT.fullmatch(inner) or inner.replace(".", "").isdigit() else f"({inner})"
            return name
        return _IDENT.sub(repl, expr)

    def _note_derivations(self, s: Stmt, env: dict) -> None:
        known = {k: v for k, v in env.items() if v != k}
        prev = self.info.derivations.get(id(s))
        if prev is None:
            self.info.derivations[id(s)] = known
        else:                                    # reached more than once: keep what every path agrees on
            self.info.derivations[id(s)] = {k: v for k, v in prev.items() if known.get(k) == v}


def analyze_flow(program: Program, statement_actions) -> FlowInfo:
    return FlowAnalyzer(program, statement_actions).run()
