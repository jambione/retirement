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

import glob
import json
import os
import shutil
import subprocess
import tempfile
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from retirement.core.config import env, project_root

log = logging.getLogger("retirement.ask")

DEFAULT_TIMEOUT = 180.0

# These CLIs are installed by npm, bun or Homebrew, and an ssh or launchd shell
# sees almost none of that. PATH alone reported every backend as "missing" on a
# machine where all of them work from a Terminal, so look where they actually
# live as well.
CLI_DIRS = (
    "/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin",
    "~/.local/bin", "~/bin", "~/.bun/bin", "~/.deno/bin",
    "~/.npm-global/bin", "~/.volta/bin", "~/.yarn/bin",
    "~/Library/pnpm", "~/.cargo/bin",
)
CLI_GLOBS = (
    "~/.nvm/versions/node/*/bin",      # nvm is the usual home for these
    "~/.local/share/fnm/node-versions/*/installation/bin",
)


def resolve_binary(name: str) -> str | None:
    """Absolute path to a CLI, however it was installed.

    An absolute name is taken as given -- that is how a pinned ASK_*_BIN wins,
    and the only reliable answer on a machine whose PATH depends on which shell
    asked.
    """
    if "/" in name:
        path = Path(name).expanduser()
        return str(path) if os.access(path, os.X_OK) else None

    found = shutil.which(name)
    if found:
        return found

    for directory in CLI_DIRS:
        candidate = Path(directory).expanduser() / name
        if os.access(candidate, os.X_OK):
            return str(candidate)

    for pattern in CLI_GLOBS:
        base = Path(pattern).expanduser()
        for directory in sorted(glob.glob(str(base)), reverse=True):   # newest first
            candidate = Path(directory) / name
            if os.access(candidate, os.X_OK):
                return str(candidate)
    return None


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


def describe(cmd: list[str]) -> str:
    """The command as run, with the prompt elided — prompts are long and the
    argv is the part that explains a flag error."""
    shown = []
    skip_next = False
    for i, part in enumerate(cmd):
        if skip_next:
            shown.append("<prompt>")
            skip_next = False
            continue
        if part == "-p":
            shown.append(part)
            skip_next = True
            continue
        shown.append(part)
    return " ".join(shown)


def _run(cmd: list[str], timeout: float, env_overrides: dict[str, str] | None = None) -> str:
    process_env = {**os.environ, **(env_overrides or {})}
    log.debug("running %s", describe(cmd))
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
        raise RuntimeError(
            f"exit {proc.returncode} from: {describe(cmd)}\n"
            f"{(err or 'no output on stderr')[:400]}"
        )
    # agy needs its envelope intact (see _unwrap_agy); everything else is
    # happy with the generic extraction.
    text = out if "agy" in os.path.basename(cmd[0]) else _text_from(out)
    if not text:
        raise RuntimeError(
            f"no output from: {describe(cmd)}\n{(err or 'no stderr either')[:400]}"
        )
    return text


# ── backends ───────────────────────────────────────────────────────────────
def _claude_cli(prompt: str, timeout: float) -> str:
    binary = resolve_binary(env("ASK_CLAUDE_BIN", "claude") or "claude")
    return _run(
        [binary, "-p", prompt,
         "--model", env("ASK_CLAUDE_MODEL", "sonnet") or "sonnet",
         "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit"],
        timeout,
    )


# Markers the Antigravity CLI uses when its subscription login has lapsed.
# Worth detecting by hand: a logged-out agy returns a well-formed JSON envelope
# with an error inside it, so the generic extractor happily reports "returned
# nothing" for what is actually a five-second fix.
AGY_LOGGED_OUT = (
    "authentication required", "authentication failed",
    "not logged into antigravity", "please sign in",
    "run 'agy' to log in", "run agy to log in",
    "launch the cli without arguments to sign in",
)


def _agy_cli(prompt: str, timeout: float) -> str:
    binary = resolve_binary(env("ASK_AGY_BIN", "agy") or "agy")
    cmd = [
        binary, "-p", prompt,
        "--output-format", "json",
        # Without this agy applies its own, shorter print timeout and returns a
        # truncated answer on a long assessment.
        "--print-timeout", f"{int(max(30.0, timeout))}s",
        "--disable-slash-commands",
        "--model", env("ASK_AGY_MODEL", "gemini-3.7-flash-high") or "gemini-3.7-flash-high",
    ]
    # Only when asked for. trading-helper passes --effort conditionally, and a
    # flag this build does not recognise fails the whole call.
    effort = env("ASK_AGY_EFFORT")
    if effort:
        cmd += ["--effort", effort]
    return _unwrap_agy(_run(cmd, timeout))


