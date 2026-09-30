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
   with `pnpm dlx shadcn@latest add <component> --yes` (tasks add theirs). Mobile: the Expo equivalent.
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
8. Deployable from the first merge: the server builds your `Dockerfile` and
   publishes `main` on a public URL as soon as Foundation merges, then after
   every merge. So `pnpm build` passes and `/` renders the real shell (no
   blank or error page, no env var at build or start). Exactly two deploy
   files, nothing else (no compose file, no scripts):
   - `next.config.ts` sets `output: "standalone"`.
   - `Dockerfile`, verbatim (web, Next.js; an Expo repo ships its web export
     behind the same pattern, a plain Node app uses `node:22-alpine` with
     `npm start`):
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
   Runtime dependencies only in `dependencies` (toolchain, linters, types in
   `devDependencies`), so the traced server stays small. Never run `docker`:
   CI builds the image (and reports its size), the server deploys it.

Commit on your branch. Final line: `PASS` or `FAIL: <reason>`.
