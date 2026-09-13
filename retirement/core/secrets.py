"""Credential lookup, in the same shape trading-helper uses.

config/secrets.json first, then the environment, so a value can be set in
either place and the file wins. Keys are the lowercase names used in
trading-helper's secrets.json (smtp_host, smtp_user, ...) and the environment
equivalents are those names uppercased (SMTP_HOST, SMTP_USER, ...), which is
also what .env already carries.

You CAN point this at the trading desk's file instead of keeping a second one:

    RETIREMENT_SECRETS=/Users/jambimac/repo/trading-helper/config/secrets.json

One file means one place to rotate a password. It also means this app can read
every secret the trading desk holds, which is the reason the default is a
separate file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from retirement.core.config import project_root


def secrets_path() -> Path:
    override = os.environ.get("RETIREMENT_SECRETS")
    if override:
        return Path(override).expanduser()
    return project_root() / "config" / "secrets.json"


def _load() -> dict[str, Any]:
    path = secrets_path()
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def get(key: str, default: str = "") -> str:
    """secrets.json wins, then the environment, then the default."""
    value = _load().get(key)
    if value in (None, ""):
        value = os.environ.get(key.upper(), default)
    return str(value) if value not in (None, "") else default


def get_int(key: str, default: int) -> int:
    try:
        return int(get(key) or default)
    except ValueError:
        return default


def has(*keys: str) -> bool:
    return all(get(key) for key in keys)
