# Role: scaffolder (DevOps + lead dev, Foundation task)

You build the Foundation every other task stands on, as light as the PRD
allows. Use the bundled `design-lock` skill for the design tokens.

1. Package manager, decided once: an existing lockfile decides
   (`pnpm-lock.yaml` / `package-lock.json` / `yarn.lock`); none yet: **pnpm**,
   with `"packageManager": "pnpm@<pnpm --version>"` and `pnpm-lock.yaml`
   committed. Never mix managers.
2. Scaffold IN PLACE, non-interactively, never in `/tmp` and copied back
   (`create-next-app` refuses this folder: `.github/`, `.shipcrew/`,
   `DESIGN.md` exist). Write `package.json` (name, `"private": true`,
   `packageManager`, the scripts of step 6), then ONE `pnpm add next react
   react-dom` and ONE `pnpm add -D typescript @types/node @types/react
   @types/react-dom tailwindcss @tailwindcss/postcss eslint eslint-config-next
   <the toolchain of step 5>`, then write `tsconfig.json`, `next.config.ts`,
   `postcss.config.mjs`, `eslint.config.mjs`, `app/layout.tsx`,
   `app/globals.css`, `app/page.tsx` with the Write tool. Web: `npx
   shadcn@latest init -d` in place, then only the components the shell uses
   (tasks add theirs). Mobile: the Expo equivalent.
3. Wire the `DESIGN.md` tokens into Tailwind / NativeWind (design-lock).
4. Data: `needs_db`: Neon Postgres + Drizzle schema, migration, seed (the
   environment has the credentials). Otherwise `lib/db.ts` with the
   `data_model` entities per the common rules. The app shell, shared
   components, `app/icon.svg`, and a stub for every route in the plan so
   parallel tasks only add inside their folders. **Stubs never error**: an API
   route stub answers 200 with a valid empty/default shape of the contract
   (`[]`, or the contract's empty object, typed like the real response), never
   501/500 or a throw, and a stub page renders an empty state, so pages built
   in parallel against it log no console error.
5. The minimal toolchain every later task needs, installed now (feature tasks
   never touch `package.json`): ONE unit runner (`vitest`, `jsdom`,
   `@vitejs/plugin-react`, `@testing-library/react`,
   `@testing-library/user-event`, `@testing-library/jest-dom`), ONE e2e runner
   (`@playwright/test`), `@faker-js/faker` for `lib/db.ts`, plus only what the
   plan's stack needs (each justified in `Decisions:`). `vitest.config.mts`
   (jsdom), one unit test of `lib/db.ts` (repository API and seed), and a
   Playwright config (`executablePath: process.env.CHROMIUM_PATH`, `baseURL`
   and `webServer` on `process.env.PORT`, default 3000; production build +
   start when `CI` is set, dev otherwise) with one smoke spec of the shell
   (home loads, layout and navigation render, no console error). Your tests
   cover the shell and the contract types only, never the behaviour of a stub
   a later task owns (no `test/foundation-api.test.ts` on stub responses, no
   e2e step on a stub screen): a test pinning a stub makes that task edit it.
6. CI: `.github/workflows/ci.yml` is already on `main`; never edit it. It runs
   `lint`, `typecheck`, `test`, `build`, `e2e` when defined: define exactly
   these (`"typecheck": "tsc --noEmit"`, `"test": "vitest run"`, `"e2e":
   "playwright test"`) and pass them all locally.
7. Write `init.sh` (install, migrate/seed if any, dev server on `$PORT`).

Commit on your branch. Final line: `PASS` or `FAIL: <reason>`.
