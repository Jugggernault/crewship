"""PRD -> live demo. CrewAI Flow = deterministic orchestration + persisted state.
Reasoning (planning) is a CrewAI Crew; hands-on work is headless Claude Code sessions.
Every artefact lives in the target repo under .shipcrew/ so any session can pick up cold."""
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import yaml
from crewai import Agent, Crew, Process, Task
from crewai.flow.flow import Flow, listen, router, start
from crewai.flow.persistence import persist
from pydantic import BaseModel, Field

from .claude import ClaudeCodeLLM, claude

CONFIG = Path(__file__).parent / "config"
ROLES = yaml.safe_load((CONFIG / "roles.yaml").read_text())
MAX_FEATURE_ATTEMPTS = 3
QA_ROUNDS = 3
DEPLOY_ATTEMPTS = 3


class Feature(BaseModel):
    id: str
    title: str
    acceptance: list[str]
    passes: bool = False
    attempts: int = 0


class Backlog(BaseModel):
    stack: str
    needs_db: bool
    data_model: str = ""
    features: list[Feature]


class ShipState(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_dir: str = ""
    done: list[str] = []  # completed stages, makes resume idempotent
    demo_url: str = ""
    local: bool = False  # no Vercel/Neon: SQLite + local server
    deploy_note: str = ""


def role(name: str) -> str:
    return ROLES["common"] + "\n" + ROLES[name]


def next_feature(features: list[dict]) -> dict | None:
    """First feature not passing that still has attempts left, in backlog order."""
    return next((f for f in features if not f["passes"] and f["attempts"] < MAX_FEATURE_ATTEMPTS), None)


def reopen(features: list[dict], failures: list[dict]) -> int:
    """QA failures send their features back to the build loop with fresh attempts."""
    ids = {x.get("feature") for x in failures}
    n = 0
    for f in features:
        if f["id"] in ids:
            f["passes"], f["attempts"] = False, 0
            n += 1
    return n


def works(*cmd: str) -> bool:
    return shutil.which(cmd[0]) is not None and subprocess.run(cmd, capture_output=True, timeout=60).returncode == 0


def probe(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return "ok" if r.status < 400 else f"http {r.status}"
    except urllib.error.HTTPError as e:
        return "protected" if e.code in (401, 403) else f"http {e.code}"
    except Exception as e:  # DNS, TLS, timeout
        return f"down: {e}"


@persist()
class ShipFlow(Flow[ShipState]):

    @property
    def dir(self) -> Path:
        return Path(self.state.project_dir)

    @property
    def sc(self) -> Path:
        return self.dir / ".shipcrew"

    def stage(self, name: str) -> bool:
        """True if the stage still has to run."""
        return name not in self.state.done

    def finish(self, name: str):
        self.state.done.append(name)
        with open(self.sc / "progress.md", "a") as f:
            f.write(f"\n## [shipcrew] stage `{name}` done\n")

    def features(self) -> list[dict]:
        # agents may rewrite features.json by hand: fill defaults via the model
        return [Feature(**f).model_dump() for f in json.loads((self.sc / "features.json").read_text())["features"]]

    def save_features(self, feats: list[dict]):
        data = json.loads((self.sc / "features.json").read_text())
        data["features"] = feats
        (self.sc / "features.json").write_text(json.dumps(data, indent=2))

    @start()
    def plan(self):
        if not self.stage("plan"):
            return
        prd = (self.sc / "prd.md").read_text()
        cfg_a = yaml.safe_load((CONFIG / "agents.yaml").read_text())
        cfg_t = yaml.safe_load((CONFIG / "tasks.yaml").read_text())
        llm = ClaudeCodeLLM(model="claude-code", cwd=self.dir)
        pm = Agent(**cfg_a["product_manager"], llm=llm, allow_delegation=False)
        arch = Agent(**cfg_a["architect"], llm=llm, allow_delegation=False)
        t1 = Task(description=cfg_t["backlog_task"]["description"],
                  expected_output=cfg_t["backlog_task"]["expected_output"], agent=pm)
        t2 = Task(description=cfg_t["architecture_task"]["description"],
                  expected_output=cfg_t["architecture_task"]["expected_output"], agent=arch,
                  context=[t1], output_pydantic=Backlog)
        out = Crew(agents=[pm, arch], tasks=[t1, t2], process=Process.sequential).kickoff(inputs={"prd": prd})
        (self.sc / "features.json").write_text(out.pydantic.model_dump_json(indent=2))
        self.finish("plan")

    @listen(plan)
    def design(self):
        if self.stage("design"):
            claude("Make DESIGN.md lint-clean and produce the brand board.", self.dir, role("designer"))
            self.finish("design")

    @listen(design)
    def scaffold(self):
        if self.stage("scaffold"):
            # SHIPCREW_LOCAL=1 forces local, =0 forces cloud, unset = auto from CLI logins
            env = os.environ.get("SHIPCREW_LOCAL")
            self.state.local = env == "1" or (env != "0" and not works("vercel", "whoami"))
            notes = []
            if not works("gh", "auth", "status"):
                notes.append("gh is UNAVAILABLE: skip GitHub entirely (local git repo only, no `vercel git connect`).")
            if self.state.local:
                notes.append("LOCAL MODE: no Vercel, no Neon. Persist with SQLite via Drizzle (better-sqlite3, "
                             "file ./data/app.db, gitignored); init.sh must migrate+seed it.")
            claude(f"Scaffold this project. Repo name: {self.dir.name}.\n" + "\n".join(notes),
                   self.dir, role("scaffolder"))
            self.finish("scaffold")

    @listen(scaffold)
    def build_and_qa(self):
        if not self.stage("build"):
            return
        for _ in range(QA_ROUNDS):
            while (f := next_feature(feats := self.features())) is not None:
                f["attempts"] += 1
                self.save_features(feats)
                out = claude(f"Implement feature {f['id']} — {f['title']}.\nAcceptance:\n- "
                             + "\n- ".join(f["acceptance"]), self.dir, role("developer"))
                f["passes"] = out.strip().splitlines()[-1:] == ["PASS"]
                self.save_features(feats)
            claude("Run full QA now.", self.dir, role("qa"))
            qa = json.loads((self.sc / "qa.json").read_text()) if (self.sc / "qa.json").exists() else {"pass": False}
            if qa.get("pass"):
                break
            feats = self.features()
            if not reopen(feats, qa.get("failures", [])):
                break  # failures not tied to a feature: nothing actionable for the loop
            self.save_features(feats)
        self.finish("build")

    @listen(build_and_qa)
    def secure(self):
        if not self.stage("secure"):
            return
        if shutil.which("strix") and shutil.which("docker"):
            # ponytail: static scan of the repo; a live-URL scan after deploy is the upgrade path
            r = subprocess.run(["strix", "-n", "--target", str(self.dir)], capture_output=True, text=True, timeout=5400)
            (self.sc / "security-raw.txt").write_text(r.stdout[-50_000:] + r.stderr[-5_000:])
        claude("Review and fix security issues.", self.dir, role("security"))
        self.finish("secure")

    @router(secure)
    def deploy(self):
        if not self.stage("deploy"):
            return "live" if self.state.demo_url.startswith("https") else "local"
        if self.state.local:
            self.state.deploy_note = "local mode"
            self.finish("deploy")
            return "local"
        for _ in range(DEPLOY_ATTEMPTS):
            claude("Deploy to production and verify.", self.dir, role("devops"))
            d = json.loads((self.sc / "deploy.json").read_text()) if (self.sc / "deploy.json").exists() else {}
            url = d.get("url", "")
            status = probe(url) if url.startswith("http") else "no url"
            self.state.deploy_note = f"{status}; {d.get('note', '')}"
            if status in ("ok", "protected"):  # don't trust the agent: the URL must answer
                self.state.demo_url = url
                self.finish("deploy")
                return "live"
        self.finish("deploy")
        return "local"

    @listen("local")
    def run_local(self):
        log = open(self.sc / "local.log", "a")
        subprocess.Popen(["bash", "init.sh"], cwd=self.dir, stdout=log, stderr=log, start_new_session=True)
        self.state.demo_url = "http://localhost:3000 (local fallback: " + self.state.deploy_note + ")"
        self.report()

    @listen("live")
    def report(self):
        feats = self.features() if (self.sc / "features.json").exists() else []
        repo = subprocess.run(["gh", "repo", "view", "--json", "url", "-q", ".url"], cwd=self.dir,
                              capture_output=True, text=True).stdout.strip() if shutil.which("gh") else ""
        from .claude import spent
        lines = [f"# shipcrew report — {self.dir.name}", "",
                 f"- Demo: {self.state.demo_url}", f"- Repo: {repo or 'n/a'}",
                 f"- Features: {sum(f['passes'] for f in feats)}/{len(feats)} passing",
                 f"- Cost: ${spent(self.dir):.2f}", ""]
        lines += [f"- [{'x' if f['passes'] else ' '}] {f['id']} {f['title']}" for f in feats]
        sec = self.sc / "security.md"
        if sec.exists():
            lines += ["", "## Security", sec.read_text()]
        (self.sc / "REPORT.md").write_text("\n".join(lines) + "\n")
        return self.state.demo_url
