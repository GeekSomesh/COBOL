"""Repository analysis: how programs, copybooks, JCL jobs and datasets connect.

``scan(folder)`` parses every COBOL program and JCL member under a folder;
``build_graph`` turns that into nodes and edges:

    program --CALLS--> program          (static CALL 'X', or CALL ws-name resolved from its VALUE)
    program --INCLUDES--> copybook
    job step --RUNS--> program          (EXEC PGM=X)
    job step --BUILDS--> program        (compiler SYSIN member, binder INCLUDE / NAME)
    program --READS / WRITES--> dataset (SELECT ... ASSIGN TO dd, OPEN mode, and the step's DD)

plus findings a reviewer should see: unresolved calls, steps that run programs that are
not in the repository, PROGRAM-IDs defined twice, copybooks nobody includes.
``impact`` answers "what is affected if this field / copybook / program / dataset changes".
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from src.analysis.ast import CallStmt, OtherStmt
from src.repo.jcl import Job, parse_jcl_file

COBOL_EXT = {".cbl", ".cob", ".cobol", ".cbl.txt"}
COPY_EXT = {".cpy", ".copy", ".cpb"}
JCL_EXT = {".jcl", ".prc", ".proc"}


@dataclass
class ProgramInfo:
    program_id: str
    file: str
    kind: str                                   # main / subprogram
    calls: list[dict[str, Any]] = field(default_factory=list)
    copybooks: list[str] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)   # SELECT name, ASSIGN dd, OPEN modes
    using: list[str] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)   # rule_id, reads, writes (COBOL names)
    error: Optional[str] = None


@dataclass
class Repo:
    root: str
    programs: list[ProgramInfo] = field(default_factory=list)
    jobs: list[Job] = field(default_factory=list)
    copybook_files: list[str] = field(default_factory=list)


def _walk_stmts(stmts):
    from src.verify.harness import walk_all
    yield from walk_all(stmts)


_SELECT = re.compile(r"\bSELECT\s+(?:OPTIONAL\s+)?([A-Z0-9-]+)\s+ASSIGN\s+(?:TO\s+)?(?:'([^']+)'|\"([^\"]+)\"|([A-Z0-9-]+))",
                     re.I)


def _rule_fields(rule: Any) -> tuple[set[str], set[str]]:
    from src.rules.decide import _leaves
    reads = {l["source_name"].split("(")[0].strip().upper() for l in _leaves(rule.conditions, [])}
    for l in _leaves(rule.conditions, []):
        if l.get("ref"):
            reads.add(str(l["ref"]).split("(")[0].strip().upper())
    writes = set()
    for a in rule.actions + rule.else_actions:
        if a.type in ("set", "compute") and a.source_name:
            writes.add(a.source_name.split("(")[0].strip().upper())
            if a.expr:
                reads.update(t for t in re.findall(r"[A-Z][A-Z0-9-]*", a.expr.upper()))
    return reads, writes


def program_info(path: Path, copy_dirs: Iterable[Path]) -> ProgramInfo:
    from src.pipeline import extract
    try:
        ex = extract(path, list(copy_dirs))
    except Exception as exc:                      # report, never stop the scan
        return ProgramInfo(path.stem.upper(), path.name, "unknown", error=str(exc)[:300])
    a = ex.analysis
    prog = a.program
    info = ProgramInfo(prog.program_id, path.name, "subprogram" if prog.using else "main",
                       copybooks=[Path(c).stem.upper() for c in a.source.copybooks], using=list(prog.using))
    seen: set[tuple[str, bool]] = set()
    for para in prog.paragraphs:
        for s in _walk_stmts(para.statements):
            if isinstance(s, CallStmt) and s.program:
                target, dynamic = s.program.upper(), s.dynamic
                if (target, dynamic) in seen:
                    continue
                seen.add((target, dynamic))
                resolved = target
                if dynamic:                       # CALL WS-NAME: the data item's VALUE names the program
                    item = prog.dictionary.lookup(target)
                    resolved = str(item.value).strip().upper() if item is not None and isinstance(item.value, str) \
                        and item.value.strip() else None
                info.calls.append({"target": target, "dynamic": dynamic, "resolved": resolved, "line": s.line_start})
    text = "\n".join(l.text for l in a.source.lines) if hasattr(a.source.lines[0], "text") else \
        "\n".join(a.source.raw.get(a.source.program_file, []))
    modes: dict[str, set[str]] = defaultdict(set)
    for para in prog.paragraphs:
        for s in _walk_stmts(para.statements):
            if isinstance(s, OtherStmt) and s.verb == "OPEN":
                mode = None
                for w in s.text.upper().split()[1:]:
                    if w in ("INPUT", "OUTPUT", "I-O", "EXTEND"):
                        mode = w
                    elif mode:
                        modes[w].add(mode)
    for m in _SELECT.finditer(text.upper()):
        assign = (m.group(2) or m.group(3) or m.group(4) or "").upper()
        dd = assign.split("-")[-1] if assign.startswith(("UT-S-", "S-")) else assign
        info.files.append({"select": m.group(1).upper(), "assign": dd, "modes": sorted(modes.get(m.group(1).upper(), []))})
    for r in ex.rules:
        reads, writes = _rule_fields(r)
        info.rules.append({"rule_id": r.rule_id, "paragraph": r.trace.paragraph, "reads": sorted(reads),
                           "writes": sorted(writes)})
    return info


def scan(folder: Path | str) -> Repo:
    root = Path(folder)
    files = [p for p in sorted(root.rglob("*")) if p.is_file()]
    copy_dirs = sorted({p.parent for p in files if p.suffix.lower() in COPY_EXT} | {root})
    repo = Repo(str(root), copybook_files=[p.stem.upper() for p in files if p.suffix.lower() in COPY_EXT])
    for p in files:
        suffix = p.suffix.lower()
        if suffix in COBOL_EXT:
            repo.programs.append(program_info(p, copy_dirs))
        elif suffix in JCL_EXT:
            repo.jobs.extend(parse_jcl_file(p))
    return repo


def scan_sources(programs: list[tuple[Path, list[Path]]], jcl: list[Path], copybooks: list[str],
                 root: str = "") -> Repo:
    """Like ``scan`` for sources that do not share one folder (the API's uploads)."""
    repo = Repo(root, copybook_files=sorted(set(copybooks)))
    for path, dirs in programs:
        repo.programs.append(program_info(path, dirs))
    for j in jcl:
        repo.jobs.extend(parse_jcl_file(j))
    return repo


def build_graph(repo: Repo) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    def node(nid: str, kind: str, label: str, **extra: Any) -> str:
        nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label, **extra})
        return nid

    by_id: dict[str, list[ProgramInfo]] = defaultdict(list)
    for p in repo.programs:
        by_id[p.program_id].append(p)
    for pid, defs in by_id.items():
        node(f"program:{pid}", "program", pid, files=[d.file for d in defs], kind_detail=defs[0].kind,
             rules=sum(len(d.rules) for d in defs))
        if len(defs) > 1:
            findings.append({"kind": "duplicate_program_id", "program": pid, "files": [d.file for d in defs],
                             "detail": f"{len(defs)} sources define PROGRAM-ID {pid}"})
    included: set[str] = set()
    for p in repo.programs:
        src = f"program:{p.program_id}"
        for c in p.calls:
            target = c["resolved"]
            if target is None:
                findings.append({"kind": "unresolved_call", "program": p.program_id, "file": p.file,
                                 "detail": f"CALL {c['target']} (line {c['line']}): target held in a data item without a VALUE"})
                continue
            if target not in by_id:
                node(f"program:{target}", "external", target)
                findings.append({"kind": "external_call", "program": p.program_id, "file": p.file,
                                 "detail": f"CALL {target} (line {c['line']}): not in the repository"})
            edges.append({"type": "CALLS", "from": src, "to": f"program:{target}", "dynamic": c["dynamic"],
                          "file": p.file, "line": c["line"]})
        for cb in p.copybooks:
            included.add(cb)
            edges.append({"type": "INCLUDES", "from": src, "to": node(f"copybook:{cb}", "copybook", cb), "file": p.file})
        if p.error:
            findings.append({"kind": "parse_error", "program": p.program_id, "file": p.file, "detail": p.error})
    for cb in repo.copybook_files:
        node(f"copybook:{cb}", "copybook", cb)
        if cb not in included:
            findings.append({"kind": "unused_copybook", "copybook": cb, "detail": f"no program includes {cb}"})
    for job in repo.jobs:
        jid = node(f"job:{job.name}", "job", job.name, file=job.file)
        for st in job.steps:
            sid = node(f"step:{job.name}.{st.name}", "step", f"{job.name}.{st.name}", pgm=st.pgm, proc=st.proc,
                       line=st.line, utility=st.utility)
            edges.append({"type": "HAS_STEP", "from": jid, "to": sid})
            for m in st.members():
                if m in by_id:
                    edges.append({"type": "BUILDS", "from": sid, "to": f"program:{m}"})
            if st.pgm and not st.utility:
                if st.pgm not in by_id:
                    node(f"program:{st.pgm}", "external", st.pgm)
                    findings.append({"kind": "missing_program", "job": job.name, "step": st.name,
                                     "detail": f"step {st.name} runs {st.pgm}, which is not in the repository"})
                edges.append({"type": "RUNS", "from": sid, "to": f"program:{st.pgm}"})
                for prog in by_id.get(st.pgm, []):
                    for f in prog.files:
                        dd = st.dds.get(f["assign"])
                        if dd is None:
                            findings.append({"kind": "missing_dd", "job": job.name, "step": st.name,
                                             "detail": f"{st.pgm} assigns {f['select']} to {f['assign']}, "
                                                       f"but step {st.name} has no {f['assign']} DD"})
                            continue
                        for dsn in dd.dsn:
                            did = node(f"dataset:{dsn}", "dataset", dsn)
                            modes = f["modes"] or (["OUTPUT"] if dd.disp in ("NEW", "MOD") else ["INPUT"])
                            for mode in modes:
                                kind = "READS" if mode == "INPUT" else "WRITES" if mode in ("OUTPUT", "EXTEND") else "UPDATES"
                                edges.append({"type": kind, "from": f"program:{st.pgm}", "to": did,
                                              "via": f"{job.name}.{st.name} DD {dd.name}", "file": f["select"]})
    unique: dict[tuple, dict[str, Any]] = {}
    for e in edges:                       # one edge per relation (duplicate sources repeat them)
        unique.setdefault((e["type"], e["from"], e["to"], e.get("via"), e.get("file")), e)
    edges = list(unique.values())
    return {"nodes": list(nodes.values()), "edges": edges, "findings": findings,
            "summary": {"programs": len(by_id), "jobs": len(repo.jobs), "copybooks": len(repo.copybook_files),
                        "datasets": sum(1 for n in nodes.values() if n["kind"] == "dataset"),
                        "rules": sum(len(p.rules) for p in repo.programs)}}


