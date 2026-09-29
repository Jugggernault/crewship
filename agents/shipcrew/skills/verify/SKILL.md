---
name: verify
description: shipcrew step 3. Verify a finished task — deterministic CI gates, then a fresh read-only reviewer on a saved diff snapshot, a fix loop with the same implementer, the integrator on conflicts, and finally qa, security and devops for the mission. Use whenever a worker reports PASS.
user-invocable: false
---

# verify — gates, fresh review, integrate

## 1. Deterministic gates (you run them)
In the task worktree run every command of `.github/workflows/ci.yml`
(`sys_os_shell`, `cwd` = the worktree). Red -> send the failing output back to
the SAME implementer conversation (same `agent` + `title`, purpose `implement`),
max 3 fix rounds, then `blocked`.

## 2. Fresh review (independence by fresh context)
1. Snapshot the diff OUTSIDE the worktree:
   `git -C .worktrees/<key> diff <base-sha>...<head-sha> > .shipcrew/diffs/<key>-r<n>.diff`
   and record both SHAs. Never paste the diff into a message.
2. Dispatch a NEW reviewer session every round:
   `sys_session_send(agent="reviewer", title="review-<key>-r<n>",
   args={purpose: "review", input: "Diff: <absolute snapshot path> (base <sha>,
   head <sha>). Contract: <title, acceptance, owned_paths>. Review only against
   the contract. Do not edit."})`
   Optionally pass a stronger `args.model` than the implementer's for a
   genuinely different second opinion.
3. `CHANGES` with blocking issues -> send the concrete fixes to the SAME
   implementer conversation, then back to step 1 with round n+1. After 3
   rounds, `intervention` (human) with the open issues.
4. `APPROVE` -> the task passes review.

## 3. Integrate
- Push and open a draft PR: `git -C .worktrees/<key> push -u origin <branch>`
  then `gh pr create --draft --fill --head <branch>`. Record `pr_url`. You never
  merge: the human or the board's PR loop does.
- Full mode without a PR loop: merge the branch into the local
  `shipcrew/integration` branch so dependants can start. On conflict, dispatch
  `integrator` (title `integrate-<key>`, purpose `implement`) in the task
  worktree, then re-run section 1.
- Remove the worktree only once its PR is open and review is clean.

## 4. Mission verification (after every build task passed)
In one wave: `qa` (purpose `verify`) and `security` (purpose `implement`) on
the integration head, each in its own worktree. qa failures become new
`developer` fix-tasks (back through dispatch). security fixes go through
section 1-3 like any task. Then `devops` (purpose `verify`) when the PRD asks
for a deployed URL. Reply with the task table (key, status, PR url), the
deploy URL if any, and `PASS` or `FAIL: <reason>`.
