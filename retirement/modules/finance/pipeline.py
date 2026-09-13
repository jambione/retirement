"""Finance as a Module. Nothing to poll -- `run` reports the current position
and how it has moved, so the board, the CLI and any future digest can all ask
the same question the same way."""
from __future__ import annotations

from typing import Any

from retirement.core.module import Module, register
from retirement.modules.finance import pickup, store


@register("finance")
class FinanceModule(Module):
    def migrate(self) -> None:
        store.migrate(self.conn)
        pickup.migrate(self.conn)

    def run(self, dry_run: bool = False) -> dict[str, Any]:
        self.migrate()
        collected = pickup.run(self.conn, self.config)
        snapshot = store.latest(self.conn)
        if snapshot is None:
            return {"snapshots": 0, "note": "No export imported yet.", "pickup": collected}
        prior = store.previous(self.conn, snapshot["id"])
        purchase = self.config.get("purchase") or {}
        budget_usd = (
            float(purchase.get("budget_eur", 0))
            * float(purchase.get("eur_usd", 1))
            * (1 + float(purchase.get("closing_cost_pct", 0)) / 100)
        )
        return {
            "as_of": snapshot["as_of"],
            "net_worth": snapshot["net_worth"],
            "assets": snapshot["assets"],
            "liabilities": snapshot["liabilities"],
            "change": (round(snapshot["net_worth"] - prior["net_worth"], 2) if prior else None),
            "since": prior["as_of"] if prior else None,
            "purchase_cost_usd": round(budget_usd, 2) if budget_usd else None,
            "warnings": snapshot["warnings"],
            "pickup": collected,
        }