# ---- impact analysis --------------------------------------------------------------------

def _callers(graph: dict[str, Any], target: str) -> list[str]:
    """Programs that reach ``target`` through CALL chains (transitive), nearest first."""
    rev: dict[str, set[str]] = defaultdict(set)
    for e in graph["edges"]:
        if e["type"] == "CALLS":
            rev[e["to"]].add(e["from"])
    out, frontier, seen = [], [target], {target}
    while frontier:
        nxt = []
        for t in frontier:
            for c in sorted(rev.get(t, ())):
                if c not in seen:
                    seen.add(c)
                    out.append(c)
                    nxt.append(c)
        frontier = nxt
    return out


def impact(repo: Repo, graph: dict[str, Any], kind: str, name: str) -> dict[str, Any]:
    """Programs, rules and jobs affected by a change to a field, copybook, program or dataset."""
    name = name.strip().upper()
    programs: set[str] = set()
    rules: list[dict[str, Any]] = []
    if kind == "field":
        for p in repo.programs:
            for r in p.rules:
                how = [w for w, names in (("reads", r["reads"]), ("writes", r["writes"])) if name in names]
                if how:
                    programs.add(f"program:{p.program_id}")
                    rules.append({"program": p.program_id, "file": p.file, "rule_id": r["rule_id"],
                                  "paragraph": r["paragraph"], "how": how})
    elif kind == "copybook":
        for p in repo.programs:
            if name in p.copybooks:
                programs.add(f"program:{p.program_id}")
                rules += [{"program": p.program_id, "file": p.file, "rule_id": r["rule_id"],
                           "paragraph": r["paragraph"], "how": ["in a program that includes the copybook"]}
                          for r in p.rules]
    elif kind == "program":
        programs.add(f"program:{name}")
    elif kind == "dataset":
        for e in graph["edges"]:
            if e["to"] == f"dataset:{name}" and e["type"] in ("READS", "WRITES", "UPDATES"):
                programs.add(e["from"])
    else:
        raise ValueError(f"unknown impact kind {kind!r}: field, copybook, program or dataset")
    direct = sorted(programs)
    callers = [c for p in direct for c in _callers(graph, p) if c not in programs]
    affected = direct + list(dict.fromkeys(callers))
    steps = sorted({e["from"] for e in graph["edges"] if e["type"] == "RUNS" and e["to"] in affected})
    jobs = sorted({s.split(":", 1)[1].split(".")[0] for s in steps})
    return {"target": {"kind": kind, "name": name},
            "programs": [p.split(":", 1)[1] for p in direct],
            "callers": [c.split(":", 1)[1] for c in dict.fromkeys(callers)],
            "rules": rules, "job_steps": [s.split(":", 1)[1] for s in steps], "jobs": jobs}
