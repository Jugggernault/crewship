"""Headless Claude Code: the hands of every agent. Also a CrewAI LLM so the whole
crew runs on the user's Claude Code login — no separate API key needed."""
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from crewai import BaseLLM

from .tools import session_env

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
# ponytail: "auto" lets Claude Code's safety classifier gate risky commands without a human;
# SHIPCREW_PERMISSION_MODE=bypassPermissions only inside a throwaway VM/container.
MODE = os.environ.get("SHIPCREW_PERMISSION_MODE", "auto")
SESSION_BUDGET = os.environ.get("SHIPCREW_SESSION_USD", "8")
MAX_USD = float(os.environ.get("SHIPCREW_MAX_USD", "60"))
RETRIES = int(os.environ.get("SHIPCREW_RETRIES", "3"))
TIMEOUT = int(os.environ.get("SHIPCREW_SESSION_TIMEOUT", "3600"))
IDLE = int(float(os.environ.get("SHIPCREW_IDLE_MIN", "10")) * 60)

_lock = threading.Lock()  # sessions run in parallel threads: serialise log/status writes


class BudgetExceeded(RuntimeError):
    pass


class Stalled(RuntimeError):
    """No stream event for IDLE seconds: the session is stuck on one command."""


def spent(root: Path) -> float:
    log = Path(root) / ".shipcrew" / "log.jsonl"
    if not log.exists():
        return 0.0
    return sum(json.loads(l).get("cost", 0) for l in log.read_text().splitlines() if l.strip())


def update_status(root: Path, fn):
    """Read-modify-write .shipcrew/status.json under the lock (read by `shipcrew status`)."""
    p = Path(root) / ".shipcrew" / "status.json"
    with _lock:
        data = json.loads(p.read_text()) if p.exists() else {}
        fn(data)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2))


def claude(prompt: str, cwd: Path, role: str = "", tools: str | None = None, budget: str | None = None,
           root: Path | None = None, label: str = "") -> str:
    """One session, retried on failure: flaky networks kill long sessions. Work is committed
    and logged, so a retry picks up where the dead one stopped. `root` = project dir holding
    .shipcrew/ (differs from cwd when the session runs in a worktree)."""
    note = ""
    for attempt in range(RETRIES + 1):
        try:
            return _claude(prompt + note, cwd, role, tools, budget, root or cwd, label)
        except BudgetExceeded:
            raise  # not transient
        except Stalled as e:
            if attempt == RETRIES:
                raise
            note = (f"\n\nNOTE: a previous attempt was killed after {IDLE // 60} min without progress while "
                    f"running: {e}. Do not repeat that; find another way (and never run `playwright install`).")
        except (RuntimeError, subprocess.TimeoutExpired):
            if attempt == RETRIES:
                raise
            time.sleep(60 * (attempt + 1))


def _doing(event: dict) -> str:
    """What an assistant event is doing (its last tool call), for status and stall notes."""
    for c in reversed(event.get("message", {}).get("content", []) or []):
        if c.get("type") == "tool_use":
            inp = c.get("input", {})
            return f"{c.get('name')}: {inp.get('command') or inp.get('file_path') or inp.get('description') or ''}"[:200]
    return ""


def _claude(prompt: str, cwd: Path, role: str, tools: str | None, budget: str | None, root: Path, label: str) -> str:
    cwd, root = Path(cwd), Path(root)
    (root / ".shipcrew").mkdir(parents=True, exist_ok=True)
    if spent(root) >= MAX_USD:
        raise BudgetExceeded(f"spent >= SHIPCREW_MAX_USD={MAX_USD}")
    if os.environ.get("SHIPCREW_DRY"):
        return "{}"
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", MODE,
           "--plugin-dir", str(PLUGIN_ROOT), "--max-budget-usd", budget or SESSION_BUDGET]
    if role:
        cmd += ["--append-system-prompt", role]
    if tools is not None:
        cmd += ["--tools", tools]
    sid, t0 = uuid.uuid4().hex[:8], time.time()
    label = label or prompt.split("\n")[0][:40]
    sess_log = root / ".shipcrew" / "sessions" / f"{sid}.jsonl"
    sess_log.parent.mkdir(parents=True, exist_ok=True)
    state = {"last": time.time(), "doing": "starting", "result": None}

    def touch(doing: str = ""):
        state["last"] = time.time()
        if doing:
            state["doing"] = doing
        update_status(root, lambda d: d.setdefault("sessions", {}).__setitem__(
            sid, {"label": label, "cwd": str(cwd), "doing": state["doing"], "last": state["last"]}))

    err = sess_log.with_suffix(".err")  # a file, not a pipe: an unread stderr pipe can fill and deadlock
    with open(err, "w") as ef:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=ef, text=True,
                                env=session_env(), start_new_session=True)

    def reader():
        with open(sess_log, "a") as log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "result":
                    state["result"] = ev
                touch(_doing(ev) if ev.get("type") == "assistant" else "")

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    touch()
    try:
        while proc.poll() is None:
            time.sleep(5)
            if time.time() - t0 > TIMEOUT:
                _kill(proc)
                raise subprocess.TimeoutExpired(cmd, TIMEOUT)
            # ponytail: idle = no stream event; one legit command longer than IDLE_MIN gets killed too
            if time.time() - state["last"] > IDLE:
                _kill(proc)
                raise Stalled(state["doing"])
        th.join(timeout=10)
    finally:
        update_status(root, lambda d: d.get("sessions", {}).pop(sid, None))
    data = state["result"]
    if not data:
        raise RuntimeError(f"claude -p failed ({proc.returncode}): {err.read_text()[-2000:]}")
    with _lock, open(root / ".shipcrew" / "log.jsonl", "a") as f:
        f.write(json.dumps({"t": int(t0), "secs": int(time.time() - t0), "cost": data.get("total_cost_usd", 0),
                            "error": data.get("is_error", False), "label": label, "session": sid,
                            "prompt": prompt[:200]}) + "\n")
    if data.get("is_error"):
        raise RuntimeError(f"claude session error: {data.get('result', '')[:2000]}")
    return data.get("result", "")


def _kill(proc: subprocess.Popen):
    """Kill the whole process group: the stuck command is a grandchild (bash -> npx ...)."""
    try:
        os.killpg(proc.pid, 9)
    except OSError:
        proc.kill()


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
        return claude(prompt, self.cwd, tools="", budget="2", label="planner")

    def supports_function_calling(self) -> bool:
        return False

    def supports_stop_words(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 200_000
