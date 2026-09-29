# Role: scaffolder (DevOps + lead dev, Foundation task)

You build the Foundation every other task stands on. Use the bundled
`design-lock` skill for the design tokens.

1. Package manager, decided once: a lockfile already in the repo decides
   (`pnpm-lock.yaml` -> pnpm, `package-lock.json` -> npm, `yarn.lock` -> yarn).
   No lockfile yet: **pnpm** (shared store, the fastest install for parallel
   worktrees). Set `"packageManager": "pnpm@<pnpm --version>"` in
   `package.json` and commit `pnpm-lock.yaml`. Never mix managers (no
   `npm install` in a pnpm repo).
2. Scaffold IN PLACE, in your worktree, non-interactively. Never generate in
   `/tmp` (or anywhere else) and copy back. `create-next-app` refuses this
   folder (`.github/`, `.shipcrew/`, `DESIGN.md` already exist), so set the app
   up directly: write `package.json` (name, `"private": true`,
   `packageManager`, the scripts of step 6), add the dependencies with ONE
   `pnpm add next react react-dom` and ONE `pnpm add -D typescript
   @types/node @types/react @types/react-dom tailwindcss @tailwindcss/postcss
   eslint eslint-config-next <the test toolchain of step 5>`, then write
   `tsconfig.json`, `next.config.ts`, `postcss.config.mjs`,
   `eslint.config.mjs`, `app/layout.tsx`, `app/globals.css`, `app/page.tsx`
   with the Write tool. Web: then `npx shadcn@latest init -d` in place (your
   session has the shadcn MCP tools). Mobile: the Expo equivalent, in place.
3. Wire the `DESIGN.md` tokens into Tailwind / NativeWind (design-lock skill).
4. Data: if `needs_db`, Neon Postgres + Drizzle schema, migration and seed
   (the environment already carries the credentials; do not read `.env*`).
   Otherwise create `lib/db.ts` with the `data_model` entities, per the common
   rules (faker seed 42, repository API). A stub page/screen for every route in
   the plan, the app shell and shared components, so parallel tasks only add
   inside their own folders. Add `app/icon.svg` (web) so no `/favicon.ico`
   404 reaches the console.
5. The WHOLE test toolchain every later task needs, installed now, so feature
   tasks never touch `package.json` or the lockfile: `vitest`,
   `@testing-library/react`, `@testing-library/user-event`,
   `@testing-library/jest-dom`, `jsdom`, `@vitejs/plugin-react`,
   `@playwright/test`, `@faker-js/faker` (plus whatever the plan's stack
   needs). `vitest.config.mts` (jsdom for component tests), one example unit
   test of `lib/db.ts` (the repository API and seed, no server), and a
   Playwright config (`executablePath: process.env.CHROMIUM_PATH`, `baseURL` and
   `webServer` on `process.env.PORT`, default 3000; `webServer` runs the
   production build + start when `CI` is set, dev otherwise) with one smoke
   spec that only checks the shell (the home page loads, the layout and
   navigation render, no console error). Never `playwright install`: the
   browser is preinstalled.
   Your tests cover the shell and the contract types only: never assert the
   behaviour of a stub route, page or API a later task owns (no
   `test/foundation-api.test.ts` on the stub responses, no e2e step on a stub
   screen). The task that owns the route writes its tests; a test pinning a
   stub makes that task edit your file.
6. CI: `.github/workflows/ci.yml` is already on `main` (the server installed
   it before your task started). Never write or edit it (that needs a human).
   It runs, each only when the script exists: `lint`, `typecheck`, `test`,
   `build`, `e2e`. Make `package.json` define exactly these (`"typecheck":
   "tsc --noEmit"`, `"test": "vitest run"`, `"e2e": "playwright test"`) and
   pass them all locally.
7. Write `init.sh` (install, migrate/seed if any, start the dev server on `$PORT`).

Done means every CI script (lint, typecheck, test, build, e2e) passes locally.
Commit on your branch. Final line: `PASS` or `FAIL: <reason>`.
