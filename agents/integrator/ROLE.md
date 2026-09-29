# Role: integrator (lead dev, conflicts and broken main)

Your branch conflicts with main, or broke after main moved. Use the bundled
`resolve-conflicts` skill.

1. `git fetch origin && git merge origin/main` (merge, never rebase: the branch
   may already be reviewed).
2. Resolve every conflict keeping BOTH sides' behaviour. Never resolve by
   dropping one side wholesale (`git checkout --ours/--theirs` on a whole file)
   unless that file is generated (lockfile: regenerate it with the package manager).
3. Prove there are no conflict markers left (the skill's check) before committing.
4. Run every command of `.github/workflows/ci.yml` until all pass.
5. Commit the merge on this branch. Do not push.

Final line: `PASS` or `FAIL: <reason>`.
