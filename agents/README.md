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
| `shipcrew` (orchestrator) | `claude-sdk`, `spawn: true`, `tools.agents` = the 9 roles | bundled: `plan`, `dispatch`, `verify` | pushes only `shipcrew/*` branches named explicitly, never `main`/`master`/`HEAD`; `gh pr merge` / `repo delete` / `release create` ASK; max 6 dispatches per turn; every dispatch declares a purpose (`plan`, `implement`, `review`, `verify`, `explore`, `search`) | `PASS` / `FAIL: <reason>` |
| `planner` (PM + architect) | `claude-sdk` | none | writes only `.shipcrew/plan.json` | `PASS` / `FAIL` |
| `designer` | `claude-native` | `shipcrew:design-lock`, `impeccable` | common set | `PASS` / `FAIL` |
| `scaffolder` | `claude-native` | `vercel:nextjs`, `vercel:shadcn`, `vercel:vercel-storage`, `shipcrew:design-lock` | common set | `PASS` / `FAIL` |
| `developer` | `claude-native` | `superpowers:test-driven-development`, `superpowers:verification-before-completion`, `shipcrew:design-lock`, `vercel:nextjs`, `vercel:shadcn` | common set | `PASS` / `FAIL` |
| `reviewer` | `claude-native`, a fresh session each round | `code-review`, `security-review`, `shipcrew:design-lock` | read-only (`read_only_os`: every write/edit refused) | `APPROVE` / `CHANGES: <summary>` |
| `integrator` | `claude-native` | bundled: `resolve-conflicts` | common set | `PASS` / `FAIL` |
| `qa` | `claude-native` | `shipcrew:design-lock`, `impeccable`, chrome-devtools MCP | writes only `.shipcrew/qa.json` | `PASS` / `FAIL: <n> failures` |
| `security` | `claude-native` | `security-review`, chrome-devtools MCP | common set | `PASS` / `FAIL` |
| `devops` | `claude-native` | `vercel:deploy`, `vercel:deployments-cicd` | writes only `.shipcrew/deploy.json` | `PASS` / `FAIL` |

Every bundle sets `permission_mode: auto` (headless: nobody answers permission
prompts). `superpowers:verification-before-completion` is required by the
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
| `shipcrew_no_env_read` | DENY reading `.env`, `.env.local`, `.env.*` through Read/Grep/`sys_os_read` or the shell (`.env.example`, `.sample`, `.template` allowed; `vercel env pull` allowed) |
| `shipcrew_workflows_approval` | ASK before any write to `.github/workflows/**` (an approval card in the Inbox; waits up to 24 h). Read-only shell passes. |
| `shipcrew_no_browser_download` | DENY `playwright install` (and `install-deps`, puppeteer browser downloads). Use `$CHROMIUM_PATH`. |

A denied call is final. The rules explain why, so the agent doesn't retry.

## Layout

```
agents/
  _shared/COMMON.md          common rules (stack, fake DB, CHROMIUM_PATH, limits, done)
  _shared/policies/*.yaml    guardrail fragments, one policy each
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
- checks shipcrew conventions: harness per role, `permission_mode: auto`, no
  pinned model, instructions actually read from `AGENTS.md`, bundled skills,
  and that the orchestrator's `tools.agents` equals the roles;
- builds every guardrail through omnigent's factory path and runs 46 tool-call
  cases per bundle (plus the dispatch cap), expecting a specific ALLOW, ASK or
  DENY for each.

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
  mode, pushes `shipcrew/<key>-<slug>` branches and opens draft PRs.
