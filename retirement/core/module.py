"""Every workstream in this project is a Module.

The property search is the first one. A second module (finance modelling,
residency pathways, healthcare comparison) only has to implement `run` and
register itself in REGISTRY to inherit the CLI, the database, the scheduler and
the email digest.
"""
from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from typing import Any, Callable


class Module(ABC):
    name: str = "module"

    def __init__(self, config: dict[str, Any], conn: sqlite3.Connection):
        self.config = config
        self.conn = conn

    @abstractmethod
    def migrate(self) -> None:
        """Create whatever tables this module needs. Must be idempotent."""

    @abstractmethod
    def run(self, dry_run: bool = False) -> dict[str, Any]:
        """Do one cycle of work. Returns a summary dict for the CLI/log."""


REGISTRY: dict[str, Callable[..., Module]] = {}


def register(name: str) -> Callable[[type[Module]], type[Module]]:
    def wrap(cls: type[Module]) -> type[Module]:
        cls.name = name
        REGISTRY[name] = cls
        return cls
    return wrap


def load_registry() -> None:
    """Import modules so their @register decorators fire."""
    from retirement.modules.property import pipeline  # noqa: F401
