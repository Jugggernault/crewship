# shipcrew agent bundles

The shipcrew crew as omnigent agent
bundles (`spec_version: 1` directories: `config.yaml` + `AGENTS.md` + optional
`skills/`). Every role is Claude, since the only provider available is a
Claude subscription. No model is pinned, so each bundle runs on the Claude
provider configured with `omnigent setup`.

- `agents/<role>/` is the bundle the board launches as a task's root session
  (`POST /v1/shipcrew/tasks/{id}/start` with the task's `role`).
- `agents/shipcrew/` is the orchestrator (modelled on omnigent's `polly`). Its
  `agents/<role>` entries are symlinks to the same role bundles; omnigent
  dereferences symlinks when it copies a bundle before packing it.

## Roles

| role | harness | skills it uses | guardrails (on top of the common set) | verdict line |
|---|---|---|---|---|
| `shipcrew` (orchestrator) | `claude-sdk`, `spawn: true`, `tools.agents` = the 9 roles | bundled: `plan`, `dispatch`, `verify` | pushes only `shipcrew/<id8>-<slug>` task branches, each named explicitly (every push of a chained command is checked), never `main`/`master`/`HEAD`, `--all`/`--mirror`/`--tags`/`--delete`/`+refspec`; `gh pr merge` / `repo delete` / `release create` ASK; max 6 dispatches per turn; every dispatch declares a purpose (`plan`, `implement`, `review`, `verify`, `explore`, `search`) | `PASS` / `FAIL: <reason>` |
| `planner` (PM + architect) | `claude-sdk` | none | read-only shell allowlist; writes only `.shipcrew/plan.json` | `PASS` / `FAIL` |
| `designer` | `claude-native` | `shipcrew:design-lock`, `impeccable` | builder allowlist, owned paths | `PASS` / `FAIL` |
| `scaffolder` | `claude-native` | `vercel:nextjs`, `vercel:shadcn`, `vercel:vercel-storage`, `shipcrew:design-lock` | scaffolder allowlist (builder + generators, dependency changes), owned paths | `PASS` / `FAIL` |
| `developer` | `claude-native` | `superpowers:test-driven-development`, `superpowers:verification-before-completion`, `shipcrew:design-lock`, `vercel:nextjs`, `vercel:shadcn` | builder allowlist, owned paths | `PASS` / `FAIL` |
| `reviewer` | `claude-native`, a fresh session each round | `code-review`, `security-review`, `shipcrew:design-lock` | read-only shell allowlist plus test runners; read-only (`read_only_os`: every write/edit refused) | `APPROVE` / `CHANGES: <summary>` |
| `integrator` | `claude-native` | bundled: `resolve-conflicts` | builder allowlist, owned paths | `PASS` / `FAIL` |
| `qa` | `claude-native` | `shipcrew:design-lock`, `impeccable`, chrome-devtools MCP | qa allowlist (read, test, run the app, curl localhost; no shell writes); writes only `.shipcrew/qa.json` | `PASS` / `FAIL: <n> failures` |
| `security` | `claude-native` | `security-review`, chrome-devtools MCP | security allowlist (builder + run the app, curl localhost), owned paths | `PASS` / `FAIL` |
| `devops` | `claude-native` | `vercel:deploy`, `vercel:deployments-cicd` | devops allowlist (read-only + Vercel reads); writes only `.shipcrew/deploy.json` | `PASS` / `FAIL` |

Permissions are set up so that nothing ever uses `bypassPermissions` (see
[Permissions](#permissions-allowlists-and-owned-paths)).
`superpowers:verification-before-completion` is required by the
common rules for every role. Skills are referenced by their installed names.
Only role-specific skills are vendored into a bundle (`resolve-conflicts`, and
the orchestrator's `plan` / `dispatch` / `verify`).

**Reviewer independence without a second vendor.** polly gets independence
from a different vendor. We only have Claude, so the orchestrator starts a
**new** reviewer session for every review round (`review-<key>-r<n>`). That
session gets only a saved diff snapshot and the task contract, never the
implementer's worktree or transcript. It can't edit files, and it may be given
a stronger `args.model` than the implementer.

### Common guardrail set (every bundle)

Enforced at the policy layer (omnigent `guardrails.policies`, evaluated on
native Claude tool calls through the PreToolUse hook), not only in the prompt:

| policy | effect |
|---|---|
| `blast_radius` (`gate_pushes: false`) | polly's catastrophic DENY set: force-push, `rm -rf /` or a system dir, hard reset to a remote ref |
| `shipcrew_no_remote_writes` (workers) | DENY `git push`, `gh pr create/merge/close/reopen/ready`, `gh release create`, `gh repo create/delete/fork`, `gh api -X POST/PUT/PATCH/DELETE`. The orchestrator pushes. |
| `shipcrew_no_env_read` | DENY reading `.env`, `.env.local`, `.env.*` through Read/Grep/`sys_os_read` or the shell (`.env.example`, `.sample`, `.template` allowed; `vercel env pull` is not denied here, the shell allowlists ask for it) |
| `shipcrew_workflows_approval` | ASK before any write to `.github/workflows/**` (an approval card in the Inbox; waits up to 24 h). Read-only shell passes. |
| `shipcrew_no_browser_download` | DENY `playwright install` (and `install-deps`, puppeteer browser downloads). Use `$CHROMIUM_PATH`. |

A denied call is final. The rules explain why, so the agent doesn't retry.

## Permissions: allowlists and owned paths

The policies live in the omnigent fork (`omnigent/shipcrew/policies.py`,
registered in omnigent's policy registry so uploaded bundles may use them) and
are configured from `_shared/policies/`:

| policy | fragment | effect |
|---|---|---|
| `shipcrew_shell_allowlist` | `shell_allowlist_<profile>.yaml` | A shell command runs with **no prompt** when every simple command in it (split on `;` `&&` `\|\|` `\|` `&` and newlines, quote-aware) matches the role's allowlist. Anything else is **ASK**. So is a command with `$(..)`, backticks, `<(..)` or a heredoc, except Claude Code's `git commit -m "$(cat <<'EOF' ... EOF)"` idiom. An env prefix is allowed only for known names (`CI`, `CHROMIUM_PATH`, `PORT`, `NODE_ENV` ...). `timeout`/`time`/`nohup`, `/usr/bin/` and `node_modules/.bin/` prefixes, `pnpm exec` and `git --no-pager` are unwrapped first. |
| `shipcrew_owned_paths` | `owned_paths.yaml` | A write outside the task's `owned_paths` is **ASK**. That covers Write/Edit/MultiEdit/NotebookEdit/`sys_os_write`, shell redirections, `cp`/`mv`/`rm`/`touch`/`mkdir`/`tee`/`chmod`, `git mv`/`rm`/`restore`/`checkout --`, `prettier --write`, `eslint --fix`, `ruff format`, and dependency changes (`npm install <pkg>` and similar). A write to `package.json`, a lockfile or `pyproject.toml`, at any depth, is **ASK** unless the task lists that file by name. `cd` and `git -C` are tracked. Always free: build output and caches (`node_modules`, `.next`, `dist`, `coverage`, `test-results` ...) and, outside the worktree, `/tmp` and `/dev/null`. Reads are never gated. |
| `shipcrew_orchestrator_push_guard` | `orchestrator_push_guard.yaml` | Every `git push` must name refspecs, and each one must be `shipcrew/<first 8 chars of the task id>-<slug>` (the one branch scheme the server's worktrees use too). Anything else is **DENY**. `gh pr merge` / `repo delete` / `release create` are **ASK**. |

Allowlists are built from shared command groups in
`_shared/policies/allowlists/`. A fragment line `# @include allowlists/<group>`
is expanded by `build_agents.py`:

| profile (roles) | groups |
|---|---|
| builder (developer, designer, integrator) | `read_only`, `git_read`, `git_write`, `fs_write`, `dev_tools` |
| scaffolder | builder + `scaffold` (create-next-app, shadcn, drizzle-kit, `npm install <pkg>`) |
| security | builder + `run_app` |
| qa | `read_only`, `git_read`, `dev_tools`, `run_app`; `shell_writes: false` |
| read-only (planner) | `read_only`, `git_read`; `shell_writes: false` |
| reviewer | `read_only`, `git_read`, `test_runners` (npm test, node --test, vitest run, jest, pytest); `shell_writes: false` |
| devops | `read_only`, `git_read`, `vercel_read`; `shell_writes: false` |

The groups:

- `read_only`: `ls cat head tail wc rg grep find`, with `-exec`/`-delete`
  refused. `sed -n` with print scripts only. Also `jq yq`, `sort` without `-o`,
  `diff stat du pwd cd echo printf which` and similar, `python3 -m json.tool`,
  `<tool> --version`, and the `gh pr/run/issue/repo view|list` reads.
- `git_read`: `status diff log show rev-parse ls-files blame grep merge-base`,
  the listing forms of `branch`/`tag`/`remote`/`stash`/`reflog`, and
  `config --get`. `--output` is refused.
- `git_write`: `add commit branch switch checkout merge rebase cherry-pick
  revert restore reset mv rm`, `stash push -m` and `fetch`. Refused: branch
  delete/move, `rebase -i`/`--exec`, and `reset --hard/--merge/--keep`. There
  is no push and no remote write (`shipcrew_no_remote_writes` DENYs them).
- `fs_write`: `mkdir touch cp mv rm rmdir chmod tee`, each target judged by
  owned paths.
- `dev_tools`: installs from the lockfile only (`npm ci`, `pnpm install
  --frozen-lockfile`, `uv sync`, `uv pip install -r/-e`), plus `npm test`,
  `npm run <script>`, the pnpm/yarn equivalents, `node --test`, `vitest`,
  `jest`, `playwright test`, `tsc`, `eslint`, `prettier`, `next build/lint`,
  `biome`, `stylelint`, `@google/design.md lint`, `pytest` (plain, `uv run`,
  `.venv/bin`), `ruff mypy pyright` and `black --check`.
- `run_app`: `./init.sh`, `npm start`, `next dev/start`, `ps ss lsof kill
  pkill`, and `curl` to `localhost`/`127.0.0.1`/`[::1]` only. curl may not
  write a file: `-o`, `-O`, `-T`, `-K`, `-c`, `-D` and `--proxy` are refused.
- `vercel_read`: `vercel ls/inspect/whoami/logs/project ls/env ls/domains
  ls/alias ls`. `vercel link`/`git connect`/`deploy`/`env pull` ASK.

**How it reaches the session.** When the board starts a task, the server
(`omnigent/shipcrew/sessions.py`) packs the role bundle and writes the task's
`owned_paths` and absolute worktree path over the two `# @task.*` slots of
`shipcrew_owned_paths` (`inject_task_contract`). The contract is stored with
the session's bundle on the server, so the agent cannot edit it. If the
bundle is started without a task contract (not from a board card), the owned
paths policy abstains.

**ASK, ALLOW and Claude's own prompt (claude-native).** The guardrails run in
omnigent's `PreToolUse` hook. The outcomes are:

- **ASK**: the server holds the hook, publishes an approval card (Inbox), and
  the board card goes to **Intervention**.
- **Approve** (`accept`): the tool runs.
- **Deny** (`decline`): omnigent refuses the call **and interrupts the
  agent's turn**. The card then drops to Review, idle.
- **`cancel`**: refuses the call only, and the agent continues with the
  reason.
- **DENY** from a policy is final.
- **ALLOW** (or no match) hands the call back to Claude's own permission
  system.

So that Claude never adds a second prompt for tools the guardrails govern,
claude-native bundles use `permission_mode: default` plus `allowed_tools:`
(Bash, Edit, Write, MultiEdit, NotebookEdit, Read, Glob, Grep, ... and the role's
MCP servers). The omnigent fork turns `allowed_tools` into Claude's
`--allowedTools` launch flag. Any other tool still gets Claude's prompt,
which omnigent also routes to the Inbox. If the policy server is unreachable,
the hook fails to "ask". claude-sdk bundles (planner, orchestrator) keep
omnigent's `auto`: the SDK pre-approves its tools and the same guardrails
gate them in-process. Nothing uses `bypassPermissions`.

### What the allowlists do not stop (accepted risks)

The shell allowlists gate the commands an agent *types*. They do not sandbox
the code those commands run. These risks are accepted on purpose:

- **Test runners and `npm run <script>` run arbitrary code.** A builder writes
  test files and then runs them (`npm test`, `node --test`, `vitest`,
  `pytest`), and `npm run *` runs any `package.json` script. So a builder can
  execute anything the OS user can, including writing outside its owned
  paths. The same holds for the reviewer's and qa's test runners, which run
  the branch's code. The backstop is on the server: before a merge, the PR
  loop checks the **whole diff** against the task's `owned_paths` and against
  the `APPROVALS.md` rules. The defaults (`.github/**`, `auth/**`,
  migrations, `.env*`, `infra/**`, `agents/**`) always apply, and a repo file
  can only add rules. Any match is held for `POST /tasks/{id}/approve`,
  whoever or whatever wrote the file. Nothing is pushed except by the server,
  and only to the task branch.
- **`npx <name>`** is allowlisted only for fixed package names (`vitest`,
  `jest`, `tsc`, `eslint`, `prettier`, `next`, `biome`, `stylelint`,
  `@google/design.md`, and for the scaffolder `create-next-app@*`, `shadcn@*`,
  `drizzle-kit`). A package that is not installed is downloaded from the npm
  registry under that name. Typosquatting is ruled out because the names are
  exact, but a compromised upstream release is not. `npx -p <pkg>`,
  `npm exec --package=<pkg>` and any other name ask.
- **`kill` / `pkill`** (qa and security, `run_app`) can end any process of the
  OS user, including the omnigent server or other agents. This is accepted so
  those roles can stop the dev servers they start. Run the board as a
  dedicated user if that matters to you.
- **`curl` to localhost** (qa, security) can reach every local service,
  including the omnigent API. Run the server with auth, so that an agent
  without a token cannot approve its own cards or merges.
- **Parsing is conservative, not perfect.** A command whose words the shell
  would rewrite always asks: `$VAR`, `${..}` and `$'..'` outside single
  quotes, brace expansion, a glob in an option name, `$(..)`, backticks and
  heredocs (Claude Code's commit-message heredoc is the one exception). A
  refused option also matches its abbreviations (`git reset --har`, `git
  fetch --upload-p=`) and short-option clusters (`git rebase -xcmd`, `sort
  -uo`).

### Live check (2026-09-29)

The test ran on a real stack: `scripts/shipcrew_stack.sh` on port 16771, with
its own state dir and the bundles of this branch. The Claude Code 2.1.284
subscription login ran one developer-bundle task on a throwaway npm repo
(`"test": "node --test"`, owned paths `src/**`, `test/**`):

- **Launch.** The session started with `terminal_launch_args` =
  `--permission-mode default --allowedTools Bash,Edit,Write,...`.
- **`npm test`.** It ran immediately, with no approval card and no Claude
  prompt, and passed (1 test).
- **`node -e "console.log(40 + 2)"`** (not allowlisted).
  - One approval card appeared: `shipcrew_shell_allowlist: node -e
    console.log(40 + 2) is not on the builder shell allowlist`.
  - The board card went Running → **Intervention**.
  - After Approve, the command ran once and printed `42`, with no second
    Claude prompt. The card went back to Running.
- **Write `notes/smoke.txt`** (outside the owned paths).
  - One approval card appeared: `shipcrew_owned_paths: notes/smoke.txt is
    outside this task's owned paths (src/**, test/**)`.
  - The card went to **Intervention**.
  - After Deny, Claude got `PreToolUse:Write hook error: ...` and omnigent
    interrupted the turn. The card went to Review. The file was not created
    and the worktree stayed clean.
- **Bug found and fixed.** The first run exposed one bug: the throwaway
  worktree lived under `/tmp`, and the `/tmp/**` free-path rule matched
  before the worktree check, so the write went through. The worktree check
  now runs first, and there is a regression test.

### PR-loop e2e (2026-09-29)

The omnigent round-2 integration ran planner, developer and reviewer bundles
end to end on port 16772 (record: omnigent `docs/shipcrew/STATUS.md`). The
developer ran `npm test` and `git commit` with no prompt. A chained
`git fetch ...; ls .github/workflows` asked on `shipcrew_workflows_approval`.
The reviewer's `npm test` asked on the read-only allowlist, which led to the
reviewer's own `test_runners` allowlist.

## Layout

```
agents/
  _shared/COMMON.md          common rules (stack, fake DB, CHROMIUM_PATH, limits, done)
  _shared/policies/*.yaml    guardrail fragments, one policy each
  _shared/policies/allowlists/*.yaml  shell command groups (# @include'd by the allowlist fragments)
  <role>/ROLE.md             role prompt (hand-written)
  <role>/AGENTS.md           GENERATED = ROLE.md + COMMON.md  (config: instructions: AGENTS.md)
  <role>/config.yaml         bundle spec; its guardrails block is GENERATED between markers
  integrator/skills/resolve-conflicts/SKILL.md
  shipcrew/skills/{plan,dispatch,verify}/SKILL.md
  shipcrew/agents/<role> -> ../../<role>
```

**Why the build step?** An omnigent bundle can't include files from outside
its own directory. The parser reads `instructions:` only when the file is inside
the bundle, and treats any other value (such as `../_shared/COMMON.md`) as
literal prompt text. YAML has no include either. So the shared rules and
policies have a single source in `_shared/`, and `scripts/build_agents.py`
renders them into each bundle. Never edit a generated `AGENTS.md` or a
generated guardrails block by hand.

```bash
python3 scripts/build_agents.py            # after editing ROLE.md / COMMON.md / a policy fragment
python3 scripts/build_agents.py --check    # CI / pre-commit: fails if generated files are stale
```

To change a role's policy set, edit the names on its
`# >>> shipcrew-guardrails: ...` marker line and rebuild.

## Validation

```bash
cd /path/to/omnigent && uv run python /path/to/shipcrew/scripts/validate_agents.py
```

For each bundle, the validator does the following:
- runs `build_agents.py --check`;
- runs omnigent's `parse` + `validate`;
- repeats the server upload path: `materialize_bundle`, then tar.gz, then
  `load(bytes, enforce_handler_allowlist=True)`, with safe extraction and only
  registered policy handlers allowed;
- checks shipcrew conventions:
  - the harness per role;
  - claude-native bundles use `permission_mode: default` plus `allowed_tools`,
    which omnigent's launch-arg derivation turns into `--allowedTools`;
    claude-sdk bundles use `auto`; nothing uses bypassPermissions;
  - no pinned model;
  - instructions are actually read from `AGENTS.md`;
  - bundled skills;
  - the orchestrator's `tools.agents` equals the roles;
- builds every guardrail through omnigent's factory path and runs 117
  tool-call cases per bundle (plus the dispatch cap), expecting a specific
  ALLOW, ASK or DENY for each. The cases cover:
  - allowlisted commands (ALLOW, no prompt);
  - other commands (ASK);
  - writes in and out of the owned paths, and to `package.json`;
  - the old DENY set (push, PRs, `.env`, `playwright install`, `rm -rf /`);
  - the branch scheme.

  Worker bundles get the task contract injected with the server's own
  `inject_task_contract`. Without a contract, the owned paths policy must
  abstain.

## Contract with the shipcrew server module

- **Task input.** The root session's first message is the task contract:
  worktree path, title, body, acceptance, `owned_paths`, and the tasks already
  merged.
- **Final line.** Every worker ends with exactly one verdict line: `PASS`,
  `FAIL: <reason>`, `APPROVE` or `CHANGES: <summary>`.
- **Output files.** The planner writes `.shipcrew/plan.json`. Tasks refer to
  each other by `key`, so `depends_on` holds keys that the board maps to task
  ids. The schema is in `planner/ROLE.md`. qa writes `.shipcrew/qa.json`,
  security writes `.shipcrew/security.md` and devops writes
  `.shipcrew/deploy.json`.
- **Board mode.** Send the orchestrator `mode: plan-only` when the board's
  scheduler runs the tasks. It then stops once `plan.json` is valid.
- **Publishing.** Workers never push. The module, or the orchestrator in full
  mode, pushes `shipcrew/<id8>-<slug>` branches (first 8 chars of the task
  id, slug of the title) and opens draft PRs.
