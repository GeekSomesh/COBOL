"""Recursive-descent parser for the documented COBOL subset (see docs/parser_subset.md).

A hand-written parser rather than a Lark grammar: COBOL's separator period closes
every open IF/EVALUATE scope at once, and ELSE binds to the nearest IF. Both are
simple to express by "stop the statement list at a period or terminator and let
each owner consume what is theirs", and awkward in a context-free grammar.

Conditions are turned straight into the raw condition tree used by
:class:`src.common.models.Slice` (COBOL names), with abbreviated relations and
88-level condition names expanded via the data dictionary.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from src.analysis.ast import (
    AssignStmt, CallStmt, EvaluateStmt, ExecStmt, Expr, GoToStmt, Ident, IfStmt, Lit,
    MoveStmt, Operand, OtherStmt, Paragraph, PerformStmt, Program, SearchStmt, SetStmt,
    Stmt, WhenBranch, operand_text,
)
from src.analysis.dictionary import DataItem, Dictionary
from src.analysis.lexer import Token
from src.ingest.source import ResolvedSource

VERBS = {
    "ACCEPT", "ADD", "ALTER", "CALL", "CANCEL", "CLOSE", "COMPUTE", "CONTINUE", "DELETE",
    "DISPLAY", "DIVIDE", "EVALUATE", "EXEC", "EXIT", "GENERATE", "GO", "GOBACK", "IF",
    "INITIALIZE", "INITIATE", "INSPECT", "MERGE", "MOVE", "MULTIPLY", "OPEN", "PERFORM",
    "READ", "RELEASE", "RETURN", "REWRITE", "SEARCH", "SET", "SORT", "START", "STOP",
    "STRING", "SUBTRACT", "TERMINATE", "UNSTRING", "WRITE", "ENTRY", "COMMIT", "ROLLBACK",
    "UNLOCK", "FREE", "ALLOCATE",
}
SCOPE_TERMINATORS = {
    "END-IF", "END-EVALUATE", "END-PERFORM", "END-SEARCH", "END-CALL", "END-READ",
    "END-WRITE", "END-REWRITE", "END-DELETE", "END-START", "END-RETURN", "END-COMPUTE",
    "END-ADD", "END-SUBTRACT", "END-MULTIPLY", "END-DIVIDE", "END-STRING", "END-UNSTRING",
    "END-ACCEPT", "END-DISPLAY", "END-EXEC", "END-INVOKE", "END-MOVE",
}
LIST_STOP = {"ELSE", "WHEN"} | SCOPE_TERMINATORS
FIGURATIVE = {
    "ZERO": 0, "ZEROS": 0, "ZEROES": 0, "SPACE": " ", "SPACES": " ",
    "HIGH-VALUE": "HIGH-VALUES", "HIGH-VALUES": "HIGH-VALUES",
    "LOW-VALUE": "LOW-VALUES", "LOW-VALUES": "LOW-VALUES",
    "QUOTE": '"', "QUOTES": '"', "NULL": "NULL", "NULLS": "NULL",
}
CONDITION_WORDS = {
    "AND", "OR", "NOT", "IS", "THAN", "TO", "GREATER", "LESS", "EQUAL", "EQUALS", "THEN",
    "NUMERIC", "ALPHABETIC", "ALPHABETIC-LOWER", "ALPHABETIC-UPPER", "POSITIVE", "NEGATIVE",
    "OF", "IN", "THRU", "THROUGH", "ALSO", "OTHER", "ANY", "TRUE", "FALSE", "BY", "FROM",
    "INTO", "GIVING", "ROUNDED", "REMAINDER", "UNTIL", "VARYING", "TIMES", "USING",
    "RETURNING", "UPON", "WITH", "TEST", "BEFORE", "AFTER", "CORRESPONDING", "CORR",
    "SENTENCE", "RUN", "UP", "DOWN", "ON", "AT", "SIZE", "ERROR", "EXCEPTION", "OVERFLOW",
    "INVALID", "KEY", "END", "SECTION", "DIVISION", "ALL", "FUNCTION", "ADDRESS", "LENGTH",
} | VERBS | LIST_STOP
DATA_CLAUSE_WORDS = {
    "PIC", "PICTURE", "VALUE", "VALUES", "REDEFINES", "OCCURS", "USAGE", "COMP", "COMP-1",
    "COMP-2", "COMP-3", "COMP-4", "COMP-5", "COMPUTATIONAL", "COMPUTATIONAL-1",
    "COMPUTATIONAL-2", "COMPUTATIONAL-3", "COMPUTATIONAL-4", "COMPUTATIONAL-5", "BINARY",
    "PACKED-DECIMAL", "DISPLAY", "INDEX", "POINTER", "SIGN", "JUST", "JUSTIFIED", "BLANK",
    "SYNC", "SYNCHRONIZED", "GLOBAL", "EXTERNAL", "RENAMES",
}
USAGE_WORDS = DATA_CLAUSE_WORDS - {"PIC", "PICTURE", "VALUE", "VALUES", "REDEFINES", "OCCURS",
                                    "USAGE", "SIGN", "JUST", "JUSTIFIED", "BLANK", "SYNC",
                                    "SYNCHRONIZED", "GLOBAL", "EXTERNAL", "RENAMES"}
HANDLER_PHRASES = [
    ("AT", "END"), ("AT", "END-OF-PAGE"), ("AT", "EOP"), ("END-OF-PAGE",), ("EOP",),
    ("ON", "EXCEPTION"), ("EXCEPTION",), ("ON", "OVERFLOW"), ("OVERFLOW",),
    ("ON", "SIZE", "ERROR"), ("SIZE", "ERROR"), ("INVALID", "KEY"), ("INVALID",),
]
NEGATE = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}
FLIP = {"==": "==", "!=": "!=", "<": ">", ">": "<", "<=": ">=", ">=": "<="}


class ParseError(Exception):
    def __init__(self, msg: str, tok: Optional[Token]):
        where = f"{tok.file}:{tok.line}" if tok else "end of file"
        super().__init__(f"{where}: {msg}")
        self.token = tok


def lit_from_token(tok: Token) -> Lit:
    if tok.kind == "NUM":
        text = tok.value
        num: int | float = float(text) if "." in text else int(text)
        return Lit(num, "num", text)
    if tok.kind == "STR":
        return Lit(tok.value, "str", tok.value)
    if tok.kind == "WORD" and tok.value in FIGURATIVE:
        return Lit(FIGURATIVE[tok.value], "fig", tok.value)
    raise ParseError(f"expected literal, got {tok.value!r}", tok)


def _join(parts: list[str]) -> str:
    text = " ".join(parts)
    return text.replace("( ", "(").replace(" )", ")")


class Parser:
    def __init__(self, tokens: list[Token], src: ResolvedSource):
        self.toks = tokens
        self.i = 0
        self.src = src
        self.dict = Dictionary()
        self.diags: list[str] = []
        self.last: Optional[Token] = None
        self._abbr: Optional[tuple[Operand, str]] = None
        self._notes: list[str] = []

    # ---- token helpers ---------------------------------------------------
    def peek(self, k: int = 0) -> Optional[Token]:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def at_word(self, *words: str, k: int = 0) -> bool:
        t = self.peek(k)
        return t is not None and t.kind == "WORD" and t.value in words

    def at_kind(self, kind: str, k: int = 0) -> bool:
        t = self.peek(k)
        return t is not None and t.kind == kind

    def at_op(self, *ops: str, k: int = 0) -> bool:
        t = self.peek(k)
        return t is not None and t.kind == "OP" and t.value in ops

    def next(self) -> Token:
        t = self.peek()
        if t is None:
            raise ParseError("unexpected end of file", self.last)
        self.i += 1
        self.last = t
        return t

    def accept_word(self, *words: str) -> Optional[Token]:
        return self.next() if self.at_word(*words) else None

    def expect_word(self, *words: str) -> Token:
        if not self.at_word(*words):
            raise ParseError(f"expected {'/'.join(words)}, got {self.peek()!r}", self.peek() or self.last)
        return self.next()

    def skip_to_period(self) -> None:
        while self.peek() is not None and not self.at_kind("PERIOD"):
            self.next()
        if self.at_kind("PERIOD"):
            self.next()

    def at_division(self, name: str) -> bool:
        return self.at_word(name) and self.at_word("DIVISION", k=1)

    # ---- program ---------------------------------------------------------
    def parse_program(self) -> Program:
        program_id = ""
        while self.peek() is not None and not self.at_division("DATA") and not self.at_division("PROCEDURE"):
            if self.at_word("PROGRAM-ID"):
                self.next()
                if self.at_kind("PERIOD"):
                    self.next()
                tok = self.next()
                program_id = tok.value.upper()
                self.skip_to_period()
            else:
                self.next()
        if self.at_division("DATA"):
            self.next(); self.next()
            if self.at_kind("PERIOD"):
                self.next()
            self.parse_data_division()
        paragraphs: list[Paragraph] = []
        if self.at_division("PROCEDURE"):
            self.next(); self.next()
            self.skip_to_period()
            paragraphs = self.parse_procedure_division()
        if not program_id:
            program_id = self.src.program_file.rsplit(".", 1)[0].upper()
        return Program(program_id, paragraphs, self.dict, self.diags)

    # ---- DATA DIVISION ---------------------------------------------------
    def parse_data_division(self) -> None:
        section = "WORKING-STORAGE"
        stack: list[DataItem] = []
        last_non88: Optional[DataItem] = None
        while self.peek() is not None and not self.at_division("PROCEDURE"):
            t = self.peek()
            if t.kind == "WORD" and self.at_word("SECTION", k=1):
                section = t.value
                self.next(); self.next()
                if self.at_kind("PERIOD"):
                    self.next()
                stack.clear()
                continue
            if self.at_word("FD", "SD", "RD", "CD"):
                self.skip_to_period()
                stack.clear()
                continue
            if t.kind == "NUM" and t.value.isdigit():
                try:
                    item = self.parse_data_entry(section)
                except ParseError as exc:
                    self.diags.append(f"data entry skipped: {exc}")
                    self.skip_to_period()
                    continue
                if item.level == 88:
                    item.parent = last_non88
                    if last_non88 is not None:
                        last_non88.children.append(item)
                elif item.level in (66, 77) or item.level == 1:
                    stack = [item] if item.level == 1 else []
                    last_non88 = item
                else:
                    while stack and stack[-1].level >= item.level:
                        stack.pop()
                    if stack:
                        item.parent = stack[-1]
                        stack[-1].children.append(item)
                    stack.append(item)
                    last_non88 = item
                item.comment = self.src.comment_before(item.file, item.line)
                self.dict.add(item)
                continue
            self.diags.append(f"{t.file}:{t.line}: unexpected {t.value!r} in DATA DIVISION")
            self.skip_to_period()

    def parse_data_entry(self, section: str) -> DataItem:
        lvl_tok = self.next()
        item = DataItem(int(lvl_tok.value), "FILLER", lvl_tok.file, lvl_tok.line, section)
        t = self.peek()
        if t is not None and t.kind == "WORD" and t.value not in DATA_CLAUSE_WORDS:
            item.name = self.next().value
        while self.peek() is not None and not self.at_kind("PERIOD"):
            if self.accept_word("REDEFINES"):
                item.redefines = self.next().value
            elif self.at_word("PIC", "PICTURE"):
                self.next()
                if self.at_kind("PIC"):
                    item.pic = self.next().value
            elif self.accept_word("USAGE"):
                self.accept_word("IS")
                item.usage = self.next().value
            elif self.at_word(*USAGE_WORDS):
                item.usage = self.next().value
            elif self.at_word("VALUE", "VALUES"):
                self.next()
                self.accept_word("IS", "ARE")
                values = self._value_list()
                if item.level == 88:
                    item.values88 = values
                elif values:
                    item.value = values[0][0]
            elif self.accept_word("OCCURS"):
                if self.at_kind("NUM"):
                    item.occurs = int(self.next().value)
                if self.accept_word("TO") and self.at_kind("NUM"):
                    item.occurs = int(self.next().value)
                self.accept_word("TIMES")
            else:
                self.next()     # SIGN, JUSTIFIED, INDEXED BY, DEPENDING ON, KEY ... not modelled
        if self.at_kind("PERIOD"):
            self.next()
        return item

    def _value_list(self) -> list[list[Any]]:
        values: list[list[Any]] = []
        while True:
            self.accept_word("ALL")
            t = self.peek()
            if t is None or not (t.kind in ("NUM", "STR") or (t.kind == "WORD" and t.value in FIGURATIVE)):
                break
            lo = lit_from_token(self.next()).value
            if self.accept_word("THRU", "THROUGH"):
                hi = lit_from_token(self.next()).value
                values.append([lo, hi])
            else:
                values.append([lo])
        return values

    # ---- PROCEDURE DIVISION ----------------------------------------------
    def is_paragraph_header(self) -> bool:
        t = self.peek()
        return (t is not None and t.kind in ("WORD", "NUM") and t.value not in VERBS
                and self.at_kind("PERIOD", k=1))

    def parse_procedure_division(self) -> list[Paragraph]:
        paragraphs: list[Paragraph] = []
        section: Optional[str] = None
        current: Optional[Paragraph] = None

        def close(tok: Optional[Token]) -> None:
            if current is not None and tok is not None:
                current.line_end = tok.line

        if self.at_word("DECLARATIVES"):
            while self.peek() is not None and not (self.at_word("END") and self.at_word("DECLARATIVES", k=1)):
                self.next()
            self.next(); self.next()
            if self.at_kind("PERIOD"):
                self.next()

        while self.peek() is not None:
            t = self.peek()
            if self.at_word("END") and self.at_word("PROGRAM", k=1):
                break
            if t.kind == "PERIOD":
                self.next()
                continue
            if t.kind == "WORD" and self.at_word("SECTION", k=1):
                close(self.last)
                section = t.value
                self.next(); self.next()
                self.skip_to_period()
                current = None
                continue
            if self.is_paragraph_header():
                close(self.last)
                self.next(); self.next()
                current = Paragraph(t.value, section, t.file, t.line, t.line)
                paragraphs.append(current)
                continue
            if current is None:
                current = Paragraph(None, section, t.file, t.line, t.line)
                paragraphs.append(current)
            start = self.i
            stmts = self.parse_statements()
            current.statements.extend(stmts)
            if self.i == start:        # nothing consumed: skip the offending token
                bad = self.next()
                self.diags.append(f"{bad.file}:{bad.line}: unexpected {bad.value!r}")
        close(self.last)
        return paragraphs

    def parse_statements(self, extra_stop: Optional[Callable[[], bool]] = None) -> list[Stmt]:
        out: list[Stmt] = []
        while True:
            t = self.peek()
            if t is None or t.kind == "PERIOD":
                break
            if t.kind == "WORD" and t.value in LIST_STOP:
                break
            if extra_stop is not None and extra_stop():
                break
            if self.at_word("NEXT") and self.at_word("SENTENCE", k=1):
                tok = self.next(); self.next()
                out.append(OtherStmt("NEXT SENTENCE", tok.file, tok.line, tok.line, "NEXT SENTENCE"))
                continue
            if t.kind != "WORD" or t.value not in VERBS:
                break           # not a statement start (paragraph header or junk): let the caller decide
            try:
                stmt = self.parse_statement()
            except ParseError as exc:
                self.diags.append(f"statement skipped: {exc}")
                self._recover()
                continue
            if stmt is not None:
                out.append(stmt)
        return out

    def _recover(self) -> None:
        """Skip to the next verb, terminator or period after a parse error."""
        while self.peek() is not None:
            t = self.peek()
            if t.kind == "PERIOD" or (t.kind == "WORD" and (t.value in VERBS or t.value in LIST_STOP)):
                return
            self.next()

    def parse_statement(self) -> Optional[Stmt]:
        verb = self.peek().value
        if verb == "IF":
            return self.parse_if()
        if verb == "EVALUATE":
            return self.parse_evaluate()
        if verb == "PERFORM":
            return self.parse_perform()
        if verb == "SEARCH":
            return self.parse_search()
        if verb == "EXEC":
            return self.parse_exec()
        return self.parse_simple()

    # ---- IF / EVALUATE / SEARCH -------------------------------------------
    def parse_if(self) -> IfStmt:
        start = self.next()
        cond, notes = self.parse_condition()
        self.accept_word("THEN")
        then = self.parse_statements()
        node = IfStmt("IF", start.file, start.line, start.line, cond=cond, notes=notes, then=then)
        if self.at_word("ELSE"):
            node.else_line = self.next().line
            node.else_ = self.parse_statements()
        if self.at_word("END-IF"):
            self.next()
        node.line_end = self.last.line
        return node

    def parse_evaluate(self) -> EvaluateStmt:
        start = self.next()
        subjects = [self._eval_subject()]
        while self.accept_word("ALSO"):
            subjects.append(self._eval_subject())
        node = EvaluateStmt("EVALUATE", start.file, start.line, start.line)
        pending: list[dict[str, Any]] = []
        pending_line: Optional[int] = None
        pending_notes: list[str] = []
        while self.at_word("WHEN"):
            when_tok = self.next()
            if pending_line is None:
                pending_line = when_tok.line
            if self.accept_word("OTHER"):
                stmts = self.parse_statements()
                if pending:     # WHENs with no statements before OTHER do nothing
                    node.whens.append(WhenBranch(self._any(pending), pending_line, [], pending_notes))
                node.whens.append(WhenBranch(None, when_tok.line, stmts))
                pending, pending_line, pending_notes = [], None, []
                continue
            parts: list[dict[str, Any]] = []
            self._notes = []
            for k, subject in enumerate(subjects):
                if k > 0:
                    self.expect_word("ALSO")
                part = self._eval_object(subject)
                if part is not None:
                    parts.append(part)
            pending.append(parts[0] if len(parts) == 1 else {"all": parts})
            pending_notes.extend(self._notes)
            if self.at_word("WHEN"):
                continue        # stacked WHENs share the next statements
            stmts = self.parse_statements()
            node.whens.append(WhenBranch(self._any(pending), pending_line, stmts, pending_notes))
            pending, pending_line, pending_notes = [], None, []
        if self.at_word("END-EVALUATE"):
            self.next()
        node.line_end = self.last.line
        return node

    @staticmethod
    def _any(conds: list[dict[str, Any]]) -> dict[str, Any]:
        return conds[0] if len(conds) == 1 else {"any": conds}

    def _eval_subject(self) -> Any:
        if self.at_word("TRUE", "FALSE"):
            return self.next().value == "TRUE"
        return self.parse_operand()

    def _eval_object(self, subject: Any) -> Optional[dict[str, Any]]:
        if self.accept_word("ANY"):
            return None
        if isinstance(subject, bool):
            self._abbr = None
            cond = self._or()
            return cond if subject else {"not": cond}
        negate = bool(self.accept_word("NOT"))
        lo = self.parse_operand()
        if self.accept_word("THRU", "THROUGH"):
            hi = self.parse_operand()
            leaf = self._leaf_between(subject, lo, hi)
            return {"not": leaf} if negate else leaf
        return self._relation(subject, "!=" if negate else "==", lo)

    def parse_search(self) -> SearchStmt:
        start = self.next()
        self.accept_word("ALL")
        node = SearchStmt("SEARCH", start.file, start.line, start.line)
        while self.peek() is not None and not self.at_word("AT", "WHEN", "END") and not self.at_kind("PERIOD"):
            self.next()
        if self.accept_word("AT") or self.at_word("END"):
            self.accept_word("END")
            self.parse_statements()     # AT END actions are not decision rules
        while self.at_word("WHEN"):
            when_tok = self.next()
            cond, notes = self.parse_condition()
            stmts = self.parse_statements()
            node.whens.append(WhenBranch(cond, when_tok.line, stmts, notes))
        if self.at_word("END-SEARCH"):
            self.next()
        node.line_end = self.last.line
        return node

    def parse_exec(self) -> ExecStmt:
        start = self.next()
        kind = self.next().value if self.peek() is not None else ""
        while self.peek() is not None and not self.at_word("END-EXEC"):
            self.next()
        if self.at_word("END-EXEC"):
            self.next()
        return ExecStmt("EXEC", start.file, start.line, self.last.line, kind=kind)

    # ---- PERFORM ---------------------------------------------------------
    def parse_perform(self) -> PerformStmt:
        start = self.next()
        node = PerformStmt("PERFORM", start.file, start.line, start.line)
        t = self.peek()
        out_of_line = (t is not None and t.kind == "WORD"
                       and t.value not in ("UNTIL", "VARYING", "WITH", "TEST", "FOREVER")
                       and t.value not in VERBS and t.value not in LIST_STOP
                       and not self.at_word("TIMES", k=1))
        if out_of_line:
            node.target = self.next().value
            if self.accept_word("THRU", "THROUGH"):
                node.thru = self.next().value
        node.loop = self._perform_loop()
        if not out_of_line:
            node.body = self.parse_statements()
            if self.at_word("END-PERFORM"):
                self.next()
        node.line_end = self.last.line
        return node

    def _perform_loop(self) -> Optional[str]:
        parts: list[str] = []
        if self.at_word("WITH", "TEST"):            # [WITH] TEST BEFORE|AFTER
            self.accept_word("WITH")
            self.expect_word("TEST")
            parts.append("TEST " + self.next().value)
        if (self.at_kind("NUM") or self.at_kind("WORD")) and self.at_word("TIMES", k=1):
            parts.append(self.next().value + " TIMES"); self.next()
        elif self.accept_word("FOREVER"):
            parts.append("FOREVER")
        elif self.at_word("UNTIL", "VARYING"):
            while self.peek() is not None:
                if self.accept_word("UNTIL"):
                    cond, _ = self.parse_condition()
                    parts.append(f"UNTIL {cond}")
                    if not self.at_word("AFTER"):
                        break
                    continue
                t = self.peek()
                if t.kind == "PERIOD" or (t.kind == "WORD" and (t.value in VERBS or t.value in LIST_STOP)):
                    break
                parts.append(self.next().value)
        return " ".join(parts) or None

    # ---- simple statements -----------------------------------------------
    def _handler_at(self) -> Optional[int]:
        """Length of a conditional phrase (AT END, NOT ON SIZE ERROR, ...) starting here."""
        offset = 1 if self.at_word("NOT") else 0
        for phrase in HANDLER_PHRASES:
            if all(self.at_word(w, k=offset + n) for n, w in enumerate(phrase)):
                if phrase == ("INVALID",) and self.at_word("KEY", k=offset + 1):
                    continue
                return offset + len(phrase)
        return None

    def _collect(self, verb: str) -> tuple[list[Token], list[tuple[str, list[Stmt]]]]:
        """Tokens of a simple statement up to the next statement, plus its handler phrases."""
        main: list[Token] = []
        handlers: list[tuple[str, list[Stmt]]] = []
        end_word = "END-" + verb
        while self.peek() is not None:
            t = self.peek()
            if t.kind == "PERIOD":
                break
            if t.kind == "WORD":
                if t.value == end_word:
                    self.next()
                    break
                if t.value in LIST_STOP or t.value in VERBS:
                    break
                n = self._handler_at()
                if n is not None:
                    phrase = " ".join(self.next().value for _ in range(n))
                    stmts = self.parse_statements(extra_stop=lambda: self._handler_at() is not None)
                    handlers.append((phrase, stmts))
                    continue
            main.append(self.next())
        return main, handlers

    def parse_simple(self) -> Optional[Stmt]:
        start = self.next()
        verb = start.value
        if verb == "GO":
            self.accept_word("TO")
        if verb in ("CONTINUE", "GOBACK"):
            return OtherStmt(verb, start.file, start.line, start.line, verb)
        if verb == "STOP":
            self.accept_word("RUN")
            return OtherStmt("STOP RUN", start.file, start.line, self.last.line, "STOP RUN")
        if verb == "EXIT":
            while self.at_word("PROGRAM", "PARAGRAPH", "SECTION", "PERFORM", "CYCLE"):
                self.next()
            return OtherStmt("EXIT", start.file, start.line, self.last.line, "EXIT")
        toks, handlers = self._collect(verb)
        end = self.last.line
        text = verb + (" " + " ".join(self._tok_text(t) for t in toks) if toks else "")
        try:
            if verb == "MOVE":
                return self._move(start, toks, end)
            if verb in ("COMPUTE", "ADD", "SUBTRACT", "MULTIPLY", "DIVIDE"):
                return self._assign(start, verb, toks, end)
            if verb == "SET":
                return self._set(start, toks, end)
            if verb == "CALL":
                return self._call(start, toks, handlers, end)
            if verb == "GO":
                return GoToStmt("GO TO", start.file, start.line, end,
                                targets=[t.value for t in toks if t.kind == "WORD" and t.value not in ("DEPENDING", "ON")])
        except ParseError as exc:
            self.diags.append(f"statement kept as text: {exc}")
        return OtherStmt(verb, start.file, start.line, end, text, handlers)

    @staticmethod
    def _tok_text(t: Token) -> str:
        if t.kind == "STR":
            return "'" + t.value.replace("'", "''") + "'"
        return t.value

    def _sub(self, toks: list[Token]) -> Parser:
        p = Parser(toks, self.src)
        p.dict = self.dict
        p.last = toks[0] if toks else self.last
        return p

    def _idents(self, toks: list[Token]) -> list[Ident]:
        p = self._sub(toks)
        out: list[Ident] = []
        while p.peek() is not None:
            if p.at_word("ROUNDED"):
                p.next()
                continue
            op = p.parse_operand()
            if isinstance(op, Ident):
                out.append(op)
        return out

    def _split(self, toks: list[Token], *words: str) -> tuple[list[Token], Optional[str], list[Token]]:
        for k, t in enumerate(toks):
            if t.kind == "WORD" and t.value in words:
                return toks[:k], t.value, toks[k + 1:]
        return toks, None, []

    def _move(self, start: Token, toks: list[Token], end: int) -> MoveStmt:
        if toks and toks[0].is_word("CORRESPONDING", "CORR"):
            toks = toks[1:]
        src_toks, _, tgt_toks = self._split(toks, "TO")
        src = self._sub(src_toks).parse_operand()
        return MoveStmt("MOVE", start.file, start.line, end, source=src, targets=self._idents(tgt_toks))

    def _assign(self, start: Token, verb: str, toks: list[Token], end: int) -> AssignStmt:
        node = AssignStmt(verb, start.file, start.line, end)
        if verb == "COMPUTE":
            for k, t in enumerate(toks):
                if (t.kind == "OP" and t.value == "=") or t.is_word("EQUAL"):
                    targets = self._idents(toks[:k])
                    expr = self._sub(toks[k + 1:]).parse_operand()
                    expr = expr if isinstance(expr, Expr) else Expr(operand_text(expr), [expr.name] if isinstance(expr, Ident) else [])
                    node.assignments = [(tgt, expr) for tgt in targets]
                    return node
            raise ParseError("COMPUTE without '='", start)
        if toks and toks[0].is_word("CORRESPONDING", "CORR"):
            node.notes.append(f"{verb} CORRESPONDING")
            return node
        before_rem, rem, _ = self._split(toks, "REMAINDER")
        if rem:
            node.notes.append("DIVIDE REMAINDER")
            toks = before_rem
        # Expressions put the receiving field first: ADD A TO B -> B = B + A,
        # MULTIPLY A BY B -> B = B * A, DIVIDE A INTO B -> B = B / A.
        main, giving, giving_toks = self._split(toks, "GIVING")
        connector = {"ADD": ("TO",), "SUBTRACT": ("FROM",), "MULTIPLY": ("BY",), "DIVIDE": ("INTO", "BY")}[verb]
        left, word, right = self._split(main, *connector)
        a, b = self._operands(left), self._operands(right)
        a_txt, b_txt = [operand_text(o) for o in a], [operand_text(o) for o in b]
        names = [o.name for o in a + b if isinstance(o, Ident)]
        if giving:
            if verb == "ADD":
                expr = " + ".join(a_txt + b_txt)
            elif verb == "SUBTRACT":
                expr = " - ".join(b_txt[:1] + a_txt)
            elif verb == "MULTIPLY":
                expr = f"{a_txt[0]} * {b_txt[0]}"
            elif word == "INTO":
                expr = f"{b_txt[0]} / {a_txt[0]}"
            else:
                expr = f"{a_txt[0]} / {b_txt[0]}"
            node.assignments = [(tgt, Expr(expr, names)) for tgt in self._idents(giving_toks)]
            return node
        symbol = {"ADD": "+", "SUBTRACT": "-", "MULTIPLY": "*", "DIVIDE": "/"}[verb]
        for tgt in [o for o in b if isinstance(o, Ident)]:
            expr = f" {symbol} ".join([tgt.text] + a_txt)
            node.assignments.append((tgt, Expr(expr, [tgt.name] + [o.name for o in a if isinstance(o, Ident)])))
        return node

    def _operands(self, toks: list[Token]) -> list[Operand]:
        p = self._sub(toks)
        out: list[Operand] = []
        while p.peek() is not None:
            if p.at_word("ROUNDED"):
                p.next()
                continue
            out.append(p.parse_operand())
        return out

    def _set(self, start: Token, toks: list[Token], end: int) -> SetStmt:
        node = SetStmt("SET", start.file, start.line, end)
        left, word, rest = self._split(toks, "TO", "UP", "DOWN")
        node.targets = self._idents(left)
        if word == "TO":
            if rest and rest[0].is_word("TRUE", "FALSE"):
                node.truth = rest[0].value == "TRUE"
            else:
                node.value = self._sub(rest).parse_operand()
        elif word in ("UP", "DOWN"):
            if rest and rest[0].is_word("BY"):
                rest = rest[1:]
            node.step = (word, self._sub(rest).parse_operand())
        return node

    def _call(self, start: Token, toks: list[Token], handlers: list, end: int) -> CallStmt:
        node = CallStmt("CALL", start.file, start.line, end, handlers=handlers)
        if toks:
            node.program = toks[0].value
            _, _, using = self._split(toks[1:], "USING")
            node.using = [t.value for t in using if t.kind == "WORD"
                          and t.value not in ("BY", "REFERENCE", "CONTENT", "VALUE", "RETURNING", "OMITTED")]
        return node

    # ---- operands ----------------------------------------------------------
    def parse_operand(self) -> Operand:
        parts: list[str] = []
        idents: list[str] = []
        single = self._operand_term(parts, idents)
        count = 1
        while self.at_op("+", "-", "*", "/", "**"):
            parts.append(self.next().value)
            single = self._operand_term(parts, idents)
            count += 1
        if count == 1 and single is not None:
            return single
        return Expr(_join(parts), idents)

    def _operand_term(self, parts: list[str], idents: list[str]) -> Optional[Operand]:
        t = self.peek()
        if t is None:
            raise ParseError("expected operand", self.last)
        if t.kind == "LPAREN":
            self.next()
            parts.append("(")
            inner = self.parse_operand()
            if isinstance(inner, Expr):
                parts.append(inner.text)
                idents.extend(inner.idents)
            else:
                parts.append(operand_text(inner))
                if isinstance(inner, Ident):
                    idents.append(inner.name)
            if not self.at_kind("RPAREN"):
                raise ParseError("expected ')'", self.peek() or self.last)
            self.next()
            parts.append(")")
            return None
        if t.kind == "OP" and t.value in ("+", "-"):
            self.next()
            parts.append(t.value)
            self._operand_term(parts, idents)
            return None
        if t.kind in ("NUM", "STR"):
            lit = lit_from_token(self.next())
            parts.append(operand_text(lit))
            return lit
        if t.kind == "WORD":
            if t.value in FIGURATIVE:
                lit = lit_from_token(self.next())
                parts.append(lit.text)
                return lit
            if t.value == "ALL" and self.peek(1) is not None and self.peek(1).kind == "STR":
                self.next()
                lit = lit_from_token(self.next())
                parts.append(operand_text(lit))
                return lit
            if t.value == "FUNCTION":
                self.next()
                name = self.next().value
                text = "FUNCTION " + name
                if self.at_kind("LPAREN"):
                    text += self._paren_text()
                parts.append(text)
                return Expr(text, [])
            if t.value in CONDITION_WORDS:
                raise ParseError(f"expected operand, got {t.value!r}", t)
            ident = self._ident()
            parts.append(ident.text)
            idents.append(ident.name)
            return ident
        raise ParseError(f"expected operand, got {t.value!r}", t)

    def _paren_text(self) -> str:
        depth = 0
        out: list[str] = []
        while self.peek() is not None:
            t = self.next()
            if t.kind == "LPAREN":
                depth += 1
            elif t.kind == "RPAREN":
                depth -= 1
            out.append(self._tok_text(t))
            if depth == 0:
                break
        return _join(out)

    def _ident(self) -> Ident:
        """Identifier; ``text`` keeps subscripts but drops OF/IN qualification."""
        name_tok = self.next()
        text = name_tok.value
        qualifier = None
        while self.at_word("OF", "IN") and self.at_kind("WORD", k=1):
            self.next()
            qualifier = qualifier or self.next().value
        if self.at_kind("LPAREN") and self._looks_like_subscript():
            text += self._paren_text()
        return Ident(name_tok.value, text, qualifier)

    def _looks_like_subscript(self) -> bool:
        """'(' right after an identifier is a subscript/refmod, unless it starts a condition group."""
        depth, k = 0, 0
        while (t := self.peek(k)) is not None:
            if t.kind == "LPAREN":
                depth += 1
            elif t.kind == "RPAREN":
                depth -= 1
                if depth == 0:
                    return True
            elif t.kind == "WORD" and t.value in ("AND", "OR", "NOT", "GREATER", "LESS", "EQUAL"):
                return False
            elif t.kind == "OP" and t.value in ("=", "<", ">", "<=", ">=", "<>"):
                return False
            elif t.kind == "PERIOD":
                return False
            k += 1
        return False

    # ---- conditions --------------------------------------------------------
    def parse_condition(self) -> tuple[dict[str, Any], list[str]]:
        self._abbr = None
        self._notes = []
        cond = self._or()
        return cond, list(self._notes)

    def _or(self) -> dict[str, Any]:
        items = [self._and()]
        while self.at_word("OR"):
            self.next()
            items.append(self._and())
        if len(items) == 1:
            return items[0]
        flat: list[dict[str, Any]] = []
        for c in items:
            flat.extend(c["any"] if set(c) == {"any"} else [c])
        return {"any": flat}

    def _and(self) -> dict[str, Any]:
        items = [self._not()]
        while self.at_word("AND"):
            self.next()
            items.append(self._not())
        if len(items) == 1:
            return items[0]
        flat: list[dict[str, Any]] = []
        for c in items:
            flat.extend(c["all"] if set(c) == {"all"} else [c])
        return {"all": flat}

    def _not(self) -> dict[str, Any]:
        if self.at_word("NOT") and not (self._abbr is not None and self._relop_ahead(1)):
            self.next()
            inner = self._not()
            if set(inner) == {"not"}:
                return inner["not"]
            return {"not": inner}
        return self._primary()

    def _relop_ahead(self, k: int) -> bool:
        t = self.peek(k)
        if t is None:
            return False
        if t.kind == "OP" and t.value in ("=", "<", ">", "<=", ">=", "<>"):
            return True
        return t.kind == "WORD" and t.value in ("GREATER", "LESS", "EQUAL", "EQUALS")

    def _primary(self) -> dict[str, Any]:
        if self.at_kind("LPAREN"):
            save, save_abbr = self.i, self._abbr
            try:
                subject = self.parse_operand()
                if self._relop_ahead(0) or self.at_word("IS", "NOT"):
                    return self._after_subject(subject)
            except ParseError:
                pass
            self.i, self._abbr = save, save_abbr
            self.next()
            cond = self._or()
            if not self.at_kind("RPAREN"):
                raise ParseError("expected ')' closing condition", self.peek() or self.last)
            self.next()
            return cond
        # abbreviated relation: "... AND < 10" or "... AND NOT < 10"
        if self._abbr is not None and (self._relop_ahead(0) or (self.at_word("NOT") and self._relop_ahead(1))):
            op = self.parse_relop()
            obj = self.parse_operand()
            subject = self._abbr[0]
            self._abbr = (subject, op)
            return self._relation(subject, op, obj)
        t = self.peek()
        if t is not None and t.kind == "WORD":
            item = self.dict.condition_name(t.value)
            if item is not None and not self._relop_ahead(1) and not self.at_word("IS", k=1):
                self.next()
                return self._expand88(item)
        subject = self.parse_operand()
        return self._after_subject(subject)

    def _after_subject(self, subject: Operand) -> dict[str, Any]:
        save = self.i
        self.accept_word("IS")
        negate = bool(self.accept_word("NOT"))
        if self.at_word("NUMERIC", "ALPHABETIC", "ALPHABETIC-LOWER", "ALPHABETIC-UPPER"):
            word = self.next().value
            leaf = self._leaf(subject, "is_numeric" if word == "NUMERIC" else "is_alphabetic", None)
            if word in ("ALPHABETIC-LOWER", "ALPHABETIC-UPPER"):
                self._notes.append(word)
            return {"not": leaf} if negate else leaf
        if self.at_word("POSITIVE", "NEGATIVE", "ZERO", "ZEROS", "ZEROES"):
            word = self.next().value
            op = {"POSITIVE": "is_positive", "NEGATIVE": "is_negative"}.get(word, "is_zero")
            leaf = self._leaf(subject, op, None)
            return {"not": leaf} if negate else leaf
        self.i = save
        op = self.parse_relop()
        if op is not None:
            obj = self.parse_operand()
            self._abbr = (subject, op)
            return self._relation(subject, op, obj)
        # bare operand after a relation: abbreviated object, "A = 1 OR 2"
        if self._abbr is not None:
            return self._relation(self._abbr[0], self._abbr[1], subject)
        self._notes.append(f"unresolved condition {operand_text(subject)!r}")
        return self._leaf(subject, "==", "TRUE")

    def parse_relop(self) -> Optional[str]:
        save = self.i
        self.accept_word("IS")
        negate = bool(self.accept_word("NOT"))
        op: Optional[str] = None
        t = self.peek()
        if t is not None and t.kind == "OP" and t.value in ("=", "<", ">", "<=", ">=", "<>"):
            op = {"=": "==", "<>": "!="}.get(t.value, t.value)
            self.next()
        elif self.accept_word("GREATER", "LESS"):
            base = ">" if self.last.value == "GREATER" else "<"
            self.accept_word("THAN")
            if self.at_word("OR") and self.at_word("EQUAL", k=1):
                self.next(); self.next(); self.accept_word("TO")
                base += "="
            op = base
        elif self.accept_word("EQUAL", "EQUALS"):
            self.accept_word("TO")
            op = "=="
        if op is None:
            self.i = save
            return None
        return NEGATE[op] if negate else op

    # ---- condition leaves ----------------------------------------------------
    def _leaf(self, subject: Operand, op: str, value: Any) -> dict[str, Any]:
        if isinstance(subject, Expr):
            self._notes.append("arithmetic expression in condition")
        leaf: dict[str, Any] = {"source_name": operand_text(subject), "op": op}
        if value is not None:
            leaf["value"] = value
        return leaf

    def _relation(self, subject: Operand, op: str, obj: Operand) -> dict[str, Any]:
        if isinstance(subject, Lit) and not isinstance(obj, Lit):
            subject, obj, op = obj, subject, FLIP[op]
        if isinstance(subject, Lit):
            self._notes.append("comparison between two literals")
        if isinstance(obj, Lit):
            return self._leaf(subject, op, obj.value)
        if isinstance(obj, Expr):
            self._notes.append("arithmetic expression in condition")
        leaf = self._leaf(subject, op, None)
        leaf["ref"] = obj.text
        return leaf

    def _leaf_between(self, subject: Operand, lo: Operand, hi: Operand) -> dict[str, Any]:
        if isinstance(lo, Lit) and isinstance(hi, Lit):
            return self._leaf(subject, "between", [lo.value, hi.value])
        self._notes.append("THRU range with non-literal bounds")
        return self._leaf(subject, "between", [operand_text(lo), operand_text(hi)])

    def _expand88(self, item: DataItem) -> dict[str, Any]:
        parent = item.parent.name if item.parent is not None else item.name
        singles = [v[0] for v in item.values88 if len(v) == 1]
        ranges = [v for v in item.values88 if len(v) == 2]
        parts: list[dict[str, Any]] = []
        if len(singles) == 1:
            parts.append({"source_name": parent, "op": "==", "value": singles[0]})
        elif singles:
            parts.append({"source_name": parent, "op": "in", "value": singles})
        for lo, hi in ranges:
            parts.append({"source_name": parent, "op": "between", "value": [lo, hi]})
        if not parts:
            self._notes.append(f"88-level {item.name} has no VALUE")
            return {"source_name": parent, "op": "==", "value": None}
        return parts[0] if len(parts) == 1 else {"any": parts}


def parse_program(tokens: list[Token], src: ResolvedSource) -> Program:
    return Parser(tokens, src).parse_program()
