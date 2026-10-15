"""Differential testing of CALLed subprograms (PROCEDURE DIVISION USING ...).

A subprogram has no ACCEPT/DISPLAY of its own: the caller passes records by
reference and reads back whatever the subprogram changed. To test one against
GnuCOBOL we generate a small driver program:

* its WORKING-STORAGE is the subprogram's LINKAGE SECTION, copied verbatim
  (COPY ... REPLACING included), so both see the same record layouts;
* it ACCEPTs each parameter record as one line of bytes, CALLs the subprogram,
  and DISPLAYs every parameter field the subprogram can change.

The harness side works on whole records in bytes (src/verify/storage.py), so
REDEFINES views of the same bytes (SAM2's TRAN-UPDATE-NUM / TRAN-UPDATE-NUMX1)
and OCCURS elements are generated, simulated and compared consistently.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional, Sequence

from src.analysis.analyze import Analysis
from src.analysis.ast import AssignStmt, MoveStmt, SetStmt
from src.verify.engine import base, is_numeric_entry, store
from src.verify.harness import Harness, loop_bounds, raw_tests, walk_all
from src.verify.storage import Storage


def written_fields(analysis: Analysis) -> set[str]:
    """Base names of every field a statement of the program may write."""
    out: set[str] = set()
    d = analysis.program.dictionary
    for para in analysis.program.paragraphs:
        for s in walk_all(para.statements):
            if isinstance(s, (MoveStmt, SetStmt)):
                for t in s.targets:
                    item = d.lookup(t.name)
                    if item is not None and item.is_condition and item.parent is not None:
                        out.add(item.parent.name)          # SET cond-name TO TRUE writes its field
                    else:
                        out.add(base(t.text))
            elif isinstance(s, AssignStmt):
                out.update(base(t.text) for t, _ in s.assignments)
    return out


def build_subprogram_harness(analysis: Analysis) -> Optional[Harness]:
    program = analysis.program
    if not program.using:
        return None
    d = program.dictionary
    params = []
    for name in program.using:
        item = d.lookup(name)
        if item is None or item.parent is not None:
            return None
        params.append(item.name)
    st = Storage(d, roots=params)
    layout: list[dict[str, Any]] = []
    for root in params:
        cells = st.fields(root)
        if not cells or any(not c.display for c in cells if not c.alias):
            return None                    # binary/packed parameters cannot be passed as text
        for c in cells:
            if c.display:
                layout.append({"name": c.key, "pic": c.item.pic, "length": c.length, "entry": c.entry,
                               "alias": c.alias, "root": root})
    # outputs: parameter fields the subprogram writes, plus every view of the same bytes
    written = written_fields(analysis)
    touched: set[str] = set()
    for root in params:
        for c in st.by_root.get(root, []):
            if base(c.key) in written:
                touched.add(c.key)
    for key in list(touched):
        touched.update(st.overlaps.get(key, []))
    changed = [c.key for root in params for c in st.fields(root)
               if c.key in touched and c.display and not c.key.startswith("FILLER#")]
    if not changed:
        return None
    # compare whole parameter records byte for byte (a group DISPLAY shows raw bytes); an
    # elementary numeric parameter is shown formatted, so it is compared as a number
    outputs = [root for root in params if any(st.cells[k].root == root for k in changed)]
    fields = d.entries()
    initial: dict[str, Any] = {}
    for name, entry in fields.items():
        if entry.get("defined_in") and name not in st.cells:
            if "value" in entry:
                initial[name] = store(entry["value"] if entry["value"] != " " else "", entry)
            elif is_numeric_entry(entry):
                initial[name] = store(0, entry)
            else:
                initial[name] = ""
    conds = [s.condition for s in analysis.slices]
    return Harness(params[0], layout, outputs, fields, initial, [], storage=st, records=params,
                   loop_bounds=loop_bounds(analysis), raw_tests=raw_tests(conds))


# ---- the driver program ------------------------------------------------------------------

def _linkage_text(source: str) -> str:
    lines = source.splitlines()
    start = end = None
    for i, line in enumerate(lines):
        code = line[6:72] if len(line) > 6 and not line.lstrip().startswith("*>") else line
        if start is None and re.search(r"\bLINKAGE\s+SECTION\b", code.upper()):
            start = i + 1
        elif start is not None and re.search(r"\bPROCEDURE\s+DIVISION\b", code.upper()):
            end = i
            break
    if start is None or end is None:
        raise ValueError("no LINKAGE SECTION")
    return "\n".join(lines[start:end])


def driver_source(analysis: Analysis, h: Harness, program_text: str, fixed: bool = True) -> str:
    pid = analysis.program.program_id
    ind = "       " if fixed else ""
    body = "           " if fixed else "    "
    out = [f"{ind}IDENTIFICATION DIVISION.", f"{ind}PROGRAM-ID. DRV{pid[:24]}.",
           f"{ind}DATA DIVISION.", f"{ind}WORKING-STORAGE SECTION.", _linkage_text(program_text),
           f"{ind}PROCEDURE DIVISION."]
    for root in h.records:
        out.append(f"{body}ACCEPT {root}")
    out.append(f"{body}CALL '{pid}' USING")
    out += [f"{body}    {root}" for root in h.records]
    for o in h.outputs:                  # whole records: DISPLAY of a group prints its raw bytes
        out.append(f"{body}DISPLAY '{o}='")
        out.append(f"{body}    {o}")
    out.append(f"{body}STOP RUN.")
    return "\n".join(out) + "\n"


def build_driver(analysis: Analysis, h: Harness, program_path: Path, copy_dirs: Sequence[Path],
                 toolchain: Any, out_dir: Path) -> Optional[Path]:
    """Compile driver + subprogram into one executable; None when it does not compile."""
    from src.common.cobol import CompileJob
    fixed = analysis.source.fmt != "free"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = analysis.source.sha256[7:19]
    drv = out_dir / f"DRV{analysis.program.program_id}-{tag}.cbl"
    drv.write_text(driver_source(analysis, h, Path(program_path).read_text(encoding="utf-8", errors="replace"),
                                 fixed), encoding="utf-8")
    exe = out_dir / f"DRV{analysis.program.program_id}-{tag}"
    dirs = [Path(d) for d in copy_dirs] or [Path(program_path).parent]
    res = toolchain.compile_many([CompileJob(drv, exe, dirs, extra=(Path(program_path),))], free=not fixed)[0]
    return exe if res.ok else None
