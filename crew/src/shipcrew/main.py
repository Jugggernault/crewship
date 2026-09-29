import argparse
import shutil
import subprocess
import sys
from pathlib import Path

def run(project: Path, resume: bool):
    from .flow import ShipFlow
    sc = project / ".shipcrew"
    if not (sc / "prd.md").exists():
        sys.exit(f"{sc / 'prd.md'} missing — run /shipcrew:ship first")
    run_id = sc / "run_id"
    flow = ShipFlow()
    inputs = {"project_dir": str(project)}
    if resume and run_id.exists():
        inputs["id"] = run_id.read_text().strip()  # @persist restores state; done stages are skipped
    result = flow.kickoff(inputs=inputs)
    run_id.write_text(flow.state.id)
    print(result)


def status(project: Path):
    """Where is the run: stages done, features, open PRs, cost, last activity."""
    import json
    import time
    from .claude import spent
    from .tools import session_env
    sc = project / ".shipcrew"
    state = json.loads((sc / "status.json").read_text()) if (sc / "status.json").exists() else {}
    print(f"stage: {state.get('stage', '?')}   done: {', '.join(state.get('done', [])) or '-'}")
    if (sc / "features.json").exists():
        for f in json.loads((sc / "features.json").read_text())["features"]:
            mark = "x" if f.get("passes") else ("!" if f.get("blocked") else " ")
            print(f"  [{mark}] {f['id']} {f['title'][:60]}  {f.get('track', '')} {('PR #' + str(f['pr'])) if f.get('pr') else ''}")
    for s in state.get("sessions", {}).values():
        idle = int(time.time() - s.get("last", 0))
        print(f"  session {s['label']}: {s.get('doing', '')[:70]}  (idle {idle}s)")
    print(f"cost: ${spent(project):.2f}")
    if shutil.which("gh", path=session_env()["PATH"]):
        subprocess.run(["gh", "pr", "list"], cwd=project, env=session_env())


def cli():
    p = argparse.ArgumentParser(prog="shipcrew")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor")
    d.add_argument("--fix", action="store_true", help="install what can be installed without sudo")
    for c in ("run", "resume", "status"):
        sub.add_parser(c).add_argument("project", type=Path)
    a = p.parse_args()
    if a.cmd == "doctor":
        from .tools import doctor
        sys.exit(0 if doctor(fix=a.fix) else 1)
    project = a.project.expanduser().resolve()
    if a.cmd == "status":
        return status(project)
    (project / ".shipcrew").mkdir(parents=True, exist_ok=True)
    run(project, resume=a.cmd == "resume")
