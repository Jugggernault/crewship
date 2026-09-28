---
name: design-lock
description: Enforce a project's DESIGN.md on every UI change. Use whenever writing or reviewing frontend code in a repo that has a DESIGN.md at its root — tokens only, no ad-hoc colors, fonts, radii or spacing.
---

# Design lock

`DESIGN.md` at the repo root is law (Google DESIGN.md format: YAML tokens + prose rules).

## Before touching UI
1. Read `DESIGN.md` fully, prose included — the do/don't rules matter as much as tokens.
2. If `app/tokens.css` (or the project's token file) is missing or older than DESIGN.md,
   regenerate it: `npx -y @google/design.md export --format css-tailwind DESIGN.md > app/tokens.css`
   and import it from the global stylesheet.

## While coding
- Colors, fonts, radii, spacing, shadows: only via the generated tokens / Tailwind theme.
  No hex, rgb, arbitrary `[...]` Tailwind values or inline styles for these.
- Components listed under `components:` must use their declared tokens.
- Missing token? Add it to DESIGN.md first (with rationale), re-lint, re-export. Never inline it.

## Before calling the work done
- `npx -y @google/design.md lint DESIGN.md` exits 0.
- `grep -rnE '#[0-9a-fA-F]{3,8}\b|rgb\(|\[[0-9.]+(px|rem)\]' app components src 2>/dev/null`
  returns nothing outside the token file.
- If the `impeccable` skill is installed, run its audit on changed screens and fix
  anything flagged as off-system.
