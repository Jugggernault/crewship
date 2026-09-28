#!/usr/bin/env bash
# One-time install of the skills/plugins/CLIs the crew's roles use. Safe to re-run.
# Required CLIs (gh, vercel) are checked by `shipcrew doctor`, not installed here (need sudo/login).
set -u
here="$(cd "$(dirname "$0")" && pwd)"
try() { echo "+ $*"; "$@" || echo "  (failed, continuing)"; }

# Interview: grill-me + the grilling primitive it calls (mattpocock/skills)
try npx -y skillfish add mattpocock/skills grilling -y --global --agent "Claude Code"
try npx -y skillfish add mattpocock/skills grill-me -y --global --agent "Claude Code"
# CrewAI authoring skill
try npx -y skillfish add claudiodearaujo/izacenter crewai -y --global --agent "Claude Code"

# Process discipline: TDD, verification-before-completion, code review (obra/superpowers)
try claude plugin marketplace add obra/superpowers-marketplace
try claude plugin install superpowers@superpowers-marketplace

# Real-browser verification for developer/QA/security roles: Chromium via chrome-devtools MCP
claude mcp get chrome-devtools >/dev/null 2>&1 || try claude mcp add --scope user chrome-devtools -- npx -y chrome-devtools-mcp@latest

# Brand board (.fig) via OpenPencil CLI + MCP
try npm i -g @open-pencil/cli
claude mcp get open-pencil >/dev/null 2>&1 || try claude mcp add --scope user open-pencil -- openpencil-mcp

# Optional deep pentest (Docker + STRIX_LLM + LLM_API_KEY = API billing, not your Claude login)
command -v strix >/dev/null || echo "strix: review then run: curl -sSL https://strix.ai/install | bash"

# This plugin
try claude plugin marketplace add "$here"
try claude plugin install shipcrew@shipcrew

uv sync --project "$here/crew" && "$here/bin/shipcrew" doctor
