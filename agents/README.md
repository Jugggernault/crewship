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
| `designer` | `claude-native` | bundled: `design-lock`; shadcn MCP | builder allowlist, owned paths | `PASS` / `FAIL` |
| `scaffolder` | `claude-native` | bundled: `design-lock`; shadcn MCP | scaffolder allowlist (builder + generators, dependency removals), owned paths | `PASS` / `FAIL` |
| `developer` | `claude-native` | bundled: `design-lock`; shadcn MCP | builder allowlist, owned paths | `PASS` / `FAIL` |
| `reviewer` | `claude-native`, a fresh session each round | built-in `code-review`; bundled: `design-lock` | read-only shell allowlist plus test runners; read-only (`read_only_os`: every write/edit refused) | `APPROVE` / `CHANGES: <summary>` |
| `integrator` | `claude-native` | bundled: `resolve-conflicts` | builder allowlist, owned paths | `PASS` / `FAIL` |
| `qa` (verify) | `claude-native` | bundled: `design-lock`; chrome-devtools MCP | verify allowlist (read, test, run the app, curl localhost, `git add`/`commit`); writes only test files inside its owned paths and `.shipcrew/qa.json` (`shipcrew_test_writes_only`) | findings JSON + `PASS` / `FAIL: <n> failures` |
| `security` (verify) | `claude-native` | chrome-devtools MCP | verify allowlist (as qa); writes only PoC/regression tests inside its owned paths and `.shipcrew/security.md` | findings JSON + `PASS` / `FAIL` |
| `devops` | `claude-native` | none (exact `vercel` commands in its ROLE) | devops allowlist (read-only, Vercel reads, the exact ship commands, curl to `*.vercel.app`); writes only `.shipcrew/deploy.json` | `DEPLOYED: <url>` / `FAIL: <reason>` |

