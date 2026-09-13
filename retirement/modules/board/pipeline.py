"""The board as a Module: it has no feed to poll, so `run` just recomputes the
summary and reports. It exists so the board joins the same CLI, database and
scheduler as everything else rather than being a special case."""
from __future__ import annotations

from typing import Any

from retirement.core.module import Module, register
from retirement.modules.board import store, summary


@register("board")
class BoardModule(Module):
    def migrate(self) -> None:
        store.migrate(self.conn)

    def run(self, dry_run: bool = False) -> dict[str, Any]:
        self.migrate()
        data = summary.build(self.conn, self.config)
        return {
            "open": data["counts"]["open"],
            "done": data["counts"]["done"],
            "due_soon": len(data["due_soon"]),
            "overdue": len(data["overdue"]),
            "days_to_target": data["days_to_target"],
            "summary": data["sentence"],
        }
