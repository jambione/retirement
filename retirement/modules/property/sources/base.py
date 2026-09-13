from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from typing import Any

from retirement.modules.property.models import Listing


class Source(ABC):
    """A place listings come from.

    Adding a second portal later means implementing `fetch` and nothing else;
    normalisation, scoring, storage and the digest are all source-agnostic.
    """

    name: str = "source"

    def __init__(self, conn: sqlite3.Connection, config: dict[str, Any]):
        self.conn = conn
        self.config = config

    def available(self) -> bool:
        return True

    @abstractmethod
    def fetch(self, area: dict[str, Any]) -> list[Listing]:
        """Return listings for one configured search area."""