Permissions are set up so that nothing ever uses `bypassPermissions` (see
[Permissions](#permissions-allowlists-and-owned-paths)).
Sessions load none of the host user's Claude settings (see
[Settings sources](#no-host-user-settings-plugins-or-hooks)), so no user plugin
skill (`vercel:*`, `superpowers:*`, `shipcrew:*`) is available: a skill a role
needs ships in its bundle. `design-lock` is a symlink to the repo's
`skills/design-lock` in designer, scaffolder, developer, reviewer and qa
(dereferenced when the bundle is packed); `resolve-conflicts` and the
orchestrator's `plan` / `dispatch` / `verify` are vendored. omnigent passes
the bundle with `--plugin-dir`, so they load as `<role>:<skill>`. Claude Code's
built-in skills (`code-review`, `simplify`, `verify`, ...) stay.

**Reviewer independence without a second vendor.** polly gets independence
from a different vendor. We only have Claude, so the orchestrator starts a
**new** reviewer session for every review round (`review-<key>-r<n>`). That
session gets only a saved diff snapshot and the task contract, never the
implementer's worktree or transcript. It can't edit files, and it may be given
a stronger `args.model` than the implementer.

### Decisions and the ship stage

Every role except the reviewer ends its final reply with a `Decisions:` list
right before the verdict line (`_shared/COMMON.md`): what it chose without
asking, one line each, or `Decisions: none`. The omnigent fork parses it
(`omnigent/shipcrew/decisions.py`) into the card's `decisions` (merged across
fix turns) and the planner's into `mission.plan_decisions`; the board drawer
and the mission report show them.

Generated apps stay light (`_shared/COMMON.md`, "Keep the app light"): every
dependency is justified in `Decisions:`, platform / framework built-ins come
first, the Foundation installs only the minimal toolchain (one unit runner, one
e2e runner, faker for `lib/db.ts`) and answers every API stub with a valid
empty shape (never 501), and the reviewer gets the developer's decisions in its
prompt and flags an unjustified or built-in-duplicating dependency (`major`,
`CHANGES`). Each generated `AGENTS.md` is kept near 12 KB: COMMON.md is the
only shared text, ROLE.md files do not repeat it.

Nobody plans a deploy task any more. When every agent task of a mission is
merged, the server ships it (`omnigent/shipcrew/ship.py`): a `devops` session
in a fresh worktree of `main` runs `vercel link --yes --project <repo-name>`
and `vercel deploy --prod --yes` and ends with `DEPLOYED: <url>`; the server
then checks the URL itself and writes the report. A blocked or intervention
card stops the ship (the reason shows on the mission).

### CI workflow: installed by the server

Agents never write `.github/workflows/**` (the workflows guard asks a human).
When the first task of a mission starts, the server commits
`omnigent/shipcrew/templates/ci.yml` to `origin/main` itself (`chore: shipcrew
CI`, one plumbing commit + fast-forward push, skipped when `main` already has
`.github/workflows/ci.yml`; `SHIPCREW_INSTALL_CI=0` turns it off), before any
task branch is cut. The workflow detects pnpm / yarn / npm from the lockfile
and runs `lint`, `typecheck`, `test`, `build`, `e2e` only when `package.json`
defines them, with `CHROMIUM_PATH=/usr/bin/google-chrome` (preinstalled on
`ubuntu-latest`, never `playwright install`). The scaffolder only makes the
scripts match.

### MCP servers per role

Every bundle runs with `strict_mcp_config: true`: the session loads only the
MCP servers its bundle lists, plus omnigent's own relay (the `sys_*` tools).
None of the host user's servers leak in: no claude.ai connectors (Gmail,
Canva, Notion, Vercel, Figma, ...), no `~/.claude.json` servers, no plugin
servers. That saves context and narrows what a prompt-injected agent can reach.

| role | MCP servers |
|---|---|
| `developer`, `scaffolder`, `designer` | `shadcn` (`npx -y shadcn@latest mcp`) |
| `qa` | `chrome-devtools` (headless, `--isolated`, `${CHROMIUM_PATH:-/usr/bin/chromium}`) |
| `devops` | none (deploys with the `vercel` CLI) |
| `reviewer`, `integrator`, `security`, `planner`, `shipcrew` | none |

The servers live in `_shared/mcp/<server>.json` (one server object each). A
bundle names them on its `# >>> shipcrew-mcp: <server> ...` marker inside
`executor.config`, and `scripts/build_agents.py` renders `strict_mcp_config`
and `mcp_config` (JSON) between the markers. The `mcp__<server>` entries of
`allowed_tools` must match (the validator checks it).

In the omnigent fork (`omnigent/shipcrew/launch_args.py`), claude-native gets
`--strict-mcp-config --mcp-config <json>` next to `--allowedTools`. The bridge
then appends the relay's own `--mcp-config`: Claude merges repeated flags and
strict mode keeps every server passed that way. claude-sdk gets
`--strict-mcp-config` through `HARNESS_CLAUDE_SDK_STRICT_MCP_CONFIG=1`, and its
in-process `omnigent` server stays. Checked live on 2026-09-29: a developer
session saw exactly `omnigent` and `shadcn`, and `sys_os_read` worked.

### No host-user settings, plugins or hooks

Every bundle also declares `setting_sources: project,local` (rendered by
`build_agents.py` next to the MCP scoping). claude-native sessions get
`--setting-sources project,local`; claude-sdk sessions get
`HARNESS_CLAUDE_SDK_SETTING_SOURCES`, which becomes
`ClaudeAgentOptions.setting_sources` (omnigent fork,
`omnigent/shipcrew/launch_args.py`). `~/.claude/settings.json` is skipped, so
the host user's enabled plugins (and their SessionStart hooks: the live run
showed ponytail, superpowers and vercel output in worker sessions), user
skills and `~/.claude/CLAUDE.md` stay out. The subscription login is not a
setting and keeps working; omnigent's own `--settings` file (hooks, permission
relay) and the bundle's `--plugin-dir` still apply. Checked against the real
CLI (2.1.285): with the flag the init event lists only the built-in plugins and
18 built-in skills, no hook runs, and the turn answers; without it 8 user
plugins, 124 skills and 5 SessionStart hooks load.

### Common guardrail set (every bundle)

Enforced at the policy layer (omnigent `guardrails.policies`, evaluated on
native Claude tool calls through the PreToolUse hook), not only in the prompt:

