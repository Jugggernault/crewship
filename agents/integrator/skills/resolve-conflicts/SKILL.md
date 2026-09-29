---
name: resolve-conflicts
description: Merge origin/main into a task branch and resolve conflicts keeping both sides' behaviour, then prove no conflict markers remain and CI is green before committing. Use whenever a shipcrew branch conflicts with main or broke after main moved.
---

# resolve-conflicts

## 1. Merge
```bash
git fetch origin
git merge --no-ff --no-edit origin/main || true
git diff --name-only --diff-filter=U        # the conflicted files
```

## 2. Resolve, file by file
- Read both sides and the merge base (`git show :1:<f>`, `:2:<f>` ours, `:3:<f>` theirs).
- Keep BOTH behaviours: two routes added to the same nav -> keep both entries;
  two new fields on the same entity -> keep both; two exports -> keep both.
- When the sides truly contradict, main wins on shared contracts (`lib/db.ts`
  API, API route shapes, shared components) and the branch adapts its own code
  to the new contract.
- Lockfiles and generated files: never hand-merge. Take main's version, then
  regenerate (`git checkout --theirs package-lock.json && npm install --no-audit --no-fund`).
- `git add <f>` only after the file is resolved.

## 3. Verify (all must hold before committing)
```bash
# no conflict markers anywhere in tracked files
! git grep -nE '^(<{7}|={7}|>{7})( |$)' -- . ':!*.md'
git diff --name-only --diff-filter=U | wc -l   # must print 0
```
Then run every command of `.github/workflows/ci.yml` locally until all pass.

## 4. Commit
`git commit --no-edit` (keeps the merge commit message). Do not push.
Report every file you resolved and how (which behaviours were kept).
