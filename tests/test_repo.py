"""Repository analysis on IBM's samples: JCL, call graph, datasets, impact; rule drift."""

from pathlib import Path

import pytest

from src.pipeline import extract
from src.repo.graph import build_graph, impact, scan
from src.repo.jcl import parse_jcl, parse_jcl_file
from src.rules.diff import diff_rules
from tests.conftest import ROOT

IBM = ROOT / "data" / "public" / "ibm-zopeneditor-sample"


@pytest.fixture(scope="module")
def ibm():
    repo = scan(IBM)
    return repo, build_graph(repo)


def test_jcl_steps_symbols_and_build_members():
    job = parse_jcl_file(IBM / "RUN.jcl")[0]
    assert job.name == "ZDERUN" and job.symbols["HLQ"] == "IBMUSER"
    steps = {s.name: s for s in job.steps}
    assert steps["CMPLSAM2"].members() == ["SAM2"] and steps["LINKSAM1"].members() == ["SAM1"]
    run = steps["SAM1"]
    assert run.pgm == "SAM1" and run.dds["CUSTFILE"].dsn == ["IBMUSER.SAMPLE.CUSTFILE"]
    assert run.dds["CUSTOUT"].disp == "NEW"
    assert steps["LINKSAM1"].dds["SYSLIB"].dsn == ["CEE.SCEELKED", "IBMUSER.SAMPLE.LOAD"]   # concatenation


def test_jcl_continuations_and_proc_calls():
    text = "//J1 JOB 1\n//S1 EXEC PGM=MYPROG,\n//   PARM='X,Y'\n//IN DD DSN=A.B.C,\n//  DISP=SHR\n//S2 EXEC NIGHTLY\n"
    job = parse_jcl(text)[0]
    assert [(s.name, s.pgm, s.proc) for s in job.steps] == [("S1", "MYPROG", None), ("S2", None, "NIGHTLY")]
    assert job.steps[0].dds["IN"].dsn == ["A.B.C"] and job.steps[0].dds["IN"].disp == "SHR"


def test_call_graph_datasets_and_findings(ibm):
    repo, g = ibm
    calls = [(e["from"], e["to"], e["dynamic"]) for e in g["edges"] if e["type"] == "CALLS"]
    assert ("program:SAM1", "program:SAM2", True) in calls          # CALL SAM2: data item with VALUE 'SAM2'
    io = {(e["type"], e["to"]) for e in g["edges"] if e["from"] == "program:SAM1" and e["type"] in ("READS", "WRITES")}
    assert io == {("READS", "dataset:IBMUSER.SAMPLE.CUSTFILE"), ("READS", "dataset:IBMUSER.SAMPLE.TRANFILE"),
                  ("WRITES", "dataset:IBMUSER.SAMPLE.CUSTOUT"), ("WRITES", "dataset:IBMUSER.SAMPLE.CUSTRPT")}
    kinds = {f["kind"] for f in g["findings"]}
    assert {"duplicate_program_id", "missing_program"} <= kinds      # SAM1 + SAM1LIB; ASAM1 not in the repo


def test_impact_of_programs_fields_and_datasets(ibm):
    repo, g = ibm
    sam2 = impact(repo, g, "program", "SAM2")
    assert sam2["callers"] == ["SAM1"] and sam2["jobs"] == ["ZDERUN"]
    field = impact(repo, g, "field", "TRAN-ACTION")
    assert set(field["programs"]) == {"SAM2"} and all("reads" in r["how"] for r in field["rules"])
    assert impact(repo, g, "copybook", "TRANREC")["programs"] == ["SAM1", "SAM2"]
    assert impact(repo, g, "dataset", "IBMUSER.SAMPLE.CUSTOUT")["jobs"] == ["ZDERUN"]


def test_drift_reports_changed_rules_with_a_witness(tmp_path):
    for f in IBM.glob("*.cpy"):
        (tmp_path / f.name).write_text(f.read_text())
    text = (IBM / "SAM2.cbl").read_text().replace("IF TRAN-UPDATE-NUMX1 = '+'", "IF TRAN-UPDATE-NUMX1 = '#'", 1)
    (tmp_path / "SAM2.cbl").write_text(text)
    old = extract(IBM / "SAM2.cbl", [IBM]).rules
    new = extract(tmp_path / "SAM2.cbl", [tmp_path]).rules
    d = diff_rules(old, new)
    assert d["summary"]["changed"] == 1 and d["summary"]["added"] == d["summary"]["removed"] == 0
    change = d["changed"][0]
    assert change["changes"] == ["TRAN-UPDATE-NUMX1 ==: + -> #"]
    assert change["example_where_they_differ"]["TRAN-UPDATE-NUMX1"] in ("+", "#")
