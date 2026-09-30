# shipcrew

PRD in, live demo URL out. Claude Code plugin + CrewAI Flow. Design rationale: [PROPOSAL.md](PROPOSAL.md).

## Install

```bash
./setup.sh            # skills/plugins/CLIs the roles use, then `shipcrew doctor`
```

Required: `claude`, `git`, `node`, `uv`, `gh` (logged in), `vercel` (logged in).
Also required: chrome-devtools MCP (e2e, already set up). Optional: Docker + `strix` (deep pentest, API-billed), `openpencil` (brand `.fig`).

Self-hosted instead of Vercel: one VPS runs the crew (omnigent server + host, the
board) and a k3s + ArgoCD cluster that keeps each project's preview (main and every
PR) in sync with its repo. `sudo bash deploy/vps/install.sh` in the omnigent fork,
then two one-time logins; see its `docs/shipcrew/VPS.md` (`SHIPCREW_DEPLOY_TARGET=argocd`).
The VPS installer clones this repo to `~shipcrew/shipcrew` and runs `setup.sh` itself.

## Use

In Claude Code: `/shipcrew:ship` then describe the project or paste a PRD. After the interview,
style choice and one "go", everything runs unattended. Result: `<project>/.shipcrew/REPORT.md`.

CLI: `shipcrew doctor | run <dir> | resume <dir>` (needs `<dir>/.shipcrew/prd.md`).

| env | default | |
|---|---|---|
| `SHIPCREW_MAX_USD` | 60 | global spend cap (sum of session costs) |
| `SHIPCREW_SESSION_USD` | 8 | per Claude Code session cap |
| `SHIPCREW_PERMISSION_MODE` | auto | `bypassPermissions` only in a throwaway VM |
| `STRIX_LLM`, `LLM_API_KEY` | — | needed by strix |

## Test

```bash
uv run --project crew python crew/tests/test_flow.py   # fake Claude, checks loop/QA/deploy/report
```
