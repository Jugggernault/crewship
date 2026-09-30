#!/usr/bin/env python3
"""Validate every shipcrew agent bundle with omnigent's own spec machinery.

Run it inside an omnigent environment, e.g. from an omnigent checkout::

    cd /path/to/omnigent && uv run python /path/to/shipcrew/scripts/validate_agents.py

For each ``agents/<bundle>/`` it checks:

1. the generated files are fresh (``scripts/build_agents.py --check``);
2. ``omnigent.spec.parse`` + ``omnigent.spec.validate`` on the source dir;
3. the upload path: ``materialize_bundle`` (symlinks dereferenced) -> tar.gz ->
   ``omnigent.spec.load(bytes, enforce_handler_allowlist=True)`` (safe
   extraction + registered-handler allowlist), as ``omnigent server --agent``
   does;
4. shipcrew conventions: instructions came from the generated AGENTS.md (not a
   literal file name), harness per role, the permission setup (claude-native:
   ``permission_mode: default`` + ``allowed_tools`` turned into
   ``--allowedTools`` by omnigent's launch-arg derivation; claude-sdk:
   ``auto``; never bypassPermissions), bundled skills, the orchestrator's
   ``tools.agents`` = the roles; the MCP scoping per role (``MCP_SERVERS``:
   strict MCP config, exactly the expected servers, turned into
   ``--strict-mcp-config`` / ``--mcp-config`` for claude-native and into the
   claude-sdk strict env flag, and ``allowed_tools``' ``mcp__*`` entries match);
5. the headless rendering: every claude-native worker bundle, rendered for
   claude-sdk exactly as the server does at session creation when
   ``SHIPCREW_WORKER_HARNESS`` picks sdk for its role
   (``omnigent.shipcrew.harness.apply_worker_harness``), parses and validates,
   keeps strict MCP + setting sources in the SDK spawn env, and gives the same
   verdict as native on every guardrail case, with the tool calls spelled the
   way the SDK agent makes them (``sys_os_shell`` / ``sys_os_write`` /
   ``sys_os_edit`` / ``sys_os_read``);
6. the guardrails behave: every function policy is resolved and built through
   omnigent's factory path, then run on a matrix of tool calls (git push,
   gh pr create, .env reads, workflow edits, playwright install, rm -rf /,
   allowlisted and non-allowlisted shell, writes in and out of the task's
   owned paths, ...) with the expected ALLOW / ASK / DENY per bundle. Worker
   bundles get the task contract injected exactly as the board does.

Exits non-zero on the first failing bundle report.
"""

from __future__ import annotations

import gzip
import inspect
import io
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from omnigent.policies.function import _resolve_dotted_path
from omnigent.spec import AgentSpec, FunctionPolicySpec, load, materialize_bundle, parse, validate

REPO = Path(__file__).resolve().parent.parent
AGENTS = REPO / "agents"

ROLES = [
    "planner",
    "designer",
    "scaffolder",
    "developer",
    "reviewer",
    "integrator",
    "qa",
    "security",
    "devops",
]
ORCHESTRATOR = "shipcrew"
SDK_BUNDLES = {"planner", ORCHESTRATOR}  # claude-sdk; every other role is claude-native
# MCP servers each bundle may load (on top of omnigent's own relay). Every
# session runs with --strict-mcp-config, so none of the host user's servers,
# plugins or claude.ai connectors (Gmail, Canva, Notion, Vercel, ...) leak in.
MCP_SERVERS: dict[str, set[str]] = {
    "planner": set(),
    # No shadcn MCP (~265 MB per session): builders use the shadcn CLI.
    "designer": set(),
    "scaffolder": set(),
    "developer": set(),
    "reviewer": set(),
    "integrator": set(),
    "qa": {"chrome-devtools"},
    "security": {"chrome-devtools"},
    "devops": set(),  # deploys with the vercel CLI
    "shipcrew": set(),
}
# Sessions load no host-user plugins (setting_sources: project,local), so a
# skill a role needs ships in its bundle (design-lock: a symlink to skills/).
BUNDLED_SKILLS = {
    "designer": {"design-lock"},
    "scaffolder": {"design-lock"},
    "developer": {"design-lock"},
    "reviewer": {"design-lock"},
    "qa": {"design-lock"},
    "integrator": {"resolve-conflicts"},
    ORCHESTRATOR: {"plan", "dispatch", "verify"},
}
SETTING_SOURCES = ["project", "local"]

# ── Guardrail behaviour matrix ──────────────────────────────────────────────
# (label, tool name, arguments, {profile: expected verdict}); "*" = every profile
# not listed explicitly. Profiles: builder (developer, designer, integrator),
# scaffolder, security, qa, reviewer, devops, planner, orchestrator.
#
# Worker bundles are checked the way the board starts them: the task contract
# (TASK_OWNED under REPO_PATH) is injected into the owned-paths policy with the
# server's own omnigent.shipcrew.sessions.inject_task_contract.

REPO_PATH = "/work/mission"
TASK_OWNED = ["app/**", "e2e/cart.spec.ts"]
# The mission's other active tasks at start (injected like the board does):
# their files are DENY with a hint, not an approval card.
OTHER_TASKS = [{"title": "Polls API", "owned_paths": ["lib/polls-api/**"]}]
BRANCH = "shipcrew/1a2b3c4d-cart"  # shipcrew/<first 8 chars of the task id>-<slug>

BUILDERS = ("builder", "scaffolder")  # owned paths + git/file writes
VERIFIERS = ("security", "qa")  # owned paths + test files only + git add/commit
COMMITTERS = (*BUILDERS, *VERIFIERS)  # commit on the task branch
RUNNERS = COMMITTERS  # run tests, linters, builds
READERS = ("reviewer", "devops", "planner")  # read-only shell
TESTERS = (*RUNNERS, "reviewer")  # may re-run the test suite
INSTALLERS = TESTERS  # lockfile-pinned install (fresh review worktrees need it)
WRITE_LIMITED = ("reviewer", "devops", "planner", *VERIFIERS)  # refused outside their files
PROBERS = COMMITTERS  # curl the local app (local_http)
APP_RUNNERS = VERIFIERS  # start / stop the app by hand (run_app)


def _bash(cmd: str) -> tuple[str, dict[str, str]]:
    return "Bash", {"command": cmd}


def _only(profiles: tuple[str, ...], verdict: str = "ALLOW", *, other: str = "ASK") -> dict[str, str]:
    """``verdict`` for *profiles* (and the orchestrator, which has no allowlist), ``other`` else."""
    return {"*": other, "orchestrator": "ALLOW", **{p: verdict for p in profiles}}


def _edit_hint() -> dict[str, str]:
    """A complex in-place edit: DENY (use the Edit tool) for the roles that edit in
    place, DENY for the verify roles (not a test file), ASK for the read-only ones."""
    return {**_no_verify(_only(())), **{p: "DENY" for p in BUILDERS}}


def _no_verify(expected: dict[str, str]) -> dict[str, str]:
    """*expected*, with the verify roles DENY: a write outside test files is refused."""
    return {**expected, **{p: "DENY" for p in VERIFIERS}}


# A write tool: builders are judged by owned paths, the write-limited roles DENY
# (the verify roles too, unless the path is a test file or their report).
def _write_verdicts(builders: str, **overrides: str) -> dict[str, str]:
    base = {"*": builders, "orchestrator": "ALLOW"}
    base.update({p: "DENY" for p in WRITE_LIMITED})
    base.update(overrides)
    return base


SHADCN = ("builder", "scaffolder")

COMMIT_HEREDOC = (
    "git add -A && git commit -m \"$(cat <<'EOF'\nfeat(cart): add cart page\n\n"
    "Co-Authored-By: Claude <noreply@anthropic.com>\nEOF\n)\""
)

