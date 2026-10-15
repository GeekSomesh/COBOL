"""Command line, Python client and a guard against super-linear analysis time."""

import json
import sys
import time

from src.cli import main as cli
from tests.conftest import ROOT
from tests.test_api import H, TXNCHK, client, upload_and_extract  # noqa: F401  (fixture)

IBM = ROOT / "data" / "public" / "ibm-zopeneditor-sample"


def test_cli_extract_export_repo_and_diff(tmp_path, capsys):
    out = tmp_path / "rules.json"
    assert cli(["extract", str(IBM / "SAM2.cbl"), "-o", str(out)]) == 0
    assert len(json.loads(out.read_text())) == 19
    dmn = tmp_path / "sam2.dmn"
    assert cli(["export", str(IBM / "SAM2.cbl"), "--format", "dmn", "-o", str(dmn)]) == 0
    assert "<decisionTable" in dmn.read_text()
    assert cli(["repo", str(IBM), "--impact", "program:SAM2"]) == 0
    assert '"callers": [\n    "SAM1"' in capsys.readouterr().out
    for f in IBM.glob("*.cpy"):
        (tmp_path / f.name).write_text(f.read_text())
    (tmp_path / "SAM2.cbl").write_text((IBM / "SAM2.cbl").read_text().replace("'FIELD NAME INVALID'", "'BAD FIELD'"))
    assert cli(["diff", str(IBM / "SAM2.cbl"), str(tmp_path / "SAM2.cbl")]) == 0
    assert '"changed": 1' in capsys.readouterr().out


def test_sdk_drives_the_api(client):
    from src.sdk import ApiError, DecisionClient

    class Adapter:                             # route the SDK's requests to the in-process test client
        headers = {}

        def request(self, method, url, timeout=None, **kw):
            return client.request(method, url.replace("http://test", ""), headers=self.headers, **kw)

    api = DecisionClient("http://test", token="t-admin", session=Adapter())
    pid = api.upload(TXNCHK)["program_id"]
    api.wait(api.extract(pid)["job_id"], poll=0.2)
    rules = api.rules(program=pid)["items"]
    assert rules and api.rule(rules[0]["rule_id"])["rule_id"] == rules[0]["rule_id"]
    assert api.review(rules[0]["rule_id"], "approve", "matches policy")["status"] == "approved"
    assert "<definitions" in api.export("dmn")
    try:
        api.rule("NOPE-R001")
        raise AssertionError("expected an ApiError")
    except ApiError as e:
        assert e.status == 404 and e.code == "RULE_NOT_FOUND" and e.request_id


def test_analysis_time_grows_linearly():
    sys.path.insert(0, str(ROOT / "scripts"))
    from perf import measure
    small, big = measure(2000), measure(8000)
    t = lambda m: m["seconds"]["analyze (parse, slice, control flow)"] + m["seconds"]["rules (baseline)"]  # noqa: E731
    assert big["rules"] > 3 * small["rules"] and not big["flow_truncated"]
    assert t(big) < 8 * t(small) + 1.0          # 4x the code: well under quadratic growth
