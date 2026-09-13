"""Ask B — run a prompt against our own data with whichever AI is to hand.

Four backends, same shape in and out. The CLI ones use the subscription logins
already sitting on the mini (the same ones the trading desk uses), so a
question costs nothing extra; the HTTP one is the fallback when no CLI is
installed.

Two rules the CLIs make easy to get wrong:

  * They are agentic coding tools pointed at a working directory. Every one of
    them is invoked here with its file-editing and shell tools explicitly
    disallowed and its cwd set to a scratch directory, because "summarise this
    shortlist" must never turn into a commit.
  * A CLI restarted over ssh loses the login Keychain (see
    scripts/com.jambi.retirement-web.plist). If a backend reports itself
    logged out, that is the cause, and the answer is to restart the service
    through the LaunchAgent rather than to log in again.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from retirement.core.config import env, project_root

DEFAULT_TIMEOUT = 180.0


@dataclass
class Provider:
    id: str
    label: str
    detail: str
    run: Callable[[str, float], str]
    ready: Callable[[], bool]


def _workspace() -> str:
    """A scratch cwd, so a CLI that ignores its tool restrictions still has
    nothing of ours within reach."""
    path = project_root() / "var" / "ask"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _text_from(raw: str) -> str:
    """CLIs return either plain text or a JSON envelope; accept both."""
    raw = (raw or "").strip()
    if not raw.startswith("{") and not raw.startswith("["):
        return raw
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(payload, list):
        payload = payload[-1] if payload else {}
    if isinstance(payload, dict):
        for key in ("result", "text", "response", "output", "content", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):  # content blocks
                joined = "".join(
                    block.get("text", "") for block in value if isinstance(block, dict)
                ).strip()
                if joined:
                    return joined
    return raw


def _run(cmd: list[str], timeout: float, env_overrides: dict[str, str] | None = None) -> str:
    process_env = {**os.environ, **(env_overrides or {})}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=max(20.0, timeout), env=process_env, cwd=_workspace(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{cmd[0]} timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise RuntimeError(f"could not start {cmd[0]}: {exc}") from exc

    out, err = (proc.stdout or "").strip(), (proc.stderr or "").strip()
    if proc.returncode != 0 and not out:
        raise RuntimeError(f"{cmd[0]} exit {proc.returncode}: {(err or 'no output')[:300]}")
    text = _text_from(out)
    if not text:
        raise RuntimeError(f"{cmd[0]} returned nothing ({err[:200] or 'no stderr'})")
    return text


# ── backends ───────────────────────────────────────────────────────────────
def _claude_cli(prompt: str, timeout: float) -> str:
    binary = shutil.which(env("ASK_CLAUDE_BIN", "claude") or "claude")
    return _run(
        [binary, "-p", prompt,
         "--model", env("ASK_CLAUDE_MODEL", "sonnet") or "sonnet",
         "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit"],
        timeout,
    )


def _agy_cli(prompt: str, timeout: float) -> str:
    binary = shutil.which(env("ASK_AGY_BIN", "agy") or "agy")
    return _run(
        [binary, "-p", prompt,
         "--output-format", "json",
         "--disable-slash-commands",
         "--model", env("ASK_AGY_MODEL", "gemini-3.7-flash-high") or "gemini-3.7-flash-high"],
        timeout,
    )


def _grok_cli(prompt: str, timeout: float) -> str:
    binary = shutil.which(env("ASK_GROK_BIN", "grok") or "grok")
    handle, tmp = tempfile.mkstemp(prefix="ask_b_", suffix=".txt")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(prompt)
        return _run(
            [binary, "--prompt-file", tmp,
             "--output-format", "json",
             "--permission-mode", "bypassPermissions",
             "--disallowed-tools", "Agent,run_terminal_cmd,search_replace,write,Write,Edit,Bash",
             "-m", env("ASK_GROK_MODEL", "grok-4-fast") or "grok-4-fast"],
            timeout,
            # Subscription login only — never bill the console API by accident.
            env_overrides={"XAI_API_KEY": "", "GROK_API_KEY": ""},
        )
    finally:
        Path(tmp).unlink(missing_ok=True)


def _anthropic_api(prompt: str, timeout: float) -> str:
    from retirement.core import llm

    return llm._ask(
        "You are answering questions about a couple's property search and "
        "retirement plan, using only the data you are given.",
        prompt,
        max_tokens=2000,
    ).strip()


def _has(name_env: str, default: str) -> Callable[[], bool]:
    return lambda: bool(shutil.which(env(name_env, default) or default))


PROVIDERS: list[Provider] = [
    Provider("claude_cli", "Claude", "claude -p, subscription login",
             _claude_cli, _has("ASK_CLAUDE_BIN", "claude")),
    Provider("grok", "Grok", "grok CLI, SuperGrok login",
             _grok_cli, _has("ASK_GROK_BIN", "grok")),
    Provider("agy", "Antigravity", "agy -p, Gemini subscription",
             _agy_cli, _has("ASK_AGY_BIN", "agy")),
    Provider("anthropic_api", "Claude (API key)", "billed to ANTHROPIC_API_KEY",
             _anthropic_api, lambda: bool(env("ANTHROPIC_API_KEY"))),
]


def available() -> list[dict]:
    return [
        {"id": p.id, "label": p.label, "detail": p.detail, "ready": p.ready()}
        for p in PROVIDERS
    ]


def default_provider() -> str | None:
    preferred = env("ASK_DEFAULT_PROVIDER")
    if preferred and any(p.id == preferred and p.ready() for p in PROVIDERS):
        return preferred
    return next((p.id for p in PROVIDERS if p.ready()), None)


def ask(prompt: str, provider: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    chosen = provider or default_provider()
    if not chosen:
        raise RuntimeError(
            "No AI backend available. Install one of the claude, grok or agy CLIs "
            "on this machine, or set ANTHROPIC_API_KEY in .env."
        )
    match = next((p for p in PROVIDERS if p.id == chosen), None)
    if match is None:
        raise RuntimeError(f"unknown provider: {chosen}")
    if not match.ready():
        raise RuntimeError(f"{match.label} is not available on this machine ({match.detail}).")

    started = time.time()
    answer = match.run(prompt, timeout)
    return {
        "answer": answer,
        "provider": match.id,
        "provider_label": match.label,
        "seconds": round(time.time() - started, 1),
    }
