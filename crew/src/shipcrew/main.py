import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# (name, check command, fix hint, required)
CHECKS = [
    ("claude", ["claude", "--version"], "npm i -g @anthropic-ai/claude-code", True),
    ("git", ["git", "--version"], "sudo pacman -S git", True),
    ("node/npx", ["npx", "--version"], "sudo pacman -S nodejs npm", True),
    ("uv", ["uv", "--version"], "curl -LsSf https://astral.sh/uv/install.sh | sh", True),
    ("gh (logged in)", ["gh", "auth", "status"], "sudo pacman -S github-cli && gh auth login  (without it: local git only)", False),
    ("vercel (logged in)", ["vercel", "whoami"], "npm i -g vercel && vercel login  (without it: local mode, SQLite)", False),
    ("docker (for strix)", ["docker", "info"], "start docker: sudo systemctl start docker", False),
    ("strix (optional deep pentest)", ["strix", "--version"], "curl -sSL https://strix.ai/install | bash  (+ STRIX_LLM, LLM_API_KEY)", False),
    ("chrome-devtools MCP (e2e)", ["claude", "mcp", "get", "chrome-devtools"],
     "claude mcp add --scope user chrome-devtools -- npx -y chrome-devtools-mcp@latest", True),
    ("openpencil (brand .fig)", ["openpencil", "--version"], "npm i -g @open-pencil/cli", False),
]


def doctor() -> bool:
    ok = True
    for name, cmd, fix, required in CHECKS:
        good = shutil.which(cmd[0]) is not None and subprocess.run(
            cmd, capture_output=True, timeout=60).returncode == 0
        tag = "ok " if good else ("MISSING (required)" if required else "missing (optional)")
        print(f"{tag:20} {name}" + ("" if good else f"   -> {fix}"))
        ok &= good or not required
    return ok


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


def cli():
    p = argparse.ArgumentParser(prog="shipcrew")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    for c in ("run", "resume"):
        sub.add_parser(c).add_argument("project", type=Path)
    a = p.parse_args()
    if a.cmd == "doctor":
        sys.exit(0 if doctor() else 1)
    project = a.project.expanduser().resolve()
    (project / ".shipcrew").mkdir(parents=True, exist_ok=True)
    run(project, resume=a.cmd == "resume")
