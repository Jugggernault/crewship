---
name: dispatch
description: shipcrew step 2. Start ready tasks in waves through the four scheduler gates (dependencies merged, capacity, owned paths disjoint, budget), one git worktree and one role sub-agent per task. Use after plan, and again every time a task finishes.
user-invocable: false
---

# dispatch — waves through four gates

A task is **ready** when it is in `backlog`/`ready` and passes all four gates:
1. **deps**: every `depends_on` task is `merged` (or, in full mode without a PR
   loop, verified and integrated into the mission integration branch, see below).
2. **capacity**: at most 6 running workers (the policy caps dispatches per turn
   at 6 too). Fewer when the machine is loaded.
3. **owned paths**: its `owned_paths` do not overlap any running task's.
4. **budget**: the mission's cost so far leaves room (stop dispatching and tell
   the human when a budget in your input is reached).

## Per ready task (emit all tool calls in this turn)
1. Worktree + branch, from the current integration head:
   `git worktree add .worktrees/<key> -b shipcrew/<key>-<slug> <base>`
   where `<base>` is `origin/main` (or the local integration branch
   `shipcrew/integration` in full mode). Record branch + worktree in the registry.
2. Dispatch the role agent named by the task's `role`:
   `sys_session_send(agent="<role>", title="<key>-<slug>",
   args={purpose: "implement", input: "<task contract>"})`
   The task contract is: worktree absolute path (the worker works ONLY there),
   title, body, acceptance criteria, owned_paths, the keys already merged, and
   the verdict line to end with. For long `developer` / `scaffolder` tasks the
   input may be one `/goal <condition>` command containing that same contract.
3. Set the task `running`, store `conversation_id`, increment `attempts`.

Then END YOUR TURN. Do not poll; you are woken per finished worker.

## On a finished worker
- Read its result (`sys_read_inbox`). Verdict `PASS` -> task to `review` and
  run the `verify` skill. `FAIL: <reason>` -> one fresh re-dispatch in a clean
  worktree if the reason is fixable, else `blocked` with the reason.
- Then re-evaluate the gates: newly unblocked tasks are dispatched in the same turn.
- Append one line per event to `.shipcrew/progress.md`
  (`<time> <key> <status> <note>`).