CASES: list[tuple[str, str, dict[str, Any], dict[str, str]]] = [
    # publishing is the orchestrator's job, on the one branch scheme
    ("git push", *_bash(f"git push origin {BRANCH}"), {"*": "DENY", "orchestrator": "ALLOW"}),
    (
        "git -C push",
        *_bash(f"git -C .worktrees/T02 push -u origin {BRANCH}"),
        {"*": "DENY", "orchestrator": "ALLOW"},
    ),
    ("push old key scheme", *_bash("git push origin shipcrew/T02-cart"), {"*": "DENY"}),
    ("push uppercase slug", *_bash("git push origin shipcrew/1a2b3c4d-Cart"), {"*": "DENY"}),
    ("push task/<id>", *_bash("git push origin task/1a2b3c4d"), {"*": "DENY"}),
    ("bare git push", *_bash("git push"), {"*": "DENY"}),
    ("push remote only", *_bash("git push origin"), {"*": "DENY"}),
    ("push main", *_bash("git push origin main"), {"*": "DENY"}),
    ("push HEAD:main", *_bash(f"git push origin {BRANCH}:main"), {"*": "DENY"}),
    ("push +refspec", *_bash(f"git push origin +{BRANCH}"), {"*": "DENY"}),
    ("push --all", *_bash("git push --all origin"), {"*": "DENY"}),
    (
        "second push in a chain",
        *_bash(f"git push origin {BRANCH} && git push origin feature"),
        {"*": "DENY"},
    ),
    ("chained push", *_bash("git add -A && git commit -m wip && git push"), {"*": "DENY"}),
    ("git push by path", *_bash("/usr/bin/git push origin main"), {"*": "DENY"}),
    (
        "gh pr create by path",
        *_bash("/usr/bin/gh pr create --fill"),
        {"*": "DENY", "orchestrator": "ALLOW"},
    ),
    ("gh pr create", *_bash("gh pr create --fill"), {"*": "DENY", "orchestrator": "ALLOW"}),
    (
        "gh pr create draft",
        *_bash(f"gh pr create --draft --fill --head {BRANCH}"),
        {"*": "DENY", "orchestrator": "ALLOW"},
    ),
    ("gh pr merge", *_bash("gh pr merge 12 --squash"), {"*": "DENY", "orchestrator": "ASK"}),
    (
        "gh api POST",
        *_bash("gh api -X POST repos/o/r/pulls -f title=x"),
        {"*": "DENY", "orchestrator": "ALLOW"},
    ),
    ("gh pr view", *_bash("gh pr view 12 --json state"), {"*": "ALLOW"}),
    ("commit msg says push", *_bash('git commit -m "push the button"'), _only(COMMITTERS)),
    ("commit via heredoc", *_bash(COMMIT_HEREDOC), _only(COMMITTERS)),
    (
        "local git + tests",
        *_bash("git status && git diff origin/main...HEAD && npm test"),
        _only(TESTERS),
    ),
    # polly's catastrophic set
    ("rm -rf /", *_bash("rm -rf /"), {"*": "DENY"}),
    ("force push", *_bash(f"git push --force origin {BRANCH}"), {"*": "DENY"}),
    ("hard reset remote", *_bash("git reset --hard origin/main"), {"*": "DENY"}),
    ("rm -rf build dirs", *_bash("rm -rf node_modules .next"), _only(COMMITTERS)),
    # secrets
    ("Read .env", "Read", {"file_path": f"{REPO_PATH}/.env"}, {"*": "DENY"}),
    ("Read .env.local", "Read", {"file_path": f"{REPO_PATH}/.env.local"}, {"*": "DENY"}),
    (
        "Read .env.production.local",
        "Read",
        {"file_path": f"{REPO_PATH}/.env.production.local"},
        {"*": "DENY"},
    ),
    ("Read .env.example", "Read", {"file_path": f"{REPO_PATH}/.env.example"}, {"*": "ALLOW"}),
    ("Read env.ts", "Read", {"file_path": f"{REPO_PATH}/lib/env.ts"}, {"*": "ALLOW"}),
    ("Read .envrc", "Read", {"file_path": f"{REPO_PATH}/.envrc"}, {"*": "ALLOW"}),
    ("Read outside owned", "Read", {"file_path": f"{REPO_PATH}/lib/db.ts"}, {"*": "ALLOW"}),
    ("sys_os_read .env", "sys_os_read", {"path": ".env"}, {"*": "DENY"}),
    ("Grep .env", "Grep", {"pattern": "KEY", "path": ".env.local"}, {"*": "DENY"}),
    ("cat .env", *_bash("cat .env"), {"*": "DENY"}),
    ("grep .env.local", *_bash("grep DATABASE_URL .env.local"), {"*": "DENY"}),
    ("source ./.env", *_bash("source ./.env && npm run dev"), {"*": "DENY"}),
    ("cat .env.example", *_bash("cat .env.example"), {"*": "ALLOW"}),
    ("vercel env pull", *_bash("vercel env pull .env.local --yes"), _only(())),
    ("echo env var name", *_bash('echo "set DATABASE_URL in the environment"'), {"*": "ALLOW"}),
    # CI definitions need a human
    (
        "Write workflow",
        "Write",
        {"file_path": f"{REPO_PATH}/.github/workflows/ci.yml", "content": "x"},
        _write_verdicts("ASK", orchestrator="ASK"),
    ),
    (
        "Edit workflow",
        "Edit",
        {
            "file_path": f"{REPO_PATH}/.github/workflows/ci.yml",
            "old_string": "a",
            "new_string": "b",
        },
        _write_verdicts("ASK", orchestrator="ASK"),
    ),
    (
        "sed -i workflow",
        *_bash("sed -i 's/npm/pnpm/' .github/workflows/ci.yml"),
        _no_verify({"*": "ASK"}),
    ),
    ("cat workflow", *_bash("cat .github/workflows/ci.yml"), {"*": "ALLOW"}),
    (
        "chained read of workflows",
        *_bash(
            "cat src/calc.py; ls -a; ls .github/workflows 2>/dev/null"
            " && cat .github/workflows/*.yml"
        ),
        {"*": "ALLOW"},
    ),
    (
        "redirect into workflow",
        *_bash("cat ci.yml > .github/workflows/ci.yml"),
        _no_verify({"*": "ASK"}),
    ),
    (
        "read then write workflow",
        *_bash("ls .github/workflows && sed -i 's/a/b/' .github/workflows/ci.yml"),
        _no_verify({"*": "ASK"}),
    ),
    (
        "write hidden in a substitution",
        *_bash("cat $(sed -i 's/a/b/' .github/workflows/ci.yml)"),
        {"*": "ASK"},
    ),
    (
        "write hidden in backticks",
        *_bash("ls `sed -i 's/a/b/' .github/workflows/ci.yml`"),
        {"*": "ASK"},
    ),
    (
        "write on a second line",
        *_bash("cat .github/workflows/ci.yml\nsed -i 's/a/b/' .github/workflows/ci.yml"),
        _no_verify({"*": "ASK"}),
    ),
    ("yq in-place edit", *_bash("yq -i '.on = \"push\"' .github/workflows/ci.yml"), {"*": "ASK"}),
    ("yq read of workflow", *_bash("yq '.jobs' .github/workflows/ci.yml"), {"*": "ALLOW"}),
    (
        "git diff --output into workflows",
        *_bash("git diff --output=.github/workflows/ci.yml"),
        {"*": "ASK"},
    ),
    ("grep -i workflows", *_bash("grep -i node .github/workflows/ci.yml"), {"*": "ALLOW"}),
    (
        "checkout then read workflows",  # asked under the old regex (seen live)
        *_bash("git checkout main -- app/page.tsx && ls .github/workflows 2>/dev/null"),
        _no_verify(_only(BUILDERS)),
    ),
    (
        "fetch then list workflows",
        *_bash("git fetch -q origin; ls .github/workflows"),
        _only(BUILDERS),
    ),
    # no browser downloads
    ("playwright install", *_bash("npx playwright install chromium"), {"*": "DENY"}),
    ("playwright install deps", *_bash("pnpm exec playwright install --with-deps"), {"*": "DENY"}),
    ("playwright install-deps", *_bash("npx playwright install-deps"), {"*": "DENY"}),
    (
        "playwright test",
        *_bash("CHROMIUM_PATH=/usr/bin/chromium npx playwright test"),
        _only(RUNNERS),
    ),
    # shell allowlists: allowlisted commands run with no prompt, the rest ASK
    ("npm test", *_bash("npm test"), _only(TESTERS)),
    ("npm test piped", *_bash("npm test 2>&1 | tail -40"), _only(TESTERS)),
    ("npm ci", *_bash("npm ci --prefer-offline --no-audit --no-fund"), _only(INSTALLERS)),
    ("vitest", *_bash("npx vitest run src/cart.test.ts"), _only(TESTERS)),
    ("pytest", *_bash("uv run pytest -q tests/"), _only(TESTERS)),
    (
        "lint typecheck build",
        *_bash("npm run lint && npm run typecheck && npm run build"),
        _only(RUNNERS),
    ),
    ("timeout wrapper", *_bash("timeout 300 npm test"), _only(TESTERS)),
    ("design lint", *_bash("npx -y @google/design.md lint DESIGN.md"), _only(RUNNERS)),
    ("git read chain", *_bash("git --no-pager log --oneline -5 && git diff --stat"), {"*": "ALLOW"}),
    (
        "read-only shell",
        *_bash("ls -la app && rg -n TODO app | head -20 && wc -l README.md"),
        {"*": "ALLOW"},
    ),
    ("sed -n print", *_bash("sed -n '1,40p' app/page.tsx"), {"*": "ALLOW"}),
    ("sed -n w", *_bash("sed -n '1w /tmp/x' app/page.tsx"), _only(())),
    ("find -exec", *_bash("find . -name '*.ts' -exec wc -l {} +"), _only(())),
    ("git stash push -m", *_bash("git stash push -m wip"), _only(BUILDERS)),
    ("git branch -D", *_bash("git branch -D feature"), _only(())),
    ("git reset --hard local", *_bash("git reset --hard HEAD~1"), _only(())),
    # Spellings that turn into a refused word (abbreviations, clusters, expansions).
    ("git reset --har (abbrev)", *_bash("git reset --har HEAD~1"), _only(())),
    ("git fetch --upload-p (abbrev)", *_bash("git fetch --upload-p=true origin"), _only(())),
    ("git rebase -x stuck", *_bash("git rebase -xtrue HEAD~1"), _only(())),
    ("sort -uo cluster", *_bash("sort -uo /tmp/x README.md"), _only(())),
    ("param expansion", *_bash("CI=--output=/tmp/x; git diff $CI"), _only(())),
    ("brace expansion", *_bash("git reset --{ha,}rd HEAD~1"), _only(())),
    (
        "git checkout tree-ish path",
        *_bash("git checkout HEAD~1 lib/db.ts"),
        _no_verify(_only(())),
    ),
    ("python -c", *_bash('python3 -c "import os; print(os.listdir())"'), _only(())),
    ("bash -c", *_bash('bash -c "npm test"'), _only(())),
    ("unknown env prefix", *_bash("NODE_OPTIONS=--require=/tmp/x.js npm test"), _only(())),
    ("curl external", *_bash("curl -s https://example.com"), _only(())),
    (
        "curl localhost",
        *_bash("curl -s -X POST -H 'content-type: application/json' http://localhost:3000/api/cart"),
        _only(PROBERS),
    ),
    ("curl localhost -o /tmp", *_bash("curl -so /tmp/x http://127.0.0.1:3000"), _only(PROBERS)),
    ("init.sh", *_bash("./init.sh"), _only(("security", "qa"))),
    ("npm install pkg", *_bash("npm install lodash"), _no_verify(_only(()))),
    ("create-next-app", *_bash("npx create-next-app@latest . --ts --yes"), _only(("scaffolder",))),
    # shadcn components through the CLI only (no shadcn MCP), exactly `add`
    ("shadcn add (pnpm dlx)", *_bash("pnpm dlx shadcn@latest add button --yes"), _only(SHADCN)),
    ("shadcn add (npx)", *_bash("npx shadcn@latest add dialog card --yes"), _only(SHADCN)),
    ("shadcn add --cwd", *_bash("pnpm dlx shadcn@latest add button --yes --cwd /tmp/x"), _only(())),
    ("shadcn add --overwrite", *_bash("pnpm dlx shadcn@latest add button -y --overwrite"), _only(())),
    ("shadcn add --path", *_bash("pnpm dlx shadcn@latest add button --path ../other"), _only(())),
    ("shadcn other version", *_bash("pnpm dlx shadcn@2 add button"), _only(())),
    ("shadcn mcp", *_bash("pnpm dlx shadcn@latest mcp"), _only(())),
    ("vercel ls", *_bash("vercel ls --prod"), _only(("devops",))),
    ("vercel inspect", *_bash("vercel inspect https://x.vercel.app --wait"), _only(("devops",))),
    ("vercel deploy", *_bash("vercel deploy --prod"), _only(())),
    # the ship stage: exactly these vercel writes, plus reads and curl to *.vercel.app
    ("vercel whoami", *_bash("vercel whoami"), _only(("devops",))),
    ("vercel logs", *_bash("vercel logs https://x.vercel.app"), _only(("devops",))),
    ("vercel link ship", *_bash("vercel link --yes --project tiny-cli"), _only(("devops",))),
    ("vercel deploy ship", *_bash("vercel deploy --prod --yes"), _only(("devops",))),
    ("vercel whoami chained", *_bash("vercel whoami && vercel deploy --prod --yes"), _only(("devops",))),
    ("vercel link other form", *_bash("vercel link --project tiny-cli"), _only(())),
    ("vercel link bad name", *_bash("vercel link --yes --project Tiny;x"), _only(())),
    ("vercel link extra flag", *_bash("vercel link --yes --project t --scope other"), _only(())),
    ("vercel deploy extra flag", *_bash("vercel deploy --prod --yes --force"), _only(())),
    ("vercel deploy preview", *_bash("vercel deploy --yes"), _only(())),
    ("vercel bare prod", *_bash("vercel --prod --yes"), _only(())),
    ("vercel env add", *_bash("vercel env add API_KEY production"), _only(())),
    ("vercel env ls", *_bash("vercel env ls"), _only(())),
    ("vercel domains add", *_bash("vercel domains add evil.com"), _only(())),
    ("vercel remove", *_bash("vercel remove tiny-cli --yes"), _only(())),
    ("vercel git connect", *_bash("vercel git connect --yes"), _only(())),
    ("npx vercel deploy", *_bash("npx vercel deploy --prod --yes"), _only(())),
    ("curl deployment", *_bash("curl -sSIL https://tiny-cli.vercel.app/"), _only(("devops",))),
    ("curl deployment max-time", *_bash("curl -sS -o /dev/null -m 20 https://tiny-cli-abc123.vercel.app/api/health"), _only(("devops",))),
    ("curl deployment POST", *_bash("curl -X POST https://tiny-cli.vercel.app/api/cart"), _only(())),
    ("curl deployment data", *_bash("curl -d x=1 https://tiny-cli.vercel.app/api"), _only(())),
    ("curl deployment -o file", *_bash("curl -so page.html https://tiny-cli.vercel.app"), _no_verify(_only(()))),
    ("curl deployment header", *_bash("curl -H 'Authorization: x' https://tiny-cli.vercel.app"), _only(())),
    ("curl lookalike host", *_bash("curl -sS https://tiny.vercel.app.evil.com/"), _only(())),
    ("curl http deployment", *_bash("curl -sS http://tiny.vercel.app/"), _only(())),
    ("curl two urls", *_bash("curl -sS https://tiny.vercel.app https://example.com"), _only(())),
    ("prettier --write .", *_bash("npx prettier --write ."), _no_verify(_only(()))),
    (
        "prettier --write owned",
        *_bash("npx prettier --write app/cart"),
        _no_verify(_only(BUILDERS)),
    ),
    # owned paths (task owns app/** and e2e/cart.spec.ts)
    (
        "Write source",
        "Write",
        {"file_path": f"{REPO_PATH}/app/page.tsx", "content": "x"},
        _write_verdicts("ALLOW"),
    ),
    (
        "Write owned by name",
        "Write",
        {"file_path": f"{REPO_PATH}/e2e/cart.spec.ts", "content": "x"},
        _write_verdicts("ALLOW", qa="ALLOW", security="ALLOW"),  # a test file: verifiers too
    ),
    (
        "Write test outside owned",
        "Write",
        {"file_path": f"{REPO_PATH}/tests/cart.test.ts", "content": "x"},
        _write_verdicts("ASK", qa="ASK", security="ASK"),  # a test, but not this task's
    ),
    (
        "Write outside owned",
        "Write",
        {"file_path": f"{REPO_PATH}/lib/db.ts", "content": "x"},
        _write_verdicts("ASK"),
    ),
    (
        "Edit package.json",
        "Edit",
        {"file_path": f"{REPO_PATH}/package.json", "old_string": "a", "new_string": "b"},
        _write_verdicts("ASK"),
    ),
    (
        "Write outside worktree",
        "Write",
        {"file_path": "/home/user/.bashrc", "content": "x"},
        _write_verdicts("ASK"),
    ),
    (
        "Write screenshot to /tmp",
        "Write",
        {"file_path": "/tmp/shot.png", "content": "x"},
        _write_verdicts("ALLOW", qa="ALLOW", security="ALLOW"),
    ),
    (
        "Write qa.json",
        "Write",
        {"file_path": f"{REPO_PATH}/.shipcrew/qa.json", "content": "{}"},
        _write_verdicts("ASK", qa="ALLOW"),
    ),
    (
        "Write security.md",
        "Write",
        {"file_path": f"{REPO_PATH}/.shipcrew/security.md", "content": "x"},
        _write_verdicts("ASK", security="ALLOW"),
    ),
    (
        "Write deploy.json",
        "Write",
        {"file_path": f"{REPO_PATH}/.shipcrew/deploy.json", "content": "{}"},
        _write_verdicts("ASK", devops="ALLOW"),
    ),
    (
        "sys_os_write plan.json",
        "sys_os_write",
        {"path": ".shipcrew/plan.json", "content": "{}"},
        _write_verdicts("ASK", planner="ALLOW"),
    ),
    ("redirect outside owned", *_bash("echo x > lib/db.ts"), _no_verify(_only(()))),
    (
        "redirect inside owned",
        *_bash("echo x > app/cart/note.txt"),
        _no_verify(_only(BUILDERS)),
    ),
    ("tee outside owned", *_bash("echo x | tee lib/db.ts"), _no_verify(_only(()))),
    ("mkdir owned", *_bash("mkdir -p app/cart/components"), _no_verify(_only(BUILDERS))),
    ("cd then write outside", *_bash("cd lib && touch x.ts"), _no_verify(_only(()))),
    ("git mv out of owned", *_bash("git mv app/a.ts lib/a.ts"), _no_verify(_only(()))),
    (
        "cp into owned",
        *_bash("cp lib/db.ts app/cart/db-copy.ts"),
        _no_verify(_only(BUILDERS)),
    ),
    # verify roles (qa, security): tests only, committed on the task branch
    (
        "verify commits its test",
        *_bash("git add e2e/cart.spec.ts && git commit -m 'test(cart): regression for the total'"),
        _only(COMMITTERS),
    ),
    ("copy a test fixture", *_bash("cp e2e/cart.spec.ts /tmp/cart.bak"), _only(COMMITTERS)),
    (
        "Edit a test the task owns",
        "Edit",
        {"file_path": f"{REPO_PATH}/e2e/cart.spec.ts", "old_string": "a", "new_string": "b"},
        _write_verdicts("ALLOW", qa="ALLOW", security="ALLOW"),
    ),
    (
        "Edit app code as a verifier",
        "Edit",
        {"file_path": f"{REPO_PATH}/app/cart/page.tsx", "old_string": "a", "new_string": "b"},
        _write_verdicts("ALLOW"),
    ),
    ("git stash (verifier)", *_bash("git stash"), _only(())),
    ("log to /tmp", *_bash("npm test > /tmp/test.log 2>&1"), _only(TESTERS)),
    # live run approvals that must not come back (round 5): all ALLOW
    ("exit code echo", *_bash("npx -y @google/design.md lint DESIGN.md; echo EXIT=$?"), _only(RUNNERS)),
    ("echo vetted vars", *_bash("echo CHROMIUM=$CHROMIUM_PATH PORT=$PORT"), {"*": "ALLOW"}),
    (
        "shell var then read",
        *_bash("S=/tmp/sc/scratchpad/app; cat $S/package.json"),
        {"*": "ALLOW"},
    ),
    (
        "background install, reads, wait",  # multi-line, trailing &, escaped [id]
        *_bash(
            "npm ci --prefer-offline --no-audit --no-fund 2>&1 | tail -3 &\n"
            "cat DESIGN.md app/api/polls/\\[id\\]/route.ts; "
            'grep -n "export\\|seed" lib/db.ts | head -40; wait'
        ),
        _only(INSTALLERS),
    ),
    (
        "default in a vetted env prefix",
        *_bash("PORT=${PORT:-3000} CI=1 npx playwright test"),
        _only(RUNNERS),
    ),
    # ... and the protections behind them
    ("expansion into a banned option", *_bash("X=--output=f; git diff $X"), _only(())),
    ("echo a secret var", *_bash("echo $DATABASE_URL"), _only(())),
    ("unassigned var in a read", *_bash("cat $SECRET_FILE"), _only(())),
    ("PATH assignment", *_bash("PATH=/tmp/evil; cat README.md"), _only(())),
    ("shell var then a runner", *_bash("S=/tmp/x; npm test"), _only(())),
    ("expansion in a runner", *_bash("npm test -- $ARGS"), _only(())),
    ("printf -v", *_bash("printf -v PATH %s /tmp/evil; npm test"), _only(())),
    ("rg --pre", *_bash("rg --pre /tmp/x.sh TODO"), _only(())),
    ("git diff-tree --output", *_bash("git diff-tree --output=/tmp/x -p HEAD"), _only(())),
    ("redirect to a var", *_bash("echo x > $OUT"), _no_verify(_only(()))),
    ("npm install -D, package.json not owned", *_bash("npm install -D vitest"), _no_verify(_only(()))),
    ("sed -i outside owned", *_bash("sed -i 's/a/b/' src/other.ts"), _no_verify(_only(()))),
    ("sed -i owned", *_bash("sed -i 's|a|b|g' app/page.tsx"), _no_verify(_only(BUILDERS))),
    ("sed -i w flag", *_bash("sed -i 's/a/b/w /tmp/x' app/page.tsx"), _edit_hint()),
    ("sed -i e command", *_bash("sed -i '1e touch /tmp/x' app/page.tsx"), _edit_hint()),
    ("perl -pi owned", *_bash("perl -pi -e 's/a/b/g' app/page.tsx"), _no_verify(_only(BUILDERS))),
    (
        "perl -pi code",
        *_bash("perl -pi -e 's/a/@{[system(\"id\")]}/' app/page.tsx"),
        _edit_hint(),
    ),
    ("perl -e code", *_bash("perl -pi -e 'system(1)' app/page.tsx"), _edit_hint()),
    # round 7: a complex in-place edit is refused with a hint (use the Edit tool),
    # no approval card; one simple s/// stays ALLOW (above)
    ("sed -i address range", *_bash("sed -i '/re/,+1d' app/page.tsx"), _edit_hint()),
    ("sed -i delete lines", *_bash("sed -i '/console.log/d' app/page.tsx"), _edit_hint()),
    ("sed -i two commands", *_bash("sed -i 's/a/b/; s/c/d/' app/page.tsx"), _edit_hint()),
    ("sed -i two -e", *_bash("sed -i -e 's/a/b/' -e 's/c/d/' app/page.tsx"), _edit_hint()),
    ("sed -i newline in replacement", *_bash("sed -i -E 's/(x)/\\1\\n  y/' app/page.tsx"), _edit_hint()),
    ("sed -i insert line", *_bash("sed -i '3a extra' app/page.tsx"), _edit_hint()),
    ("perl -pi two substitutions", *_bash("perl -pi -e 's/a/b/; s/c/d/' app/page.tsx"), _edit_hint()),
    ("sed -i -E simple", *_bash("sed -i -E 's/(a+)/\\1b/' app/page.tsx"), _no_verify(_only(BUILDERS))),
    ("sed -i complex outside owned", *_bash("sed -i '/x/d' src/other.ts"), _edit_hint()),
    ("sed -i complex in a substitution", *_bash("cat $(sed -i '/x/d' app/page.tsx)"), _only(())),
    # round 7: another in-progress task's file is refused with a hint
    (
        "Write another task's file",
        "Write",
        {"file_path": f"{REPO_PATH}/lib/polls-api/route.ts", "content": "x"},
        _write_verdicts("DENY"),
    ),
    ("touch another task's file", *_bash("touch lib/polls-api/x.ts"), _no_verify({**_only(()), **{p: "DENY" for p in BUILDERS}})),
    (
        "Write nobody's file still asks",
        "Write",
        {"file_path": f"{REPO_PATH}/lib/free.ts", "content": "x"},
        _write_verdicts("ASK"),
    ),
    # round 7: package-manager output flags match the plain entries
    ("pnpm -s lint", *_bash("pnpm -s lint"), _only(TESTERS)),
    ("pnpm --silent typecheck", *_bash("pnpm --silent typecheck"), _only(TESTERS)),
    ("npm run --silent typecheck", *_bash("npm run --silent typecheck"), _only(TESTERS)),
    ("npm run -s test", *_bash("npm run -s test"), _only(TESTERS)),
    ("pnpm -s run test", *_bash("pnpm -s run test"), _only(TESTERS)),
    ("pnpm --loglevel warn test", *_bash("pnpm --loglevel warn --no-color test"), _only(TESTERS)),
    ("pnpm -s build (not the reviewer)", *_bash("pnpm -s build"), _only(RUNNERS)),
    ("pnpm -s e2e (not the reviewer)", *_bash("pnpm -s e2e"), _only(RUNNERS)),
    ("pnpm build (not the reviewer)", *_bash("pnpm build"), _only(RUNNERS)),
    ("npx next build (not the reviewer)", *_bash("npx next build"), _only(RUNNERS)),
    ("pnpm -s add, package.json not owned", *_bash("pnpm -s add zod"), _no_verify(_only(()))),
    ("pnpm -s -g add", *_bash("pnpm -s add -g zod"), _no_verify(_only(()))),
    # verify roles add tests, never remove another task's (seen live: `git rm` of a
    # "redundant" spec). No repo at REPO_PATH here: a removal that cannot be
    # checked against origin/main is refused too.
    ("verify git rm of a test", *_bash("git rm -q e2e/home-page.spec.ts"), _no_verify(_only(()))),
    ("verify rm of a test", *_bash("rm e2e/cart.spec.ts"), _no_verify(_only(BUILDERS))),
    ("verify mv of a test", *_bash("mv e2e/cart.spec.ts e2e/old.spec.ts"), _no_verify(_only(()))),
    # (a `>` over a test is refused only when origin/main has it: tests/shipcrew/test_test_writes.py)
    ("verify rewrites a test, no base", *_bash("echo '' > e2e/cart.spec.ts"), _only(COMMITTERS)),
    ("verify appends to a test", *_bash("echo '// more' >> e2e/cart.spec.ts"), _only(COMMITTERS)),
    # live run 2 approvals that must not come back (round 6): all ALLOW
    ("next start on $PORT", *_bash("pnpm exec next start -p ${PORT:-3000}"), _only(APP_RUNNERS)),
    ("pkill next on $PORT", *_bash('pkill -f "next start -p ${PORT:-3000}"; true'), _only(APP_RUNNERS)),
    ("next start bare $PORT", *_bash("npx next start -p $PORT"), _only(APP_RUNNERS)),
    (
        "curl POST bad json",
        *_bash(
            'curl -s -o /dev/null -w "%{http_code}\\n" -X POST localhost:3000/api/polls '
            "-H 'content-type: application/json' -d '{bad'"
        ),
        _only(PROBERS),
    ),
    (
        "curl favicon on $PORT",
        *_bash('curl -s -o /dev/null -w "%{http_code}\\n" localhost:${PORT:-3000}/favicon.ico'),
        _only(PROBERS),
    ),
    ("curl DELETE [::1]", *_bash("curl -si -X DELETE 'http://[::1]:4000/api/polls/1'"), _only(PROBERS)),
    ("curl -d @file in the worktree", *_bash("curl -s -d @e2e/fixture.json localhost:3000/api"), _only(PROBERS)),
    (
        "install, tail, PIPESTATUS",
        *_bash(
            "pnpm install --frozen-lockfile --prefer-offline 2>&1 | tail -5; "
            "echo rc=${PIPESTATUS[0]}; ls"
        ),
        _only(INSTALLERS),
    ),
    (
        "sed strips ANSI in a pipe",
        *_bash(
            "npx vitest run components 2>&1 | sed 's/\\x1b\\[[0-9;]*m//g' | "
            'grep -E "Tests|FAIL"'
        ),
        _only(TESTERS),
    ),
    ("sed filter, read-only", *_bash("git log --oneline -5 | sed -n 's/^\\([0-9a-f]*\\) .*/\\1/p'"), {"*": "ALLOW"}),
    (
        "Write pnpm-workspace.yaml (feature task)",
        "Write",
        {"file_path": f"{REPO_PATH}/pnpm-workspace.yaml", "content": "x"},
        _write_verdicts("ASK"),
    ),
    # ... and the protections behind them
    ("curl remote POST", *_bash("curl -s -d x=1 https://example.com/api"), _only(())),
    ("curl upload .env", *_bash("curl -s -d @.env localhost:3000/api"), _only(())),
    ("curl upload outside", *_bash("curl -s -T /etc/passwd localhost:3000/up"), _only(())),
    ("curl form file outside", *_bash("curl -F f=@../secrets.txt localhost:3000/up"), _only(())),
    ("curl --config", *_bash("curl --config /tmp/c localhost:3000"), _only(())),
    ("curl cookie jar", *_bash("curl -c /tmp/j localhost:3000"), _only(())),
    ("curl via proxy", *_bash("curl -x http://evil:8080 localhost:3000"), _only(())),
    ("curl -o outside worktree", *_bash("curl -so /home/user/x localhost:3000"), _no_verify(_only(()))),
    ("curl -o over source", *_bash("curl -so app/page.tsx localhost:3000"), _no_verify(_only(BUILDERS))),
    ("curl -o on a $PORT url", *_bash("curl -so lib/db.ts localhost:$PORT/x"), _no_verify(_only(()))),
    ("curl host from a var", *_bash("curl -s $BASE_URL/api"), _only(())),
    ("curl secret var", *_bash("curl -s localhost:3000/$API_KEY"), _only(())),
    ("assigned var in a runner", *_bash("P=3000; curl -s localhost:$P"), _only(())),
    ("vetted var reassigned", *_bash("PORT=--config=/tmp/x; curl localhost:$PORT"), _only(())),
    ("var as the program", *_bash("npx $PORT"), _only(())),
    ("var in an option name", *_bash("npx next start --$PORT"), _only(())),
    ("var in a git write", *_bash("git checkout ${PORT:-main}"), _no_verify(_only(()))),
    ("var in rm", *_bash("rm -rf ${TMPDIR:-/tmp}/x"), _no_verify(_only(()))),
    ("sed w in a pipe", *_bash("cat app/page.tsx | sed 's/a/b/w /tmp/x'"), _only(())),
    ("sed e in a pipe", *_bash("cat app/page.tsx | sed '1e id'"), _only(())),
    ("sed r reads a file", *_bash("cat app/page.tsx | sed 'r /etc/passwd'"), _only(())),
    ("sed filter on a file", *_bash("sed 's/a/b/' app/page.tsx"), _only(())),
    ("sed on .env", *_bash("sed 's/a/b/' .env"), {"*": "DENY"}),
    ("PIPESTATUS next to a secret", *_bash("echo rc=${PIPESTATUS[0]} $API_KEY"), _only(())),
    # live run 4 (round 8): a reviewer read-only chain with an ANSI-C string asked
    # as "substitutions, heredocs ..."; a decodable $'..' is a literal: ALLOW
    (
        "read-only chain with $'..'",
        *_bash(
            'cat components/ui/progress.tsx; grep -n "result-\\|--radius" app/globals.css; '
            'grep -n "className=" components/ui/card.tsx | head -3; '
            "grep -c $'\u00a0' lib/results.ts; "
            'grep -rn "\\.skip" components/poll e2e/vote* lib/*.test.ts'
        ),
        {"*": "ALLOW"},
    ),
    ("escaped $'..' in a grep", *_bash("grep -c $'\\u00a0' lib/results.ts"), {"*": "ALLOW"}),
    # ... and a refused option spelled as an ANSI-C string is still refused
    ("$'..' option", *_bash("git diff $'--output=/tmp/x'"), _only(())),
    ("undecodable $'..'", *_bash("cat $'\\cA'"), _only(())),
    # orchestrator dispatch hygiene
    (
        "dispatch without purpose",
        "sys_session_send",
        {"agent": "developer", "title": "t02-cart", "args": {"input": "x"}},
        {"*": "ALLOW", "orchestrator": "DENY"},
    ),
    (
        "dispatch plan",
        "sys_session_send",
        {"agent": "planner", "title": "plan-x", "args": {"purpose": "plan", "input": "x"}},
        {"*": "ALLOW"},
    ),
    (
        "dispatch verify",
        "sys_session_send",
        {"agent": "qa", "title": "qa-x", "args": {"purpose": "verify", "input": "x"}},
        {"*": "ALLOW"},
    ),
]