| policy | effect |
|---|---|
| `blast_radius` (`gate_pushes: false`) | polly's catastrophic DENY set: force-push, `rm -rf /` or a system dir, hard reset to a remote ref |
| `shipcrew_no_remote_writes` (workers) | DENY `git push`, `gh pr create/merge/close/reopen/ready`, `gh release create`, `gh repo create/delete/fork`, `gh api -X POST/PUT/PATCH/DELETE`. The orchestrator pushes. |
| `shipcrew_no_env_read` | DENY reading `.env`, `.env.local`, `.env.*` through Read/Grep/`sys_os_read` or the shell (`.env.example`, `.sample`, `.template` allowed; `vercel env pull` is not denied here, the shell allowlists ask for it) |
| `shipcrew_workflows_approval` | ASK before any write to `.github/workflows/**` (an approval card in the Inbox; waits up to 24 h): write tools, shell write targets, or a non-reader command naming a workflow path (`omnigent.shipcrew.policies.workflows_guard`). Reads pass, also chained with other commands. |
| `shipcrew_no_browser_download` | DENY `playwright install` (and `install-deps`, puppeteer browser downloads). Use `$CHROMIUM_PATH`. |

A denied call is final. The rules explain why, so the agent doesn't retry.

## Permissions: allowlists and owned paths

