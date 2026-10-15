"""Command line: ``cobol2rules`` (installed by ``pip install -e .``) or ``python -m src.cli``.

    cobol2rules extract PROGRAM.cbl [-I copybooks/] [--verify] [--model qwen2.5-coder:7b] [-o rules.json]
    cobol2rules export  PROGRAM.cbl --format dmn|pmml|tree|json [-I dir] [-o out]
    cobol2rules repo    FOLDER [--impact field:TRAN-CODE] [--json graph.json]
    cobol2rules diff    OLD.cbl NEW.cbl [-I dir]
    cobol2rules serve   [--host 127.0.0.1] [--port 8000]

``extract`` without ``--verify`` is the parser only (fast, deterministic). With
``--verify`` the GnuCOBOL differential test and the other checks run (needs a
toolchain: native cobc, WSL or Docker); ``--model`` adds LLM titles and intents
through a local Ollama model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional


def _dirs(program: Path, includes: Optional[list[str]]) -> list[Path]:
    return [Path(d) for d in includes] if includes else [program.parent]


def _write(text: str, out: Optional[str]) -> None:
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(f"wrote {out}", file=sys.stderr)
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def cmd_extract(a: argparse.Namespace) -> int:
    from src.common.models import dump
    from src.pipeline import PipelineConfig, extract, run_pipeline
    program = Path(a.program)
    dirs = _dirs(program, a.include)
    if a.verify or a.model:
        toolchain = None
        if a.verify:
            from src.common.cobol import Toolchain, ToolchainError
            try:
                toolchain = Toolchain.detect()
            except ToolchainError as exc:
                print(f"warning: {exc}; behavioural checks skipped", file=sys.stderr)
        client = None
        if a.model:
            from src.llm.client import OllamaClient
            client = OllamaClient(a.model)
        cfg = PipelineConfig(model=a.model, fewshot=a.fewshot, differential=toolchain is not None)
        ex = run_pipeline(program, dirs, cfg, toolchain, client)
    else:
        ex = extract(program, dirs)
    rules = [dump(r) for r in ex.rules]
    _write(json.dumps(rules, indent=2), a.out)
    statuses: dict[str, int] = {}
    for r in ex.rules:
        statuses[r.status] = statuses.get(r.status, 0) + 1
    print(f"{len(rules)} rules from {program.name}: {statuses}", file=sys.stderr)
    return 0


def cmd_export(a: argparse.Namespace) -> int:
    from src.pipeline import extract
    from src.rules.dmn import to_dmn
    from src.rules.export import to_json, to_pmml, to_tree_json
    from src.rules.schema_check import validate
    program = Path(a.program)
    rules = extract(program, _dirs(program, a.include)).rules
    if a.format == "json":
        _write(to_json(rules), a.out)
    elif a.format == "tree":
        _write(json.dumps(to_tree_json(rules), indent=2), a.out)
    else:
        xml, skipped = to_pmml(rules) if a.format == "pmml" else to_dmn(rules, program.stem)
        if not xml:
            print("no rule set can be expressed in PMML: " + "; ".join(skipped), file=sys.stderr)
            return 1
        errors = validate(xml, a.format)
        if errors:
            print("schema validation failed: " + "; ".join(errors[:3]), file=sys.stderr)
            return 1
        _write(xml, a.out)
        for s in skipped:
            print(f"skipped: {s}", file=sys.stderr)
    return 0


def cmd_repo(a: argparse.Namespace) -> int:
    from src.repo.graph import build_graph, impact, scan
    repo = scan(a.folder)
    graph = build_graph(repo)
    s = graph["summary"]
    print(f"{s['programs']} programs, {s['jobs']} JCL jobs, {s['copybooks']} copybooks, {s['datasets']} datasets, "
          f"{s['rules']} rules")
    for e in graph["edges"]:
        if e["type"] in ("CALLS", "RUNS", "READS", "WRITES"):
            print(f"  {e['from']} --{e['type']}--> {e['to']}" + (f"  ({e['via']})" if e.get("via") else ""))
    for f in graph["findings"]:
        print(f"  finding: {f['kind']}: {f['detail']}")
    if a.impact:
        kind, _, name = a.impact.partition(":")
        print(json.dumps(impact(repo, graph, kind, name), indent=2))
    if a.json:
        Path(a.json).write_text(json.dumps(graph, indent=2), encoding="utf-8")
    return 0


def cmd_diff(a: argparse.Namespace) -> int:
    from src.pipeline import extract
    from src.rules.diff import diff_rules
    old, new = Path(a.old), Path(a.new)
    d = diff_rules(extract(old, _dirs(old, a.include)).rules, extract(new, _dirs(new, a.include)).rules)
    print(json.dumps(d["summary"]))
    for c in d["changed"]:
        print(f"changed {c['old']} -> {c['new']}: " + "; ".join(c["changes"])
              + (f"  e.g. {json.dumps(c['example_where_they_differ'])}" if c["example_where_they_differ"] else ""))
    for x in d["added"]:
        print(f"added {x['rule_id']}: {x['when']}")
    for x in d["removed"]:
        print(f"removed {x['rule_id']}")
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn
    root = Path(__file__).resolve().parents[1]
    uvicorn.run("src.api.main:app", host=a.host, port=a.port, app_dir=str(root), server_header=False)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="cobol2rules", description="Extract decision rules from COBOL.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract", help="rules of one program as JSON")
    e.add_argument("program")
    e.add_argument("-I", "--include", action="append", help="copybook folder (repeatable)")
    e.add_argument("--verify", action="store_true", help="GnuCOBOL differential test and confidence routing")
    e.add_argument("--model", help="Ollama model for titles and intents, e.g. qwen2.5-coder:7b")
    e.add_argument("--fewshot", type=int, default=3)
    e.add_argument("-o", "--out")
    x = sub.add_parser("export", help="JSON, decision trees, PMML 4.4 or DMN 1.3")
    x.add_argument("program")
    x.add_argument("--format", choices=["json", "tree", "pmml", "dmn"], default="dmn")
    x.add_argument("-I", "--include", action="append")
    x.add_argument("-o", "--out")
    r = sub.add_parser("repo", help="call graph, JCL jobs and datasets of a folder")
    r.add_argument("folder")
    r.add_argument("--impact", help="kind:name, e.g. field:TRAN-CODE, program:SAM2, copybook:TRANREC")
    r.add_argument("--json", help="write the graph as JSON")
    d = sub.add_parser("diff", help="rule drift between two versions of a program")
    d.add_argument("old")
    d.add_argument("new")
    d.add_argument("-I", "--include", action="append")
    s = sub.add_parser("serve", help="run the API and UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    a = ap.parse_args(argv)
    return {"extract": cmd_extract, "export": cmd_export, "repo": cmd_repo, "diff": cmd_diff,
            "serve": cmd_serve}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