# The same matrix under a Foundation task contract: it owns package.json by
# name (and so its lockfiles) and everything else, like a scaffold task. The
# builders then run the live commands with no approval; the CI workflow stays
# gated (the server installs it, see omnigent/shipcrew/ci_install.py).
FOUNDATION_OWNED = ["**", "package.json"]
CONTRACT_CASES: list[tuple[str, str, dict[str, Any], dict[str, str]]] = [
    (
        "dev deps install (foundation)",
        *_bash(
            "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install -D --no-audit --no-fund vitest "
            '@playwright/test @faker-js/faker 2>&1 | grep -v "^npm warn install" | head -30'
        ),
        _no_verify(_only(BUILDERS)),
    ),
    ("pnpm add (foundation)", *_bash("pnpm add zod"), _no_verify(_only(BUILDERS))),
    ("pnpm -s add (foundation)", *_bash("pnpm -s add zod"), _no_verify(_only(BUILDERS))),
    ("pnpm -w add (foundation)", *_bash("pnpm -s -w add zod"), _no_verify(_only(()))),
    (
        "Write under an other task's glob, owned too (foundation)",
        "Write",
        {"file_path": f"{REPO_PATH}/lib/polls-api/route.ts", "content": "x"},
        _write_verdicts("ALLOW"),
    ),
    ("npm install pkg (foundation)", *_bash("npm install lodash"), _no_verify(_only(BUILDERS))),
    (
        "rename config, sed package.json, check (foundation)",
        *_bash(
            "git mv -f vitest.config.ts vitest.config.mts 2>/dev/null || mv vitest.config.ts "
            "vitest.config.mts; sed -i 's|\"test\": \"vitest\"|\"test\": \"vitest run\"|' "
            "package.json; npm run typecheck 2>&1 | tail -5; npm test 2>&1 | grep -E \"warn|Tests\""
        ),
        _no_verify(_only(BUILDERS)),
    ),
    ("npm install -g (foundation)", *_bash("npm install -g vercel"), _no_verify(_only(()))),
    (
        "pnpm add in another package (foundation)",
        *_bash("pnpm add --filter web zod"),
        _no_verify(_only(())),
    ),
    (
        "Write CI workflow (foundation)",
        "Write",
        {"file_path": f"{REPO_PATH}/.github/workflows/ci.yml", "content": "x"},
        _write_verdicts("ASK", orchestrator="ASK"),
    ),
    (
        "sed -i CI workflow (foundation)",
        *_bash("sed -i 's/npm/pnpm/' .github/workflows/ci.yml"),
        _no_verify({"*": "ASK"}),
    ),
    (
        "Write pnpm-workspace.yaml (foundation)",
        "Write",
        {"file_path": f"{REPO_PATH}/pnpm-workspace.yaml", "content": "x"},
        _write_verdicts("ALLOW"),
    ),
    (
        "Write .nvmrc (foundation)",
        "Write",
        {"file_path": f"{REPO_PATH}/.nvmrc", "content": "22"},
        _write_verdicts("ALLOW"),
    ),
    (
        "sed -i pnpm-workspace.yaml (foundation)",
        *_bash("sed -i 's/a/b/' pnpm-workspace.yaml"),
        _no_verify(_only(BUILDERS)),
    ),
    (
        "pnpm install, tail, PIPESTATUS (foundation)",
        *_bash("pnpm install 2>&1 | tail -5; echo rc=${PIPESTATUS[0]}; ls"),
        _no_verify(_only(BUILDERS)),
    ),
    (
        "Edit package.json (foundation)",
        "Edit",
        {"file_path": f"{REPO_PATH}/package.json", "old_string": "a", "new_string": "b"},
        _write_verdicts("ALLOW"),
    ),
]

