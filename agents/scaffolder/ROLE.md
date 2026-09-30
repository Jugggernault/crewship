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
8. Deployable from the first merge: the server builds your `Dockerfile` and
   publishes the app on a public URL as soon as Foundation merges, then after
   every merge. So `pnpm build` passes and `/` renders the real app shell (no
   blank or error page, no env var required at build or start). Keep it light:
   exactly two deploy files, nothing else (no compose file, no scripts):
   - `next.config.ts` sets `output: "standalone"`.
   - `Dockerfile`, verbatim (web, Next.js; a mobile/Expo repo ships the Expo
     web export behind the same pattern, a plain Node app uses `node:22-alpine`
     with `npm start`):
     ```dockerfile
     # Production image: Next.js standalone on bare Alpine + the node binary
     # (no npm, no dev dependencies, no source maps, non-root). Budget < 200 MB.
     FROM node:22-alpine AS build
     WORKDIR /app
     ENV CI=1 NEXT_TELEMETRY_DISABLED=1
     COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml* package-lock.json* yarn.lock* .npmrc* ./
     RUN corepack enable && \
         if [ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile; \
         elif [ -f yarn.lock ]; then yarn install --frozen-lockfile; \
         elif [ -f package-lock.json ]; then npm ci --no-audit --no-fund; \
         else npm install --no-audit --no-fund; fi
     COPY . .
     RUN npm run build && mkdir -p public && find .next public -name '*.map' -delete

     FROM alpine:3.22
     RUN apk add --no-cache libstdc++ && adduser -D -H -u 10001 app
     COPY --from=build /usr/local/bin/node /usr/local/bin/node
     WORKDIR /app
     ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
     COPY --from=build --chown=app /app/.next/standalone ./
     COPY --from=build --chown=app /app/.next/static ./.next/static
     COPY --from=build --chown=app /app/public ./public
     USER app
     EXPOSE 3000
     HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
       CMD node -e "fetch('http://127.0.0.1:'+process.env.PORT+'/').then(r=>process.exit(r.status<500?0:1),()=>process.exit(1))"
     CMD ["node", "server.js"]
     ```
   - `.dockerignore`: `.git`, `.github`, `.shipcrew`, `node_modules`, `.next`,
     `.vercel`, `.env*` (with `!.env.example`), `coverage`,
     `playwright-report`, `test-results`, `*.tsbuildinfo`, `Dockerfile`,
     `.dockerignore`.
   Runtime dependencies only in `dependencies` (the test toolchain, linters,
   types and build-only tools in `devDependencies`), so the traced server stays
   small. Do not run `docker` yourself: CI builds the image (and reports its
   size and the first-load JS), the server deploys it.

Done means every CI script (lint, typecheck, test, build, e2e) passes locally.
Commit on your branch. Final line: `PASS` or `FAIL: <reason>`.