The policies live in the omnigent fork (`omnigent/shipcrew/policies.py`,
registered in omnigent's policy registry so uploaded bundles may use them) and
are configured from `_shared/policies/`:

| policy | fragment | effect |
|---|---|---|
| `shipcrew_shell_allowlist` | `shell_allowlist_<profile>.yaml` | A shell command runs with **no prompt** when every simple command in it (split on `;` `&&` `\|\|` `\|` `&` and newlines, quote-aware) matches the role's allowlist (so `a 2>&1 \| tail -5; b \|\| c && d &` and a trailing `wait` pass when each part does). Anything else is **ASK**. So is a command with `$(..)`, backticks, `<(..)` or a heredoc, except Claude Code's `git commit -m "$(cat <<'EOF' ... EOF)"` idiom. An env prefix is allowed only for known names (`CI`, `CHROMIUM_PATH`, `PORT`, `NODE_ENV`, `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD`, `NEXT_TELEMETRY_DISABLED`, `FORCE_COLOR`, `NO_COLOR`, `TZ`, `LANG`, `DEBUG` ...), its value may be `${PORT:-3000}`. Variables: `$?` `$#` `$$` `$!` and `${PIPESTATUS[n]}` always pass; `$VAR` / `${VAR}` / `${VAR:-x}` need a vetted name (the env prefixes, `HOME`, `PWD`, `USER`, `PATH`, `TMPDIR`, or a name assigned earlier in the same command; `echo $DATABASE_URL` asks). They pass anywhere inside an expansion-safe read-only command (the `read_only:` list: `read_only` + `git_read` entries without a `!banned` option or glob word, not `cd`/`printf`/`find`/`sed`/`jq`...). In any other allowlisted command (`npx next start -p ${PORT:-3000}`, `pkill -f "next start -p $PORT"`, `curl localhost:$PORT/x`, `npx vitest --port $PORT`) a vetted name passes when the command does not assign it (its value comes from the session environment), it is a plain argument (not the program, not an option name), the program writes no file or git state (`git`, `cp`/`mv`/`rm`/`mkdir`/`tee`..., `sed`, `find`... always ask), and the command matches with the `:-` default (or a typical value) in its place, so a banned option hidden in a default is still refused. A bare `S=/path;` makes the rest of the chain read-only-only, and `PATH`/`LD_*`/`GIT_*`/`NODE_*`/... assignments ask. A write target with a variable asks. | `timeout`/`time`/`nohup`, `/usr/bin/` and `node_modules/.bin/` prefixes, `pnpm exec` and `git --no-pager` are unwrapped first. |
| `shipcrew_owned_paths` | `owned_paths.yaml` | A write outside the task's `owned_paths` is **ASK**. That covers Write/Edit/MultiEdit/NotebookEdit/`sys_os_write`, shell redirections, `cp`/`mv`/`rm`/`touch`/`mkdir`/`tee`/`chmod`, `git mv`/`rm`/`restore`/`checkout --`, `prettier --write`, `eslint --fix`, `ruff format`, and dependency changes (`npm install <pkg>` and similar). A write to `package.json`, a lockfile or `pyproject.toml`, at any depth, is **ASK** unless the task lists that file by name; owning `package.json` (or `pyproject.toml`) by name owns the lockfiles and package-manager files next to it (`pnpm-workspace.yaml`, `.npmrc`, `.nvmrc`, `.node-version`; same rule in the PR loop's diff check). `cd` and `git -C` are tracked. Always free: build output and caches (`node_modules`, `.next`, `dist`, `coverage`, `test-results` ...) and, outside the worktree, `/tmp` and `/dev/null`. Reads are never gated. A write to a file another in-progress task of the mission owns is **DENY** with a hint to work against the shared contract (no card; see below). |
| `shipcrew_test_writes_only` | `test_writes_<role>.yaml` (qa, security) | Verify roles write **test files only**: `test/**`, `tests/**`, `e2e/**` (top level), `**/__tests__/**`, `**/__snapshots__/**`, `**/*.test.*`, `**/*.spec.*`, plus their report file. Any other write (write tools and shell targets, as for owned paths) is **DENY**, with a reason that says to report the defect instead: the board turns a `FAIL` into a developer fix task. Combined with `shipcrew_owned_paths` (`owned_paths_<role>.yaml`, report file free), a test outside the task's owned paths still ASKs. The PR loop re-checks the whole diff: a non-test file in a verify PR needs a human approval. Add-only: deleting, renaming away or truncating a test file that exists on `origin/main` (another task's: `rm`, `git rm`, `mv`/`git mv` source, `truncate`, `>`, a full `Write`) is **DENY**; `Edit` and `>>` pass, and a removal that cannot be checked (no git answer) is refused. |
| `shipcrew_orchestrator_push_guard` | `orchestrator_push_guard.yaml` | Every `git push` must name refspecs, and each one must be `shipcrew/<first 8 chars of the task id>-<slug>` (the one branch scheme the server's worktrees use too). Anything else is **DENY**. `gh pr merge` / `repo delete` / `release create` are **ASK**. |

Allowlists are built from shared command groups in
`_shared/policies/allowlists/`. A fragment line `# @include allowlists/<group>`
is expanded by `build_agents.py`:

| profile (roles) | groups |
|---|---|
| builder (developer, designer, integrator) | `read_only`, `git_read`, `git_write`, `fs_write`, `dev_tools`, `deps`, `fs_edit`, `local_http` |
| scaffolder | builder + `scaffold` (create-next-app, shadcn, drizzle-kit, `npm uninstall` / `pnpm remove`) |
| security, qa (verify roles) | `read_only`, `git_read`, `git_commit` (`git add`, `git commit`), `fs_write`, `dev_tools`, `run_app`, `local_http`; every write target judged by `shipcrew_test_writes_only` + owned paths |
| read-only (planner) | `read_only`, `git_read`; `shell_writes: false` |
| reviewer | `read_only`, `git_read`, `test_runners` (npm test, node --test, vitest run, jest, pytest); `shell_writes: false` |
| devops | `read_only`, `git_read`, `vercel_read`, `vercel_deploy`; `shell_writes: false` |

The groups:

- `read_only`: `ls cat head tail wc rg grep find uniq wait`, with
  `find -exec`/`-delete`, `rg --pre` and `printf -v` refused (`uniq IN OUT`
  is judged as a write). `sed -n` with print scripts only on files, and `sed`
  as a pipe filter (`@sed:sed_filter`: stdin only, no file operand, no
  `-i`/`-f`/`-s`, scripts of `s` (no `w`/`e` flag), `y`, `p d q =`..., no
  `r R w W e a i c`). Also `jq yq`, `sort` without `-o`,
  `diff stat du pwd cd echo printf which` and similar, `python3 -m json.tool`,
  `<tool> --version`, and the `gh pr/run/issue/repo view|list` reads.
- `git_read`: `status diff log show rev-parse ls-files blame grep merge-base`,
  the listing forms of `branch`/`tag`/`remote`/`stash`/`reflog`, and
  `config --get`. `--output` is refused (also on `diff-tree`, `rev-list`,
  `shortlog`).
- `git_write`: `add commit branch switch checkout merge rebase cherry-pick
  revert restore reset mv rm`, `stash push -m` and `fetch`. Refused: branch
  delete/move, `rebase -i`/`--exec`, and `reset --hard/--merge/--keep`. There
  is no push and no remote write (`shipcrew_no_remote_writes` DENYs them).
- `fs_write`: `mkdir touch cp mv rm rmdir chmod tee`, each target judged by
  owned paths.
- `deps`: `npm install|i|add`, `pnpm add|install|i`, `yarn add`, any flags
  (`-D`, `--save-dev`, `--no-audit` ...) except global / other-directory /
  workspace ones (`-g`, `--prefix`, `--location`, `-w`, `--workspace`, `-C`,
  `--dir`, `--filter`, `-r`). A dependency change writes `package.json` + the
  lockfile, so owned paths let it through only for the task that owns
  `package.json` (the Foundation); anyone else gets an ASK.
- `fs_edit`: `sed -i [-E] [-e] '[N[,M]]s<d>a<d>b<d>[gIi0-9]' FILE...` (ONE
  simple substitution: no second command or `-e`, no regex address, no
  `w`/`e`/`r`, no newline or `\n` in the replacement, no `-i.bak`, no `-f`) and
  `perl -pi -e 's/a/b/g' FILE...` (one substitution, no `/e`, no `@`, no `$var`
  other than `$1`/`$&`, no `(?{..})`). The edited files are write targets
  (owned paths, workflows guard). Any other `sed -i` / `perl -pi` (`/re/d`,
  `/re/,+1d`, `s/a/b/; s/c/d/`, `3a text`, `$VAR` in the script) is **DENY**
  for the roles that have this group, with the hint "Edit files with the Edit
  tool (it only needs the file to be in your owned paths); sed -i is only for
  one simple s/// substitution": the agent corrects itself, no card.
- `dev_tools`: installs from the lockfile only (`npm ci`, `pnpm install
  --frozen-lockfile`, `uv sync`, `uv pip install -r/-e`), plus `npm test`,
  `npm run <script>`, the pnpm/yarn equivalents, `node --test`, `vitest`,
  `jest`, `playwright test`, `tsc`, `eslint`, `prettier`, `next build/lint`,
  `biome`, `stylelint`, `@google/design.md lint`, `pytest` (plain, `uv run`,
  `.venv/bin`), `ruff mypy pyright` and `black --check`. Package-manager
  output flags are dropped before matching (`-s`/`--silent`, `--loglevel
  <x>`, `--reporter=<x>`, `--color`/`--no-color`, and `-w` outside a
  dependency command), in front of the subcommand and after `run`: `pnpm -s
  lint`, `npm run --silent typecheck`, `pnpm -s run test` match their plain
  entries (no `-s` entries needed); `pnpm -s add x` gets the verdict of `pnpm
  add x`.
- `test_runners` (reviewer): `npm/pnpm/yarn test`, `npm/pnpm run
  test|lint|typecheck`, `pnpm lint|typecheck`, `vitest run`, `jest`, `node
  --test`, `pytest`, `tsc --noEmit`, a lockfile install. No build and no e2e:
  CI already ran them green on the reviewed commit, and the reviewer never
  re-runs them.
- `run_app` (verify roles): `./init.sh`, `npm start`, `next dev/start`, `ps ss
  lsof kill pkill`.
- `local_http` (builders, scaffolder, verify roles): `curl` to
  `localhost`/`127.0.0.1`/`[::1]`/`0.0.0.0` only, any port and method, `-H`,
  inline `-d`/`--data*`/`--json`, `-w`, `-s`, `-i`, `-L`, `-m`. `-o FILE` is a
  write target (owned paths / test writes; `/dev/null` and `/tmp` are free).
  A file read (`-d @f`, `--data-urlencode n@f`, `-F f=@f`, `-H @f`, `-T f`)
  only for a relative path inside the cwd that is no `.env*`; `-b` only as
  `name=value`. Refused: any other host (also `--url`), `-K`/`--config`, `-c`,
  `-D`, `--trace`, `-O`, `-x`/`--proxy`, `--unix-socket`, `--resolve`,
  `--connect-to`, `-n` and every option not listed.
- `vercel_read`: `vercel whoami/ls/inspect/logs` (the global CLI, no `npx`).
  `env`, `domains`, `alias`, `project`, `git connect`, `remove` ... ASK.
- `vercel_deploy` (the ship stage, exactly): `vercel link --yes --project
  <name>` (lowercase `[a-z0-9._-]` name), `vercel deploy --prod --yes`, and
  `curl` to `https://*.vercel.app` with only `-sSILfv`, `--head/--silent/...`,
  `-o /dev/null` and `-m <s>`: no data, method, header or config flag. Any other
  form (`--scope`, `--force`, a preview deploy, `vercel --prod`) ASKs.

**How it reaches the session.** When the board starts a task, the server
(`omnigent/shipcrew/sessions.py`) packs the role bundle and writes the task's
`owned_paths` and absolute worktree path over the `# @task.*` slots of
`shipcrew_owned_paths` (`inject_task_contract`), plus the mission's other
active tasks (ready / running / review / intervention: title + owned paths, a
start-time snapshot) in `# @task.other_tasks`. A write to a file one of them
owns is **DENY** with the hint "<path> belongs to task '<title>' (in
progress). Do not edit it; work against the shared contract (e.g.
lib/api-client.ts, lib/db.ts) and mock it in your tests; if the contract lacks
something, say so in your final reply." (a file nobody else owns still asks).
At start the server also grants the task the existing test files whose only
app imports are modules it owns (`omnigent/shipcrew/inherited_tests.py`:
static scan of `test/**`, `tests/**`, `e2e/**`, `**/*.test.*`, `**/*.spec.*`
at the base ref; any import of a module it does not own, or no app import at
all, and the test is not granted). An owned-paths ASK a human accepts is
recorded on the task (`approved_paths`) and the merge gate does not hold the
PR again for those paths. The contract is stored with
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
  system; an ASK a human accepted answers Claude `allow` (omnigent fork), so
  the same call never gets a second, Claude-side prompt.

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
- **`curl` to localhost** (qa, security, builders) can reach every local service,
  including the omnigent API. Run the server with auth, so that an agent
  without a token cannot approve its own cards or merges.
