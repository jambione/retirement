"""Configuration loading: YAML for criteria, .env for secrets."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def project_root() -> Path:
    """Repo root, regardless of where the process was launched from."""
    return Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env reader. Does not overwrite variables already in the
    environment, so launchd or the shell can override the file."""
    path = path or project_root() / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_absolute():
        p = project_root() / p
    if not p.exists():
        raise FileNotFoundError(f"config file not found: {p}")
    with p.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def dump_yaml(path: str | Path, data: dict[str, Any]) -> None:
    p = Path(path)
    if not p.is_absolute():
        p = project_root() / p
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


def env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key, default)
    return value if value not in ("", None) else default


def env_int(key: str, default: int) -> int:
    raw = env(key)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def db_path() -> Path:
    raw = env("RETIREMENT_DB", "var/retirement.sqlite3") or "var/retirement.sqlite3"
    p = Path(raw)
    if not p.is_absolute():
        p = project_root() / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class Profile:
    """The household profile plus the set of enabled modules."""

    def __init__(self, data: dict[str, Any]):
        self.data = data

    @classmethod
    def load(cls, path: str | Path = "config/profile.yaml") -> "Profile":
        return cls(load_yaml(path))

    @property
    def household(self) -> dict[str, Any]:
        return self.data.get("household", {})

    def enabled_modules(self) -> dict[str, dict[str, Any]]:
        return {
            name: spec
            for name, spec in (self.data.get("modules") or {}).items()
            if isinstance(spec, dict) and spec.get("enabled")
        }
