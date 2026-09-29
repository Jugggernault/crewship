---
name: plan
description: shipcrew step 1. Dispatch the planner on the PRD, check the resulting .shipcrew/plan.json (DAG, owned_paths, acceptance), and initialise .shipcrew/registry.json. Use at the start of every mission.
user-invocable: false
---

# plan — PRD to a task DAG

1. Locate the PRD: `.shipcrew/prd.md`, or the PRD text in your input (then write
   it to `.shipcrew/prd.md` yourself: it is prose).
2. Dispatch the planner in the SAME turn:
   `sys_session_send(agent="planner", title="plan-<mission-slug>",
   args={purpose: "plan", input: "<PRD path + mission title + any constraints>"})`,
   then end your turn; you are woken when it finishes.
3. Check `.shipcrew/plan.json` yourself (`sys_os_shell`, e.g. a short
   `python3 -c` script). Reject and re-dispatch the SAME planner title with
   the concrete problems if any of these fails:
   - valid JSON, every task has `key`, `title`, `acceptance` (2-6 items),
     `role`, `depends_on`, `owned_paths`;
   - `depends_on` only names existing keys and the graph has no cycle;
   - `T01` Foundation exists (role `scaffolder`) and every build task depends on it;
   - tasks that can run at the same time (neither depends on the other,
     transitively) have non-overlapping `owned_paths` (compare glob prefixes);
   - every PRD feature appears in the coverage matrix.
4. Initialise `.shipcrew/registry.json`:
   `{"tasks": {"<key>": {"status": "backlog", "id8": "<board task id[:8], or 8 new hex chars>", "branch": null, "worktree": null,
   "conversation_id": null, "attempts": 0, "pr_url": null, "reason": null}}}`.
   Status values mirror the board: `backlog | ready | running | review |
   intervention | merged | blocked`.
5. In `mode: plan-only`, stop here and reply with the task list and `PASS`.
