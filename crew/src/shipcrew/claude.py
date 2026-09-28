"""Headless Claude Code: the hands of every agent. Also a CrewAI LLM so the whole
crew runs on the user's Claude Code login — no separate API key needed."""
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from crewai import BaseLLM

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
# ponytail: "auto" lets Claude Code's safety classifier gate risky commands without a human;
# SHIPCREW_PERMISSION_MODE=bypassPermissions only inside a throwaway VM/container.
MODE = os.environ.get("SHIPCREW_PERMISSION_MODE", "auto")
SESSION_BUDGET = os.environ.get("SHIPCREW_SESSION_USD", "8")
MAX_USD = float(os.environ.get("SHIPCREW_MAX_USD", "60"))
RETRIES = int(os.environ.get("SHIPCREW_RETRIES", "3"))
TIMEOUT = int(os.environ.get("SHIPCREW_SESSION_TIMEOUT", "3600"))


class BudgetExceeded(RuntimeError):
    pass


def spent(cwd: Path) -> float:
    log = cwd / ".shipcrew" / "log.jsonl"
    if not log.exists():
        return 0.0
    return sum(json.loads(l).get("cost", 0) for l in log.read_text().splitlines() if l.strip())


def claude(prompt: str, cwd: Path, role: str = "", tools: str | None = None, budget: str | None = None) -> str:
    """One session, retried on failure: flaky networks kill long sessions. Work is committed
    and logged in .shipcrew/progress.md, so a retry picks up where the dead one stopped."""
    for attempt in range(RETRIES + 1):
        try:
            return _claude(prompt, cwd, role, tools, budget)
        except BudgetExceeded:
            raise  # not transient
        except (RuntimeError, subprocess.TimeoutExpired):
            if attempt == RETRIES:
                raise
            time.sleep(60 * (attempt + 1))


def _claude(prompt: str, cwd: Path, role: str = "", tools: str | None = None, budget: str | None = None) -> str:
    cwd = Path(cwd)
    (cwd / ".shipcrew").mkdir(parents=True, exist_ok=True)
    if spent(cwd) >= MAX_USD:
        raise BudgetExceeded(f"spent >= SHIPCREW_MAX_USD={MAX_USD}")
    if os.environ.get("SHIPCREW_DRY"):
        return "{}"
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--permission-mode", MODE,
           "--plugin-dir", str(PLUGIN_ROOT), "--max-budget-usd", budget or SESSION_BUDGET]
    if role:
        cmd += ["--append-system-prompt", role]
    if tools is not None:
        cmd += ["--tools", tools]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude -p failed ({proc.returncode}): {proc.stderr[-2000:]}")
    with open(cwd / ".shipcrew" / "log.jsonl", "a") as f:
        f.write(json.dumps({"t": int(t0), "secs": int(time.time() - t0), "cost": data.get("total_cost_usd", 0),
                            "error": data.get("is_error", False), "prompt": prompt[:200]}) + "\n")
    if data.get("is_error"):
        raise RuntimeError(f"claude session error: {data.get('result', '')[:2000]}")
    return data.get("result", "")


class ClaudeCodeLLM(BaseLLM):
    """CrewAI reasoning via `claude -p` with tools disabled (pure text in, text out)."""

    # BaseLLM is a pydantic model in crewai 1.15: extra state must be a declared field,
    # and `model` must be passed explicitly (its validator runs before defaults).
    cwd: Path

    def call(self, messages: str | list[dict[str, Any]], *args, **kwargs) -> str:
        if isinstance(messages, str):
            prompt = messages
        else:
            prompt = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
        return claude(prompt, self.cwd, tools="", budget="2")

    def supports_function_calling(self) -> bool:
        return False

    def supports_stop_words(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 200_000
