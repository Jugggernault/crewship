---
name: ship
description: Turn a project idea or PRD into a deployed demo URL with zero further human input. Interviews the user until the PRD is airtight, locks a visual style in DESIGN.md, then launches the autonomous shipcrew flow (plan → design → scaffold with gh/Vercel/Neon → build → QA → security → deploy). Use when the user says "ship this", "build this PRD", "make me a demo of", "/ship".
---

# Ship

You are the only agent that talks to the user. Everything after step 4 runs unattended,
so every ambiguity must die here. Speak the user's language.

## 1. Preflight

Run `shipcrew doctor`. If a REQUIRED tool is missing or not logged in, show the exact fix
command from its output (suggest `! <command>` for interactive logins like `gh auth login`)
and stop until it passes. OPTIONAL misses only degrade a phase; mention them in one line.

## 2. Grill the PRD

If the `grilling` skill is available, follow it. Otherwise: one question at a time, each
with your recommended answer, walking the decision tree until nothing is left to guess.

Cover at minimum — do not launch while any is unknown:
- target user and the single core job of the demo (what must work in a 3-minute demo)
- feature list in priority order, each with testable acceptance criteria
- data: entities, which need persistence (→ Neon Postgres), auth needed or not (default: no auth, seeded demo user)
- stack (default: Next.js App Router + TypeScript + Tailwind + shadcn/ui + Drizzle + Neon, on Vercel)
- out of scope (write it down; agents over-build otherwise)
- project folder name (default `~/Work/Projects/<slug>`) and repo visibility (default private)

Write the result to `<project>/.shipcrew/prd.md` with sections: Goal, Users, Features
(numbered, with acceptance criteria), Data model, Non-goals, Stack, Demo script.

## 3. Lock the style

Offer 3 distinct visual directions (name + mood + palette + type pairing, one line each;
draw on `design-taste-frontend` / `high-end-visual-design` if installed). Once the user
picks, write `<project>/DESIGN.md` following the Google DESIGN.md spec
(`npx -y @google/design.md spec` prints it): YAML front matter tokens (colors, typography,
rounded, spacing, components) + prose rationale and do/don't rules.
Run `npx -y @google/design.md lint <project>/DESIGN.md` and fix every error.
From here on the `design-lock` skill makes every build session conform to it.

## 4. Launch

Show a 5-line summary (features, stack, style, repo name, budget `SHIPCREW_MAX_USD`,
default 60) and ask for the single go. Then run, in the background:

```bash
shipcrew run <project>
```

Tell the user they can walk away; progress lands in `<project>/.shipcrew/progress.md`.

## 5. Deliver

When the background run finishes, read `<project>/.shipcrew/REPORT.md` and give the user:
the demo URL (or the local URL if deploy fell back), the repo URL, features passed /
blocked, security findings, and cost. If it crashed, run `shipcrew resume <project>` once
before reporting the failure.
