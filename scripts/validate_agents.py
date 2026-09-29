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
   literal file name), harness per role, ``permission_mode: auto``, bundled
   skills, the orchestrator's ``tools.agents`` = the roles;
5. the guardrails behave: every function policy is resolved and built through
   omnigent's factory path, then run on a matrix of tool calls (git push,
   gh pr create, .env reads, workflow edits, playwright install, rm -rf /, ...)
   with the expected ALLOW / ASK / DENY per bundle.

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
BUNDLED_SKILLS = {
    "integrator": {"resolve-conflicts"},
    ORCHESTRATOR: {"plan", "dispatch", "verify"},
}

# ── Guardrail behaviour matrix ──────────────────────────────────────────────
# (label, tool name, arguments, {profile: expected verdict}); "*" = every profile
# not listed explicitly. Profiles: worker, reviewer, qa, devops, planner, orchestrator.

REPO_PATH = "/work/mission"


def _bash(cmd: str) -> tuple[str, dict[str, str]]:
    return "Bash", {"command": cmd}


CASES: list[tuple[str, str, dict[str, Any], dict[str, str]]] = [
    # publishing is the orchestrator's job
    (
        "git push",
        *_bash("git push origin shipcrew/T02-cart"),
        {"*": "DENY", "orchestrator": "ALLOW"},
    ),
    (
        "git -C push",
        *_bash("git -C .worktrees/T02 push -u origin shipcrew/T02-cart"),
        {"*": "DENY", "orchestrator": "ALLOW"},
    ),
    ("bare git push", *_bash("git push"), {"*": "DENY"}),
    ("push main", *_bash("git push origin main"), {"*": "DENY"}),
    ("push HEAD:main", *_bash("git push origin shipcrew/T02:main"), {"*": "DENY"}),
    ("chained push", *_bash("git add -A && git commit -m wip && git push"), {"*": "DENY"}),
    ("gh pr create", *_bash("gh pr create --fill"), {"*": "DENY", "orchestrator": "ALLOW"}),
    (
        "gh pr create draft",
        *_bash("gh pr create --draft --fill --head shipcrew/T02-cart"),
        {"*": "DENY", "orchestrator": "ALLOW"},
    ),
    ("gh pr merge", *_bash("gh pr merge 12 --squash"), {"*": "DENY", "orchestrator": "ASK"}),
    (
        "gh api POST",
        *_bash("gh api -X POST repos/o/r/pulls -f title=x"),
        {"*": "DENY", "orchestrator": "ALLOW"},
    ),
    ("gh pr view", *_bash("gh pr view 12 --json state"), {"*": "ALLOW"}),
    ("commit msg says push", *_bash('git commit -m "push the button"'), {"*": "ALLOW"}),
    ("local git", *_bash("git status && git diff origin/main...HEAD && npm test"), {"*": "ALLOW"}),
    # polly's catastrophic set
    ("rm -rf /", *_bash("rm -rf /"), {"*": "DENY"}),
    ("force push", *_bash("git push --force origin shipcrew/T02"), {"*": "DENY"}),
    ("hard reset remote", *_bash("git reset --hard origin/main"), {"*": "DENY"}),
    ("rm -rf build dirs", *_bash("rm -rf node_modules .next"), {"*": "ALLOW"}),
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
    ("sys_os_read .env", "sys_os_read", {"path": ".env"}, {"*": "DENY"}),
    ("Grep .env", "Grep", {"pattern": "KEY", "path": ".env.local"}, {"*": "DENY"}),
    ("cat .env", *_bash("cat .env"), {"*": "DENY"}),
    ("grep .env.local", *_bash("grep DATABASE_URL .env.local"), {"*": "DENY"}),
    ("source ./.env", *_bash("source ./.env && npm run dev"), {"*": "DENY"}),
    ("cat .env.example", *_bash("cat .env.example"), {"*": "ALLOW"}),
    ("vercel env pull", *_bash("vercel env pull .env.local --yes"), {"*": "ALLOW"}),
    ("echo env var name", *_bash('echo "set DATABASE_URL in the environment"'), {"*": "ALLOW"}),
    # CI definitions need a human
    (
        "Write workflow",
        "Write",
        {"file_path": f"{REPO_PATH}/.github/workflows/ci.yml", "content": "x"},
        {"*": "ASK", "reviewer": "DENY", "qa": "DENY", "devops": "DENY", "planner": "DENY"},
    ),
    (
        "Edit workflow",
        "Edit",
        {
            "file_path": f"{REPO_PATH}/.github/workflows/ci.yml",
            "old_string": "a",
            "new_string": "b",
        },
        {"*": "ASK", "reviewer": "DENY", "qa": "DENY", "devops": "DENY", "planner": "DENY"},
    ),
    ("sed -i workflow", *_bash("sed -i 's/npm/pnpm/' .github/workflows/ci.yml"), {"*": "ASK"}),
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
        {"*": "ASK"},
    ),
    (
        "read then write workflow",
        *_bash("ls .github/workflows && sed -i 's/a/b/' .github/workflows/ci.yml"),
        {"*": "ASK"},
    ),
    # no browser downloads
    ("playwright install", *_bash("npx playwright install chromium"), {"*": "DENY"}),
    ("playwright install deps", *_bash("pnpm exec playwright install --with-deps"), {"*": "DENY"}),
    ("playwright install-deps", *_bash("npx playwright install-deps"), {"*": "DENY"}),
    (
        "playwright test",
        *_bash("CHROMIUM_PATH=/usr/bin/chromium npx playwright test"),
        {"*": "ALLOW"},
    ),
    # writes by profile
    (
        "Write source",
        "Write",
        {"file_path": f"{REPO_PATH}/app/page.tsx", "content": "x"},
        {"*": "ALLOW", "reviewer": "DENY", "qa": "DENY", "devops": "DENY", "planner": "DENY"},
    ),
    (
        "Write qa.json",
        "Write",
        {"file_path": f"{REPO_PATH}/.shipcrew/qa.json", "content": "{}"},
        {"*": "ALLOW", "reviewer": "DENY", "devops": "DENY", "planner": "DENY"},
    ),
    (
        "Write deploy.json",
        "Write",
        {"file_path": f"{REPO_PATH}/.shipcrew/deploy.json", "content": "{}"},
        {"*": "ALLOW", "reviewer": "DENY", "qa": "DENY", "planner": "DENY"},
    ),
    (
        "sys_os_write plan.json",
        "sys_os_write",
        {"path": ".shipcrew/plan.json", "content": "{}"},
        {"*": "ALLOW", "reviewer": "DENY", "qa": "DENY", "devops": "DENY"},
    ),
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
    "reviewer": "reviewer",
    "qa": "qa",
    "devops": "devops",
    "planner": "planner",
    ORCHESTRATOR: "orchestrator",
}

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


def _check_guardrails(name: str, spec: AgentSpec, errors: list[str]) -> int:
    profile = PROFILE.get(name, "worker")
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
    if config.get("permission_mode") != "auto":
        errors.append(f"permission_mode: expected 'auto', got {config.get('permission_mode')!r}")
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
                f"{len(spec.skills)} bundled skills, {len(spec.sub_agents)} sub-agents"
            )
    if failed:
        print(f"\n{failed} bundle(s) failed")
        return 1
    print(f"\nall {len(bundles)} bundles valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