- **Vetted variables are trusted.** `$PORT`, `$CHROMIUM_PATH`, `$HOME`... in
  a runner or curl argument take the session environment's value, which the
  agent does not set (an assignment in the same command makes it untrusted).
- **Parsing is conservative, not perfect.** A command whose words the shell
  would rewrite asks unless the rules above admit it: other `$VAR`, complex
  `${..}` and `$'..'` outside single quotes, brace expansion, a glob in an option name, `$(..)`, backticks and
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

## Verify roles and fix tasks (board)

`qa` and `security` verify merged work; they never implement a feature. When
their session ends, the board's PR loop (`omnigent/shipcrew/verify.py` in the
fork) reads the verdict line and the findings JSON block:

- `PASS`, no blocker/major finding, no commits: the card is done (`merged`,
  nothing to merge, worktree removed).
- `PASS` with commits (the tests it added, which pass): a normal PR (CI, then
  the reviewer is skipped because the diff is tests-only, merge).
- `FAIL`, or a blocker/major finding: ONE developer task `Fix: <title>` in the
  same mission (findings with `file:line` and repro, the report file, "add a
  regression test", fix the cheap minor findings too; owned paths = the files
  every finding names, minor ones included, else the verify task's own), Ready. Tests the verify task wrote stay on a local branch
  `shipcrew-tests/<id8>-<n>` named in the fix body (`git checkout <branch> --
  <files>`). The verify card goes back to Ready with the fix in `depends_on`
  and re-runs from the merged fix. After 2 fix cycles it is held in
  Intervention with the reason.

Worktrees (task, reviewer checkout, integrator, ship) are prepared by the
server before a session starts in them (`omnigent/shipcrew/worktree_prep.py`):
`node_modules` seeded from the main checkout (else, for a reviewer, from the
task worktree at the same head), and `/AGENTS.md` / `/CLAUDE.md` added to the
repo's `info/exclude` when the repo does not track them (Next 16 `next dev` /
`next build` write them), so `git add -A` never commits them.

