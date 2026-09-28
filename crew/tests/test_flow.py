"""Orchestration check with a fake Claude: build loop, QA reopen, deploy verification, report.
Run: uv run --project crew python crew/tests/test_flow.py"""
import json
import tempfile
from pathlib import Path

from shipcrew import flow

calls = []


def fake_claude(prompt, cwd, role="", **kw):
    sc = Path(cwd) / ".shipcrew"
    calls.append(prompt.split("\n")[0])
    if "QA lead" in role:
        qa_runs = sum("QA" in c for c in calls)
        res = {"pass": qa_runs > 1, "failures": [] if qa_runs > 1 else [{"feature": "F02", "problem": "x"}]}
        (sc / "qa.json").write_text(json.dumps(res))
    if "release engineer" in role:
        (sc / "deploy.json").write_text(json.dumps({"url": "https://demo.vercel.app", "ok": True}))
    return "did it\nPASS" if "F01" in prompt or "F02" in prompt else "FAIL: nope"


def main():
    import os
    os.environ["SHIPCREW_LOCAL"] = "0"
    flow.claude = fake_claude
    flow.probe = lambda url: "ok"
    with tempfile.TemporaryDirectory() as d:
        sc = Path(d) / ".shipcrew"
        sc.mkdir()
        (sc / "prd.md").write_text("demo")
        feats = [{"id": i, "title": i, "acceptance": ["a"]} for i in ("F01", "F02", "F03")]
        (sc / "features.json").write_text(json.dumps({"stack": "next", "needs_db": False, "features": feats}))
        f = flow.ShipFlow()
        f.kickoff(inputs={"project_dir": d, "done": ["plan"]})
        out = json.loads((sc / "features.json").read_text())["features"]
        assert [x["passes"] for x in out] == [True, True, False], out
        assert out[2]["attempts"] == flow.MAX_FEATURE_ATTEMPTS  # F03 gave up, didn't loop forever
        assert sum("F02" in c for c in calls) == 2  # reopened once by QA
        assert f.state.demo_url == "https://demo.vercel.app"
        report = (sc / "REPORT.md").read_text()
        assert "2/3 passing" in report and "https://demo.vercel.app" in report
        assert flow.next_feature([{"passes": True, "attempts": 0}]) is None
    # local mode: no deploy session, straight to local fallback
    calls.clear()
    os.environ["SHIPCREW_LOCAL"] = "1"
    flow.subprocess.Popen = lambda *a, **k: None
    with tempfile.TemporaryDirectory() as d:
        sc = Path(d) / ".shipcrew"
        sc.mkdir()
        (sc / "prd.md").write_text("demo")
        (sc / "features.json").write_text(json.dumps({"stack": "next", "needs_db": True, "features": []}))
        f = flow.ShipFlow()
        f.kickoff(inputs={"project_dir": d, "done": ["plan"]})
        assert f.state.local and f.state.demo_url.startswith("http://localhost"), f.state.demo_url
        assert not any("Deploy" in c for c in calls), calls
    print("ok")


if __name__ == "__main__":
    main()