def _unwrap_agy(raw: str) -> str:
    """agy answers in an envelope: {status, response, usage}. A non-SUCCESS
    status carries the real reason, and reporting it beats reporting silence."""
    try:
        envelope = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    if not isinstance(envelope, dict) or "response" not in envelope and "status" not in envelope:
        return raw

    status = str(envelope.get("status") or "").strip().upper()
    if status and status != "SUCCESS":
        detail = str(envelope.get("error") or status)
        if any(marker in detail.lower() for marker in AGY_LOGGED_OUT):
            raise RuntimeError(
                "Antigravity CLI is not logged in. Run `agy` with no arguments "
                "from a Terminal ON the mini (an ssh session cannot reach the "
                "login Keychain), then try again."
            )
        raise RuntimeError(f"Antigravity CLI {status}: {detail[:240]}")

    text = str(envelope.get("response") or envelope.get("result") or "").strip()
    return text or raw


def _grok_cli(prompt: str, timeout: float) -> str:
    binary = resolve_binary(env("ASK_GROK_BIN", "grok") or "grok")
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
    """The paid fallback, last in preference. Calls the SDK directly -- routing
    it through llm would recurse, since llm now calls back into this module."""
    import anthropic

    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model=env("RETIREMENT_LLM_MODEL", "claude-sonnet-4-5") or "claude-sonnet-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in message.content if b.type == "text").strip()


def _has(name_env: str, default: str) -> Callable[[], bool]:
    return lambda: bool(resolve_binary(env(name_env, default) or default))


def where(name_env: str, default: str) -> str | None:
    """Where a backend's binary was found — for doctor, so 'missing' can be
    told apart from 'installed somewhere this shell cannot see'."""
    return resolve_binary(env(name_env, default) or default)


# Order is preference when ASK_DEFAULT_PROVIDER is unset. Antigravity first:
# it is the subscription this project is meant to spend, and leaving claude
# first meant the mini silently used the trading desk's login instead.
PROVIDERS: list[Provider] = [
    Provider("agy", "Antigravity", "agy -p, Gemini subscription",
             _agy_cli, _has("ASK_AGY_BIN", "agy")),
    Provider("claude_cli", "Claude", "claude -p, subscription login",
             _claude_cli, _has("ASK_CLAUDE_BIN", "claude")),
    Provider("grok", "Grok", "grok CLI, SuperGrok login",
             _grok_cli, _has("ASK_GROK_BIN", "grok")),
    Provider("anthropic_api", "Claude (API key)", "billed to ANTHROPIC_API_KEY",
             _anthropic_api, lambda: bool(env("ANTHROPIC_API_KEY"))),
]


BINARIES = {"agy": ("ASK_AGY_BIN", "agy"), "claude_cli": ("ASK_CLAUDE_BIN", "claude"),
            "grok": ("ASK_GROK_BIN", "grok")}


def available() -> list[dict]:
    out = []
    for provider in PROVIDERS:
        entry = {"id": provider.id, "label": provider.label,
                 "detail": provider.detail, "ready": provider.ready()}
        if provider.id in BINARIES:
            entry["path"] = where(*BINARIES[provider.id])
        out.append(entry)
    return out


def default_provider() -> str | None:
    preferred = env("ASK_DEFAULT_PROVIDER")
    if preferred and any(p.id == preferred and p.ready() for p in PROVIDERS):
        return preferred
    return next((p.id for p in PROVIDERS if p.ready()), None)


def preference_note() -> str | None:
    """A sentence when the configured backend is NOT the one in use, so a
    silent fallback to a different subscription is visible."""
    preferred = env("ASK_DEFAULT_PROVIDER")
    actual = default_provider()
    if not preferred or preferred == actual:
        return None
    known = {p.id for p in PROVIDERS}
    if preferred not in known:
        return f"ASK_DEFAULT_PROVIDER={preferred} is not a known backend; using {actual}."
    return (
        f"ASK_DEFAULT_PROVIDER={preferred} is set but that CLI is not installed "
        f"here — falling back to {actual or 'nothing'}."
    )


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
