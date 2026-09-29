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
5. the guardrails behave: every function policy is resolved and built through
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
    "designer": {"shadcn"},
    "scaffolder": {"shadcn"},
    "developer": {"shadcn"},
    "reviewer": set(),
    "integrator": set(),
    "qa": {"chrome-devtools"},
    "security": {"chrome-devtools"},
    "devops": set(),  # deploys with the vercel CLI
    "shipcrew": set(),
}
BUNDLED_SKILLS = {
    "integrator": {"resolve-conflicts"},
    ORCHESTRATOR: {"plan", "dispatch", "verify"},
}

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
BRANCH = "shipcrew/1a2b3c4d-cart"  # shipcrew/<first 8 chars of the task id>-<slug>

BUILDERS = ("builder", "scaffolder")  # owned paths + git/file writes
VERIFIERS = ("security", "qa")  # owned paths + test files only + git add/commit
COMMITTERS = (*BUILDERS, *VERIFIERS)  # commit on the task branch
RUNNERS = COMMITTERS  # run tests, linters, builds
READERS = ("reviewer", "devops", "planner")  # read-only shell
TESTERS = (*RUNNERS, "reviewer")  # may re-run the test suite
WRITE_LIMITED = ("reviewer", "devops", "planner", *VERIFIERS)  # refused outside their files


def _bash(cmd: str) -> tuple[str, dict[str, str]]:
    return "Bash", {"command": cmd}


def _only(profiles: tuple[str, ...], verdict: str = "ALLOW", *, other: str = "ASK") -> dict[str, str]:
    """``verdict`` for *profiles* (and the orchestrator, which has no allowlist), ``other`` else."""
    return {"*": other, "orchestrator": "ALLOW", **{p: verdict for p in profiles}}


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
    ("npm ci", *_bash("npm ci --prefer-offline --no-audit --no-fund"), _only(RUNNERS)),
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
        _only(("security", "qa")),
    ),
    ("curl localhost -o", *_bash("curl -so /tmp/x http://127.0.0.1:3000"), _only(())),
    ("init.sh", *_bash("./init.sh"), _only(("security", "qa"))),
    ("npm install pkg", *_bash("npm install lodash"), _no_verify(_only(()))),
    ("create-next-app", *_bash("npx create-next-app@latest . --ts --yes"), _only(("scaffolder",))),
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
    ("curl deployment -o file", *_bash("curl -so page.html https://tiny-cli.vercel.app"), _only(())),
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


def _with_task_contract(bundle: Path) -> AgentSpec:
    """The bundle as the board starts it: task contract injected by the server code."""
    from omnigent.shipcrew.sessions import inject_task_contract

    with tempfile.TemporaryDirectory() as tmp:
        copy = materialize_bundle(bundle, Path(tmp) / "bundle")
        config = copy / "config.yaml"
        config.write_text(
            inject_task_contract(
                config.read_text(encoding="utf-8"), owned_paths=TASK_OWNED, root=REPO_PATH
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
        if injected.get("owned_paths") != TASK_OWNED or injected.get("root") != REPO_PATH:
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
    if profile == "orchestrator":
        # capacity gate: the 7th dispatch in one turn is refused
        spawn = _build_policies(spec)[
            "spawn_bounds"
        ]  # fresh closure: the matrix above dispatched too
        send = {"type": "tool_call", "data": {"name": "sys_session_send", "arguments": {}}}
        results = [spawn(send)["result"] for _ in range(7)]
        if results != ["ALLOW"] * 6 + ["DENY"]:
            errors.append(f"spawn_bounds: expected 6 ALLOW then DENY, got {results}")
    return len(CASES)


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
        if claude_args and launch[-len(claude_args) :] != claude_args:
            errors.append(f"claude launch args lack the role MCP config: {launch}")
    else:
        if servers:
            errors.append("claude-sdk bundles take no mcp_config (only strict_mcp_config)")
        from omnigent.runtime.workflow import _build_claude_sdk_spawn_env

        if _build_claude_sdk_spawn_env(spec).get("HARNESS_CLAUDE_SDK_STRICT_MCP_CONFIG") != "1":
            errors.append("claude-sdk spawn env lacks HARNESS_CLAUDE_SDK_STRICT_MCP_CONFIG=1")


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
        n_policies = len(spec.guardrails.policies) if spec.guardrails else 0
        if errors:
            failed += 1
            print(f"FAIL {name}")
            for err in errors:
                print(f"       - {err}")
        else:
            print(
                f"ok   {name:<11} {spec.executor.config['harness']:<13} "
                f"{n_policies} policies, {n_cases} guardrail cases, "
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
