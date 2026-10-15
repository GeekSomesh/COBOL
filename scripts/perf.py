"""Performance on large programs: time every stage on generated COBOL of growing size.

    python scripts/perf.py                      # 1k, 5k, 20k and 50k lines -> results/perf.json
    python scripts/perf.py --lines 100000

The generated program has the shape of real batch code: a main paragraph that
PERFORMs many paragraphs (some through THRU ranges and GO TO exits), nested IF /
EVALUATE decisions over shared working-storage fields, COMPUTEs feeding later
tests, and a VALUE-initialised lookup table searched with SEARCH.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def generate(target_lines: int, seed: int = 7) -> str:
    r = random.Random(seed)
    nfields = 60
    data = ["       01  WS-IN."] + [f"           05  IN-F{i:03d}  PIC 9(5)." for i in range(nfields)]
    data += ["       01  WS-OUT."] + [f"           05  OUT-F{i:03d} PIC X VALUE 'N'." for i in range(nfields)]
    data += ["       01  WS-WORK."] + [f"           05  WK-F{i:03d}  PIC 9(7) VALUE 0." for i in range(nfields)]
    data += ["       01  CODE-VALUES.",
             "           05  FILLER PIC X(4) VALUE 'A010'.", "           05  FILLER PIC X(4) VALUE 'B020'.",
             "           05  FILLER PIC X(4) VALUE 'C030'.",
             "       01  CODE-TABLE REDEFINES CODE-VALUES.",
             "           05  CODE-ENTRY OCCURS 3 TIMES INDEXED BY CX.",
             "               10  CODE-KEY  PIC X.", "               10  CODE-RATE PIC 999.",
             "       01  WS-CODE PIC X.", "       01  WS-RATE PIC 999 VALUE 0."]
    paras: list[list[str]] = []
    n = 0
    lines_so_far = len(data) + 20
    while lines_so_far < target_lines:
        name = f"P{n:05d}"
        f = lambda: r.randrange(nfields)            # noqa: E731
        body = [f"       {name}."]
        kind = n % 4
        if kind == 0:
            a, b, c = f(), f(), f()
            body += [f"           COMPUTE WK-F{a:03d} = IN-F{b:03d} * 2 + IN-F{c:03d}",
                     f"           IF WK-F{a:03d} > {r.randrange(100, 9000)}",
                     f"               IF IN-F{f():03d} < {r.randrange(10, 900)}",
                     f"                   MOVE 'Y' TO OUT-F{f():03d}",
                     "               ELSE",
                     f"                   MOVE 'R' TO OUT-F{f():03d}",
                     "               END-IF",
                     "           END-IF."]
        elif kind == 1:
            x = f()
            body += [f"           EVALUATE TRUE",
                     f"               WHEN IN-F{x:03d} >= {r.randrange(500, 900)}",
                     f"                   MOVE 'H' TO OUT-F{f():03d}",
                     f"               WHEN IN-F{x:03d} >= {r.randrange(100, 499)}",
                     f"                   MOVE 'M' TO OUT-F{f():03d}",
                     "               WHEN OTHER",
                     f"                   MOVE 'L' TO OUT-F{f():03d}",
                     "           END-EVALUATE."]
        elif kind == 2:
            body += [f"           IF IN-F{f():03d} = 0",
                     f"               GO TO {name}-EXIT",
                     "           END-IF",
                     f"           ADD 1 TO WK-F{f():03d}.",
                     f"       {name}-EXIT.",
                     "           EXIT."]
        else:
            body += ["           SET CX TO 1",
                     "           SEARCH CODE-ENTRY",
                     "               AT END MOVE 0 TO WS-RATE",
                     "               WHEN CODE-KEY (CX) = WS-CODE",
                     "                   MOVE CODE-RATE (CX) TO WS-RATE",
                     "           END-SEARCH."]
        paras.append(body)
        lines_so_far += len(body) + 1
        n += 1
    main = ["       MAIN-PARA.", "           ACCEPT WS-IN"]
    for i, body in enumerate(paras):
        name = body[0].strip().rstrip(".")
        thru = f" THRU {name}-EXIT" if i % 4 == 2 else ""
        cond = f"           IF IN-F{i % nfields:03d} > 10\n    " if i % 7 == 0 else ""
        main.append(f"{cond}           PERFORM {name}{thru}")
        if i % 7 == 0:
            main.append("           END-IF")
    main += ["           STOP RUN."]
    text = ["       IDENTIFICATION DIVISION.", "       PROGRAM-ID. BIGPROG.", "       DATA DIVISION.",
            "       WORKING-STORAGE SECTION."] + data + ["       PROCEDURE DIVISION."] + main
    for body in paras:
        text += body
    return "\n".join(text) + "\n"


def measure(lines: int) -> dict:
    from src.analysis.analyze import analyze_program
    from src.rules.baseline import baseline_rule
    from src.rules.dmn import to_dmn
    from src.verify.checks import satisfiable
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "BIGPROG.cbl"
        path.write_text(generate(lines), encoding="utf-8")
        actual = len(path.read_text().splitlines())
        t = {}
        t0 = time.perf_counter()
        a = analyze_program(path)
        t["analyze (parse, slice, control flow)"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        rules = [baseline_rule(s, a.source.sha256) for s in a.slices]
        t["rules (baseline)"] = time.perf_counter() - t0
        sample = rules[: min(200, len(rules))]
        t0 = time.perf_counter()
        for r, s in zip(sample, a.slices):
            satisfiable(r, s)
        per_rule = (time.perf_counter() - t0) / max(1, len(sample))
        t["smt satisfiability per rule"] = per_rule
        t0 = time.perf_counter()
        xml, skipped = to_dmn(rules, "BIGPROG")
        t["dmn export"] = time.perf_counter() - t0
    return {"lines": actual, "paragraphs": len(a.program.paragraphs), "rules": len(rules),
            "diagnostics": len(a.diagnostics), "flow_truncated": a.flow.truncated if a.flow else None,
            "seconds": {k: round(v, 4) for k, v in t.items()},
            "rules_per_second": round(len(rules) / max(1e-9, t["analyze (parse, slice, control flow)"] + t["rules (baseline)"]), 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lines", type=int, nargs="*", default=[1000, 5000, 20000, 50000])
    args = ap.parse_args()
    out = []
    for n in args.lines:
        m = measure(n)
        out.append(m)
        print(json.dumps(m))
    (ROOT / "results" / "perf.json").write_text(json.dumps({"runs": out}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