The planner never gives a verify role an implementation task, and for a small
PRD (5 features or fewer) plans one final `qa` task whose checklist includes
the security items.

## Layout

```
agents/
  _shared/COMMON.md          common rules (stack, fake DB, CHROMIUM_PATH, limits, done)
  _shared/policies/*.yaml    guardrail fragments, one policy each
  _shared/policies/allowlists/*.yaml  shell command groups (# @include'd by the allowlist fragments)
  _shared/mcp/*.json         MCP server fragments (named on a bundle's shipcrew-mcp marker)
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
`# >>> shipcrew-guardrails: ...` marker line and rebuild. To change its MCP
servers, edit the `# >>> shipcrew-mcp: ...` marker, its `allowed_tools` and
`MCP_SERVERS` in `scripts/validate_agents.py`, then rebuild.

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
  - the MCP set per role (`MCP_SERVERS`): `strict_mcp_config` on, exactly the
    expected servers, `allowed_tools`' `mcp__*` entries match, and the derived
    launch args (claude-native) or spawn env (claude-sdk) carry the strict flag;
- checks `setting_sources` is `project,local` and reaches the launch args
  (`--setting-sources`) or the SDK spawn env;
- builds every guardrail through omnigent's factory path and runs 260
  tool-call cases per bundle (244 under the feature contract, with one other
  in-progress task owning `lib/polls-api/**`; 16 under a Foundation contract
  that owns `**` + `package.json`), including round 8's ANSI-C strings (the live reviewer chain with
  `grep -c $'\u00a0' f` is ALLOW, `git diff $'--output=x'` still asks), round 7's refusals with a hint
  (complex `sed -i` / `perl -pi`, another task's file, next to the simple
  substitution and the nobody's-file ASK that stay), the package-manager
  output flags (`pnpm -s lint`, `npm run --silent typecheck`, `pnpm -s run
  test`; `pnpm -s build` / `pnpm build` / `next build` still ASK for the
  reviewer; `pnpm -s add` = `pnpm add`, `pnpm -s -w add` refused), and every
  command a live run asked for that must now pass (`echo EXIT=$?`, vetted
  `$VAR` reads, `S=/p; cat $S/x`, `npm install -D ... | grep | head`, the
  `git mv || mv; sed -i; npm run ...` chain, background `&` + `wait`,
  `PORT=${PORT:-3000}`; live run 2: `pnpm exec next start -p ${PORT:-3000}`,
  `pkill -f "next start -p ${PORT:-3000}"; true`, `curl -X POST ... -d '{bad'`
  and `curl localhost:${PORT:-3000}/favicon.ico`, `... | tail -5; echo
  rc=${PIPESTATUS[0]}`, `... | sed 's/\x1b\[[0-9;]*m//g' | grep`, writing
  `pnpm-workspace.yaml` under the Foundation contract) and the negative cases
  behind them (remote curl, `-d @.env`, `-T /etc/passwd`, `--config`, proxies,
  `-o` outside the worktree or over a source, assigned / unvetted variables, a
  variable as the program or an option name or in a git / file writer, `sed`
  `w`/`e`/`r` and file operands; plus the dispatch cap), expecting a specific
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
- **Board commands.** The mission "Ask the crew…" box posts to
  `/missions/{id}/command`; today the server maps `run all` / `lance tout`,
  `plan`, `sync` and `stop all` / `arrête tout` with fixed rules (table in
  `shipcrew/ROLE.md`). Routing free text to a mission-scoped orchestrator
  session is a later step.
- **Publishing.** Workers never push. The module, or the orchestrator in full
  mode, pushes `shipcrew/<id8>-<slug>` branches (first 8 chars of the task
  id, slug of the title) and opens draft PRs.