PROFILE = {
    "developer": "builder",
    "designer": "builder",
    "integrator": "builder",
    "scaffolder": "scaffolder",
    "security": "security",
    "qa": "qa",
    "reviewer": "reviewer",
    "devops": "devops",
    "planner": "planner",
    ORCHESTRATOR: "orchestrator",
}
OWNED_PATHS_POLICY = "shipcrew_owned_paths"
TEST_WRITES_POLICY = "shipcrew_test_writes_only"
SHELL_ALLOWLIST_POLICY = "shipcrew_shell_allowlist"
CLAUDE_NATIVE_CORE_TOOLS = {"Bash", "Read", "Glob", "Grep"}
CLAUDE_NATIVE_WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

_RANK = {"ALLOW": 0, "ASK": 1, "DENY": 2}


def _build_policies(spec: AgentSpec) -> dict[str, Callable[..., Any]]:
    """Resolve and instantiate every function policy the way omnigent does."""
    built: dict[str, Callable[..., Any]] = {}
    for policy in spec.guardrails.policies if spec.guardrails else []:
        if not isinstance(policy, FunctionPolicySpec) or policy.function is None:
            raise AssertionError(f"policy {policy.name!r} is not a function policy")
        target = _resolve_dotted_path(policy.function.path)
        if not callable(target):
            raise TypeError(f"policy {policy.name!r}: {policy.function.path} is not callable")
        built[policy.name] = target(**(policy.function.arguments or {}))
    return built


