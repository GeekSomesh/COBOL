"""Load COBOL source, normalise free/fixed format, and resolve COPY statements.

Every resolved line keeps the file and original line number it came from, so
traces always point at what the user wrote (implementation.md section 2.2,
step 1). Comments are removed from the code text but kept per line for the
data dictionary (naming support) and the LLM stage.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

Format = Literal["free", "fixed"]

COPY_EXTENSIONS = ("", ".cpy", ".CPY", ".cbl", ".CBL", ".cob", ".COB", ".copy")
MAX_COPY_DEPTH = 10


class IngestError(Exception):
    pass


@dataclass(frozen=True)
class SrcLine:
    text: str                 # code with comments removed
    file: str                 # file name (no directory)
    line: int                 # 1-based line in that file
    comment: str | None = None


@dataclass
class ResolvedSource:
    program_file: str
    fmt: Format
    lines: list[SrcLine]
    copybooks: list[str] = field(default_factory=list)
    raw: dict[str, list[str]] = field(default_factory=dict)   # file -> original lines
    sha256: str = ""

    def raw_text(self, file: str, start: int, end: int) -> str:
        lines = self.raw.get(file, [])
        return "\n".join(lines[start - 1:end])

    def comment_before(self, file: str, line: int) -> str | None:
        """Inline comment on ``line``, else the comment-only lines directly above it."""
        if getattr(self, "_by_line", None) is None:
            self._by_line = {(l.file, l.line): l for l in self.lines}
        by_line = self._by_line
        here = by_line.get((file, line))
        if here and here.comment:
            return here.comment
        found: list[str] = []
        n = line - 1
        while (cur := by_line.get((file, n))) is not None and not cur.text.strip() and cur.comment:
            found.append(cur.comment)
            n -= 1
        return " ".join(reversed(found)) if found else None


# ---------------------------------------------------------------- format ---

def detect_format(lines: Sequence[str]) -> Format:
    for raw in lines:
        upper = raw.upper()
        if ">>SOURCE" in upper:
            return "free" if "FREE" in upper else "fixed"
        m = re.search(r"\b(IDENTIFICATION|ID)\s+DIVISION", upper)
        if m:
            seq = raw[:6]
            if m.start() >= 7 and (seq.strip() == "" or seq.strip().isdigit()) and len(raw) > 6 and raw[6] == " ":
                return "fixed"
            return "free"
    return "free"


def _split_inline_comment(text: str) -> tuple[str, str | None]:
    """Split ``*>`` comments that are outside string literals."""
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "*" and text.startswith("*>", i):
            return text[:i], text[i + 2:].strip() or None
        i += 1
    return text, None


def _normalise_lines(file: str, raw_lines: Sequence[str], fmt: Format) -> list[SrcLine]:
    out: list[SrcLine] = []
    for idx, raw in enumerate(raw_lines, start=1):
        raw = raw.rstrip("\r\n")
        if fmt == "fixed":
            indicator = raw[6] if len(raw) > 6 else " "
            area = raw[7:72] if len(raw) > 7 else ""
            if indicator in "*/":
                out.append(SrcLine("", file, idx, area.strip() or None))
                continue
            if indicator in "Dd":       # debugging line, compiled out by default
                out.append(SrcLine("", file, idx, None))
                continue
            code, comment = _split_inline_comment(area)
            if indicator == "-" and out:
                _join_continuation(out, code)
                out.append(SrcLine("", file, idx, comment))
                continue
        else:
            stripped = raw.lstrip()
            if stripped.startswith(">>"):          # compiler directive
                out.append(SrcLine("", file, idx, None))
                continue
            code, comment = _split_inline_comment(raw)
        out.append(SrcLine(code.rstrip(), file, idx, comment))
    return out


def _join_continuation(out: list[SrcLine], code: str) -> None:
    """Fixed format '-' indicator: continue the previous code line."""
    for k in range(len(out) - 1, -1, -1):
        prev = out[k]
        if prev.text.strip():
            body = code.lstrip()
            if _open_quote(prev.text) and body[:1] in "'\"":
                joined = prev.text.ljust(65) + body[1:]
            else:
                joined = prev.text.rstrip() + body
            out[k] = SrcLine(joined, prev.file, prev.line, prev.comment)
            return


def _open_quote(text: str) -> bool:
    quote = None
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
    return quote is not None


# ------------------------------------------------------------------ COPY ---

_COPY_RE = re.compile(r"(?<![A-Za-z0-9-])COPY\s", re.IGNORECASE)
_PERIOD_RE = re.compile(r"\.(\s|$)")


def _find_copy(text: str) -> int | None:
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            continue
        if ch in "cC" and _COPY_RE.match(text, i) and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "-")):
            return i
    return None


def _copy_statement_end(text: str) -> int | None:
    """Index just after the terminating period of a COPY statement, ignoring == pseudo-text ==."""
    i, in_pseudo = 0, False
    while i < len(text):
        if text.startswith("==", i):
            in_pseudo = not in_pseudo
            i += 2
            continue
        if not in_pseudo and text[i] == "." and (i + 1 == len(text) or text[i + 1].isspace()):
            return i + 1
        i += 1
    return None


_PSEUDO_OR_WORD = re.compile(r"==(.*?)==|([A-Za-z0-9:][A-Za-z0-9:_-]*)", re.DOTALL)


def _parse_copy(stmt: str) -> tuple[str, list[tuple[str, str, bool]]]:
    """Return (copybook name, [(from, to, is_pseudo_text)])."""
    body = stmt.strip().rstrip(".").strip()
    m = re.match(r"COPY\s+(\"[^\"]+\"|'[^']+'|[A-Za-z0-9_-]+)(.*)$", body, re.IGNORECASE | re.DOTALL)
    if not m:
        raise IngestError(f"bad COPY statement: {stmt!r}")
    name = m.group(1).strip("'\"")
    rest = m.group(2)
    rest = re.sub(r"^\s*(OF|IN)\s+[A-Za-z0-9_-]+", "", rest, flags=re.IGNORECASE)
    rest = re.sub(r"^\s*SUPPRESS(\s+PRINTING)?", "", rest, flags=re.IGNORECASE)
    replacing: list[tuple[str, str, bool]] = []
    rm = re.match(r"\s*REPLACING\s+(.*)$", rest, re.IGNORECASE | re.DOTALL)
    if rm:
        items = [(mm.group(1), mm.group(2)) for mm in _PSEUDO_OR_WORD.finditer(rm.group(1))]
        operands = [(p if p is not None else w, p is not None) for p, w in items
                    if not (w and w.upper() in ("BY", "LEADING", "TRAILING"))]
        for k in range(0, len(operands) - 1, 2):
            (src, pseudo), (dst, _) = operands[k], operands[k + 1]
            replacing.append((src.strip(), dst.strip(), pseudo))
    return name, replacing


def _apply_replacing(text: str, replacing: list[tuple[str, str, bool]]) -> str:
    for src, dst, pseudo in replacing:
        if not src:
            continue
        if pseudo and not re.fullmatch(r"[A-Za-z0-9-]+", src):
            # partial-word pseudo text such as ==:PFX:== (IBM-style tag), or multi-token text
            pattern = r"\s+".join(re.escape(tok) for tok in src.split())
            text = re.sub(pattern, lambda _m: dst, text, flags=re.IGNORECASE)
        else:
            text = re.sub(rf"(?<![A-Za-z0-9-]){re.escape(src)}(?![A-Za-z0-9-])",
                          lambda _m: dst, text, flags=re.IGNORECASE)
    return text


def find_copybook(name: str, copy_dirs: Sequence[Path]) -> Path:
    for d in copy_dirs:
        for ext in COPY_EXTENSIONS:
            for candidate in (Path(d) / f"{name}{ext}", Path(d) / f"{name.upper()}{ext}", Path(d) / f"{name.lower()}{ext}"):
                if candidate.is_file():
                    return candidate
    raise IngestError(f"copybook {name!r} not found in {[str(d) for d in copy_dirs]}")


def _resolve(lines: list[SrcLine], fmt: Format, copy_dirs: Sequence[Path], depth: int,
             result: ResolvedSource) -> list[SrcLine]:
    if depth > MAX_COPY_DEPTH:
        raise IngestError("COPY nesting too deep (cycle?)")
    out: list[SrcLine] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        pos = _find_copy(ln.text)
        if pos is None:
            out.append(ln)
            i += 1
            continue
        before = ln.text[:pos]
        stmt = ln.text[pos:]
        j = i
        while (end := _copy_statement_end(stmt)) is None:
            j += 1
            if j >= len(lines):
                raise IngestError(f"unterminated COPY at {ln.file}:{ln.line}")
            stmt += " " + lines[j].text
        after = stmt[end:]
        name, replacing = _parse_copy(stmt[:end])
        path = find_copybook(name, copy_dirs)
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
        result.raw[path.name] = raw
        if path.name not in result.copybooks:
            result.copybooks.append(path.name)
        cb_lines = _normalise_lines(path.name, raw, fmt)
        if replacing:
            cb_lines = [SrcLine(_apply_replacing(l.text, replacing), l.file, l.line, l.comment) for l in cb_lines]
        if before.strip():
            out.append(SrcLine(before, ln.file, ln.line, ln.comment))
        out.extend(_resolve(cb_lines, fmt, copy_dirs, depth + 1, result))
        if after.strip():
            out.append(SrcLine(after, lines[j].file, lines[j].line, None))
        i = j + 1
    return out


def load_program(path: Path | str, copy_dirs: Sequence[Path | str] = (), fmt: Format | None = None) -> ResolvedSource:
    path = Path(path)
    raw_bytes = path.read_bytes()
    raw = raw_bytes.decode("utf-8", errors="replace").splitlines()
    fmt = fmt or detect_format(raw)
    dirs = [Path(d) for d in copy_dirs] or [path.parent]
    if path.parent not in dirs:
        dirs.append(path.parent)
    result = ResolvedSource(program_file=path.name, fmt=fmt, lines=[],
                            raw={path.name: raw}, sha256="sha256:" + hashlib.sha256(raw_bytes).hexdigest())
    result.lines = _resolve(_normalise_lines(path.name, raw, fmt), fmt, dirs, 0, result)
    return result
