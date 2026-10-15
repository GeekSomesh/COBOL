"""Read z/OS JCL: jobs, steps, the program each step runs and the datasets bound to its DDs.

Enough of the language for dependency analysis, not for execution:

* ``//name JOB``, ``//name EXEC PGM=X`` / ``EXEC procname`` / ``EXEC PROC=name``, ``//name DD``
  (with unnamed ``//  DD`` concatenations), ``// SET SYM=value`` and ``&SYM`` / ``&SYM..``
  substitution, operand continuation after a trailing comma, ``//*`` comments,
  in-stream data (``DD *`` / ``DD DATA`` up to ``/*``);
* the ``DSN`` (or ``DSNAME``) and the first ``DISP`` value of each DD; ``SYSOUT=`` DDs;
* the members compiled and linked by compiler / binder steps (``SYSIN DSN=...(SAM2)``,
  in-stream ``INCLUDE OBJ(SAM2)``), so build jobs link back to programs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

UTILITIES = {"IGYCRCTL", "IEWL", "IEWBLINK", "HEWL", "IEFBR14", "IEBGENER", "IEBCOPY", "IDCAMS", "SORT",
             "ICEMAN", "IKJEFT01", "IKJEFT1B", "DFHECP1$", "DSNHPC", "IEBUPDTE", "ASMA90", "IBMZPLI"}
COMPILERS = {"IGYCRCTL", "ASMA90", "IBMZPLI", "DSNHPC"}
BINDERS = {"IEWL", "IEWBLINK", "HEWL"}


@dataclass
class DD:
    name: str
    dsn: list[str] = field(default_factory=list)       # concatenation order
    disp: Optional[str] = None                        # first DISP value: SHR / OLD / NEW / MOD
    sysout: bool = False
    instream: list[str] = field(default_factory=list)


@dataclass
class Step:
    name: str
    line: int
    pgm: Optional[str] = None
    proc: Optional[str] = None
    dds: dict[str, DD] = field(default_factory=dict)

    @property
    def utility(self) -> bool:
        return (self.pgm or "") in UTILITIES

    def members(self) -> list[str]:
        """Programs compiled or link-edited by this step (build jobs)."""
        out: list[str] = []
        if self.pgm in COMPILERS and "SYSIN" in self.dds:
            out += [m.group(1) for d in self.dds["SYSIN"].dsn for m in [re.search(r"\(([A-Z0-9#@$]+)\)$", d)] if m]
        if self.pgm in BINDERS:
            for dd in self.dds.values():
                for line in dd.instream:
                    m = re.match(r"\s*(?:INCLUDE\s+\w+\(([A-Z0-9#@$]+)\)|NAME\s+([A-Z0-9#@$]+))", line.upper())
                    if m:
                        out.append(m.group(1) or m.group(2))
        return list(dict.fromkeys(out))


@dataclass
class Job:
    name: str
    file: str
    steps: list[Step] = field(default_factory=list)
    symbols: dict[str, str] = field(default_factory=dict)


def _substitute(text: str, symbols: dict[str, str]) -> str:
    def rep(m: re.Match) -> str:
        return symbols.get(m.group(1), m.group(0))
    return re.sub(r"&([A-Z0-9#@$]+)\.?", rep, text)


def _operands(text: str) -> dict[str, str]:
    """KEY=VALUE operands, honouring quotes and parentheses."""
    out: dict[str, str] = {}
    depth, quote, cur, parts = 0, False, "", []
    for ch in text:
        if ch == "'" :
            quote = not quote
        elif not quote and ch == "(":
            depth += 1
        elif not quote and ch == ")":
            depth -= 1
        if ch == "," and depth == 0 and not quote:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    for p in parts:
        k, sep, v = p.partition("=")
        if sep:
            out[k.strip().upper()] = v.strip()
        elif p.strip():
            out.setdefault("_", p.strip())
    return out


def parse_jcl(text: str, file: str = "") -> list[Job]:
    jobs: list[Job] = []
    job: Optional[Job] = None
    step: Optional[Step] = None
    lines = text.splitlines()
    i = 0
    last_dd: Optional[DD] = None
    while i < len(lines):
        raw = lines[i].rstrip()
        i += 1
        if not raw.startswith("//") or raw.startswith("//*"):
            continue
        body = raw[2:72] if len(raw) > 72 else raw[2:]
        lineno = i
        # gather continuations: operand field ending with a comma continues on the next // line
        while body.rstrip().endswith(",") and i < len(lines) and lines[i].startswith("//") \
                and not lines[i].startswith("//*"):
            body = body.rstrip() + lines[i][2:72].strip()
            i += 1
        name, _, rest = (body + " ").partition(" ") if not body.startswith(" ") else ("", "", body)
        rest = rest.strip()
        op, _, operands = rest.partition(" ")
        operands = re.sub(r"\s+\S.*$", "", operands.strip()) if not operands.strip().startswith("'") else operands.strip()
        op = op.upper()
        symbols = job.symbols if job else {}
        if op == "JOB":
            job = Job(name.upper(), file)
            jobs.append(job)
            step = None
        elif op == "SET":
            for k, v in _operands(operands).items():
                if k != "_":
                    symbols[k] = v.strip("'").split()[0] if v.strip("'") else ""
        elif op == "EXEC":
            if job is None:
                job = Job(Path(file).stem.upper() or "JOB", file)
                jobs.append(job)
            ops = _operands(_substitute(operands, symbols))
            pgm = ops.get("PGM")
            proc = ops.get("PROC") or (ops.get("_") if not pgm else None)
            step = Step(name.upper() or f"STEP{len(job.steps) + 1}", lineno, pgm.upper() if pgm else None,
                        proc.upper() if proc else None)
            job.steps.append(step)
        elif op == "DD" and step is not None:
            ops = _operands(_substitute(operands, symbols))
            if name:
                last_dd = step.dds.setdefault(name.upper(), DD(name.upper()))
            dd = last_dd
            if dd is None:
                continue
            dsn = ops.get("DSN") or ops.get("DSNAME")
            if dsn:
                dd.dsn.append(dsn.upper().strip("'"))
            if "DISP" in ops and dd.disp is None:
                dd.disp = ops["DISP"].strip("()").split(",")[0].upper() or None
            if "SYSOUT" in ops:
                dd.sysout = True
            if ops.get("_", "").upper() in ("*", "DATA"):
                while i < len(lines) and not lines[i].startswith(("/*", "//")):
                    dd.instream.append(lines[i])
                    i += 1
    return jobs


def parse_jcl_file(path: Path) -> list[Job]:
    return parse_jcl(Path(path).read_text(encoding="utf-8", errors="replace"), Path(path).name)