def _verdict(policies: dict[str, Callable[..., Any]], tool: str, args: dict[str, Any]) -> str:
    """Most restrictive decision across all policies for one tool call."""
    event = {"type": "tool_call", "target": tool, "data": {"name": tool, "arguments": args}}
    worst = "ALLOW"
    for fn in policies.values():
        n_params = len(inspect.signature(fn).parameters)
        out = fn(event, {}) if n_params >= 2 else fn(event)
        result = (out or {}).get("result", "ALLOW")
        if _RANK[result] > _RANK[worst]:
            worst = result
    return worst


# How the claude-sdk harness spells the native tool calls of the matrix: the
# OS tools are omnigent's sys_os_* MCP tools (arguments ``command`` / ``path``).
_SDK_TOOLS = {
    "Bash": "sys_os_shell",
    "Write": "sys_os_write",
    "Edit": "sys_os_edit",
    "MultiEdit": "sys_os_edit",
    "Read": "sys_os_read",
}


def _sdk_call(tool: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """The claude-sdk spelling of a native tool call, or ``None`` (no SDK equivalent)."""
    name = _SDK_TOOLS.get(tool)
    if name is None:
        return None
    out = {("path" if k == "file_path" else k): v for k, v in args.items()}
    return name, out


def _render_sdk(bundle: Path, dest: Path, owned: list[str] | None) -> AgentSpec:
    """The bundle as the server uploads it for a claude-sdk worker session."""
    from omnigent.shipcrew.harness import SDK, apply_worker_harness
    from omnigent.shipcrew.sessions import inject_task_contract

    copy = materialize_bundle(bundle, dest)
    config = copy / "config.yaml"
    text = config.read_text(encoding="utf-8")
    if owned is not None:
        text = inject_task_contract(
            text, owned_paths=owned, root=REPO_PATH, other_tasks=OTHER_TASKS
        )
    config.write_text(apply_worker_harness(text, SDK), encoding="utf-8")
    return parse(copy, expand_env=False)


def _check_sdk_rendering(name: str, native: AgentSpec, errors: list[str]) -> int:
    """A claude-native worker rendered for claude-sdk: valid, isolated, same verdicts."""
    from omnigent.runtime.workflow import _build_claude_sdk_spawn_env

    if native.executor.config.get("harness") != "claude-native":
        return 0
    profile = PROFILE[name]
    checked = 0
    with tempfile.TemporaryDirectory() as tmp:
        rounds = [(TASK_OWNED, CASES), (FOUNDATION_OWNED, CONTRACT_CASES)]
        for i, (owned, cases) in enumerate(rounds):
            owned_or_none = owned if profile in COMMITTERS else None
            spec = _render_sdk(AGENTS / name, Path(tmp) / f"b{i}", owned_or_none)
            if i == 0:
                errors += [f"sdk rendering: validate: {e.path}: {e.message}"
                           for e in validate(spec).errors]  # fmt: skip
                config = spec.executor.config
                if config.get("harness") != "claude-sdk" or config.get("permission_mode") != "auto":
                    errors.append(f"sdk rendering: harness/permission_mode: {config}")
                if "allowed_tools" in config or "mcp_config" in config:
                    errors.append("sdk rendering keeps native-only allowed_tools / mcp_config")
                env = _build_claude_sdk_spawn_env(spec)
                if env.get("HARNESS_CLAUDE_SDK_STRICT_MCP_CONFIG") != "1":
                    errors.append("sdk rendering: spawn env lacks the strict MCP flag")
                if env.get("HARNESS_CLAUDE_SDK_SETTING_SOURCES") != ",".join(SETTING_SOURCES):
                    errors.append("sdk rendering: spawn env lacks setting sources project,local")
                if {s.name for s in spec.skills} != BUNDLED_SKILLS.get(name, set()):
                    errors.append("sdk rendering: bundled skills changed")
            policies = _build_policies(spec)
            for label, tool, args, expected in cases:
                call = _sdk_call(tool, args)
                if call is None:
                    continue
                want = expected.get(profile, expected["*"])
                got = _verdict(policies, *call)
                checked += 1
                if got != want:
                    errors.append(
                        f"sdk guardrail {label!r} ({call[0]} {call[1]}): expected {want}, got {got}"
                    )
    return checked


def _with_task_contract(bundle: Path, owned: list[str] = TASK_OWNED) -> AgentSpec:
    """The bundle as the board starts it: task contract injected by the server code."""
    from omnigent.shipcrew.sessions import inject_task_contract

    with tempfile.TemporaryDirectory() as tmp:
        copy = materialize_bundle(bundle, Path(tmp) / "bundle")
        config = copy / "config.yaml"
        config.write_text(
            inject_task_contract(
                config.read_text(encoding="utf-8"),
                owned_paths=owned,
                root=REPO_PATH,
                other_tasks=OTHER_TASKS,
            ),
            encoding="utf-8",
        )
        return parse(copy, expand_env=False)


def _check_guardrails(name: str, spec: AgentSpec, errors: list[str]) -> int:
    profile = PROFILE[name]
    names = {p.name for p in spec.guardrails.policies} if spec.guardrails else set()
    if profile in COMMITTERS:
        if OWNED_PATHS_POLICY not in names:
            errors.append(f"{OWNED_PATHS_POLICY} policy missing")
        # without a task contract (not started from a board card) it abstains
        raw = _build_policies(spec).get(OWNED_PATHS_POLICY)
        probe = {"type": "tool_call", "data": {"name": "Write", "arguments": {
            "file_path": f"{REPO_PATH}/lib/db.ts", "content": "x"}}}
        if raw is not None and raw(probe, {}).get("result") != "ALLOW":
            errors.append(f"{OWNED_PATHS_POLICY} without a task contract must abstain")
        spec = _with_task_contract(AGENTS / name)
        injected = next(
            p for p in spec.guardrails.policies if p.name == OWNED_PATHS_POLICY
        ).function.arguments
        if (
            injected.get("owned_paths") != TASK_OWNED
            or injected.get("root") != REPO_PATH
            or injected.get("other_tasks") != OTHER_TASKS
        ):
            errors.append(f"task contract not injected: {injected}")
    if profile in VERIFIERS:
        tests = next(
            (p for p in spec.guardrails.policies if p.name == TEST_WRITES_POLICY), None
        )
        if tests is None:
            errors.append(f"{TEST_WRITES_POLICY} policy missing (verify roles write tests only)")
        elif tests.function.arguments.get("root") != REPO_PATH:
            errors.append(f"{TEST_WRITES_POLICY}: task root not injected")
    if profile != "orchestrator" and SHELL_ALLOWLIST_POLICY not in names:
        errors.append(f"{SHELL_ALLOWLIST_POLICY} policy missing")
    policies = _build_policies(spec)
    for label, tool, args, expected in CASES:
        want = expected.get(profile, expected["*"])
        got = _verdict(policies, tool, args)
        if got != want:
            errors.append(f"guardrail {label!r} ({tool} {args}): expected {want}, got {got}")
    if profile in COMMITTERS:
        spec = _with_task_contract(AGENTS / name, FOUNDATION_OWNED)
        policies = _build_policies(spec)
    for label, tool, args, expected in CONTRACT_CASES:
        want = expected.get(profile, expected["*"])
        got = _verdict(policies, tool, args)
        if got != want:
            errors.append(f"guardrail {label!r} ({tool} {args}): expected {want}, got {got}")
    if profile == "orchestrator":
        # capacity gate: the 7th dispatch in one turn is refused
        spawn = _build_policies(spec)[
            "spawn_bounds"
        ]  # fresh closure: the matrix above dispatched too
        send = {"type": "tool_call", "data": {"name": "sys_session_send", "arguments": {}}}
        results = [spawn(send)["result"] for _ in range(7)]
        if results != ["ALLOW"] * 6 + ["DENY"]:
            errors.append(f"spawn_bounds: expected 6 ALLOW then DENY, got {results}")
    return len(CASES) + len(CONTRACT_CASES)


def _upload_round_trip(bundle: Path) -> AgentSpec:
    """materialize -> tar.gz -> load(bytes) with the handler allowlist, as the server does."""
    with tempfile.TemporaryDirectory() as tmp:
        materialized = materialize_bundle(bundle, Path(tmp) / "bundle")
        buf = io.BytesIO()
        with (
            gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz,
            tarfile.open(fileobj=gz, mode="w") as tar,
        ):
            tar.add(str(materialized), arcname=".")
        return load(
            buf.getvalue(),
            dest=Path(tmp) / "extracted",
            expand_env=False,
            enforce_handler_allowlist=True,
        )


def _check_conventions(name: str, spec: AgentSpec, errors: list[str]) -> None:
    instructions = spec.instructions or ""
    if not instructions.startswith("<!-- GENERATED by scripts/build_agents.py"):
        errors.append(f"instructions did not come from AGENTS.md (got {instructions[:60]!r})")
    if "# shipcrew common rules" not in instructions:
        errors.append("instructions miss the shared COMMON.md rules")
    config = spec.executor.config
    want_harness = "claude-sdk" if name in SDK_BUNDLES else "claude-native"
    if config.get("harness") != want_harness:
        errors.append(f"harness: expected {want_harness}, got {config.get('harness')!r}")
    # claude-sdk: omnigent's `auto` pre-approves tools and the guardrails gate
    # them. claude-native: Claude's `default` mode + --allowedTools for the
    # tools the guardrails govern, so an allowlisted command never prompts and
    # everything else is one ASK in the Inbox. Never bypassPermissions.
    want_mode = "auto" if name in SDK_BUNDLES else "default"
    if config.get("permission_mode") != want_mode:
        errors.append(
            f"permission_mode: expected {want_mode!r}, got {config.get('permission_mode')!r}"
        )
    allowed = {t for t in str(config.get("allowed_tools") or "").replace(" ", ",").split(",") if t}
    if want_harness == "claude-native":
        want_tools = CLAUDE_NATIVE_CORE_TOOLS | (
            set() if name == "reviewer" else CLAUDE_NATIVE_WRITE_TOOLS
        )
        if not want_tools <= allowed:
            errors.append(f"allowed_tools misses {sorted(want_tools - allowed)}")
        from omnigent.server.routes._sessions.helpers import (
            _derive_terminal_launch_args_from_spec,
        )

        launch = _derive_terminal_launch_args_from_spec(spec, headless_defaults=False) or []
        if launch[:2] != ["--permission-mode", "default"] or "--allowedTools" not in launch:
            errors.append(f"claude launch args: got {launch}")
    elif allowed:
        errors.append("allowed_tools is only for claude-native bundles")
    _check_mcp(name, spec, want_harness, allowed, errors)
    if "bypass" in str(config.get("permission_mode", "")).lower():
        errors.append("bypassPermissions is not allowed")
    if spec.executor.model is not None:
        errors.append("a model is pinned; bundles must run on the configured Claude provider")
    skills = {s.name for s in spec.skills}
    if skills != BUNDLED_SKILLS.get(name, set()):
        expected = sorted(BUNDLED_SKILLS.get(name, set()))
        errors.append(f"bundled skills: expected {expected}, got {sorted(skills)}")
    if name == ORCHESTRATOR:
        if not spec.spawn:
            errors.append("orchestrator must set spawn: true")
        if spec.tools.agents != ROLES:
            errors.append(f"tools.agents: expected {ROLES}, got {spec.tools.agents}")
        subs = {sa.name: sa for sa in spec.sub_agents}
        if sorted(subs) != sorted(ROLES):
            errors.append(f"sub-agents: expected {sorted(ROLES)}, got {sorted(subs)}")
        for role, sub in subs.items():
            standalone = parse(AGENTS / role, expand_env=False)
            if sub.instructions != standalone.instructions:
                errors.append(f"sub-agent {role!r} differs from agents/{role}")


def _check_mcp(
    name: str, spec: AgentSpec, harness: str, allowed: set[str], errors: list[str]
) -> None:
    from omnigent.shipcrew.launch_args import (
        claude_mcp_launch_args,
        mcp_server_names,
        strict_mcp_enabled,
    )

    config = spec.executor.config
    want = MCP_SERVERS[name]
    if not strict_mcp_enabled(config):
        errors.append("strict_mcp_config must be true (no host MCP servers)")
    from omnigent.shipcrew.launch_args import setting_sources

    if setting_sources(config) != SETTING_SOURCES:
        errors.append(f"setting_sources must be {SETTING_SOURCES} (no host-user plugins/hooks)")
    try:
        servers = set(mcp_server_names(config))
        claude_args = claude_mcp_launch_args(config)
    except ValueError as exc:
        errors.append(f"mcp_config: {exc}")
        return
    if servers != want:
        errors.append(f"MCP servers: expected {sorted(want)}, got {sorted(servers)}")
    mcp_tools = {t.removeprefix("mcp__") for t in allowed if t.startswith("mcp__")}
    if harness == "claude-native":
        if mcp_tools != want | {"omnigent"}:
            errors.append(
                f"allowed_tools mcp__* entries: expected {sorted(want | {'omnigent'})}, "
                f"got {sorted(mcp_tools)}"
            )
        from omnigent.server.routes._sessions.helpers import (
            _derive_terminal_launch_args_from_spec,
        )

        launch = _derive_terminal_launch_args_from_spec(spec, headless_defaults=False) or []
        if "--strict-mcp-config" not in launch:
            errors.append(f"claude launch args lack --strict-mcp-config: {launch}")
        at = launch.index("--setting-sources") if "--setting-sources" in launch else -1
        if at < 0 or launch[at + 1 : at + 2] != [",".join(SETTING_SOURCES)]:
            errors.append(f"claude launch args lack --setting-sources project,local: {launch}")
        if claude_args and launch[-len(claude_args) :] != claude_args:
            errors.append(f"claude launch args lack the role MCP config: {launch}")
    else:
        if servers:
            errors.append("claude-sdk bundles take no mcp_config (only strict_mcp_config)")
        from omnigent.runtime.workflow import _build_claude_sdk_spawn_env

        if _build_claude_sdk_spawn_env(spec).get("HARNESS_CLAUDE_SDK_STRICT_MCP_CONFIG") != "1":
            errors.append("claude-sdk spawn env lacks HARNESS_CLAUDE_SDK_STRICT_MCP_CONFIG=1")
        if _build_claude_sdk_spawn_env(spec).get("HARNESS_CLAUDE_SDK_SETTING_SOURCES") != (
            ",".join(SETTING_SOURCES)
        ):
            errors.append("claude-sdk spawn env lacks HARNESS_CLAUDE_SDK_SETTING_SOURCES")


def main() -> int:
    build = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "build_agents.py"), "--check"],
        check=False,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        print(build.stdout + build.stderr)
        print("FAIL generated files are stale")
        return 1
    print("ok   generated files are fresh")

    bundles = [*ROLES, ORCHESTRATOR]
    on_disk = sorted(
        p.name for p in AGENTS.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
    )
    if sorted(bundles) != on_disk:
        print(f"FAIL bundle dirs {on_disk} != expected {sorted(bundles)}")
        return 1

    failed = 0
    for name in bundles:
        bundle = AGENTS / name
        errors: list[str] = []
        spec = parse(bundle, expand_env=False)
        result = validate(spec)
        errors += [f"validate: {e.path}: {e.message}" for e in result.errors]
        if spec.name != name:
            errors.append(f"name: expected {name!r}, got {spec.name!r}")
        try:
            uploaded = _upload_round_trip(bundle)
        except Exception as exc:  # noqa: BLE001 - report every bundle
            errors.append(f"upload round trip: {type(exc).__name__}: {exc}")
            uploaded = None
        if uploaded is not None and uploaded.instructions != spec.instructions:
            errors.append("upload round trip changed the instructions")
        _check_conventions(name, spec, errors)
        n_cases = _check_guardrails(name, spec, errors)
        n_sdk = _check_sdk_rendering(name, spec, errors)
        n_policies = len(spec.guardrails.policies) if spec.guardrails else 0
        if errors:
            failed += 1
            print(f"FAIL {name}")
            for err in errors:
                print(f"       - {err}")
        else:
            print(
                f"ok   {name:<11} {spec.executor.config['harness']:<13} "
                f"{n_policies} policies, {n_cases} guardrail cases"
                f"{f' (+{n_sdk} as claude-sdk)' if n_sdk else ''}, "
                f"{len(spec.skills)} bundled skills, {len(spec.sub_agents)} sub-agents, "
                f"MCP: {', '.join(sorted(MCP_SERVERS[name])) or 'none'}"
            )
    if failed:
        print(f"\n{failed} bundle(s) failed")
        return 1
    print(f"\nall {len(bundles)} bundles valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
