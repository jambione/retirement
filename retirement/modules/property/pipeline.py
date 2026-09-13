"""fetch -> normalise -> filter -> score -> persist -> email."""
from __future__ import annotations

import logging
from typing import Any

from retirement.core import db, notify
from retirement.core.module import Module, register
from retirement.modules.property import digest, omi, score, store
from retirement.modules.property.models import Listing
from retirement.modules.property.sources.idealista import IdealistaSource, QuotaExceeded
from retirement.modules.property.sources.manual import ManualSource

log = logging.getLogger("retirement.property")

# Cap on how many listings go to the LLM per run. The qualitative read only
# changes the ordering near the top, so there is no point paying to assess the
# long tail -- the numeric pre-score picks who gets looked at.
LLM_BUDGET = 30


@register("property")
class PropertyModule(Module):
    def migrate(self) -> None:
        store.migrate(self.conn)
        omi.migrate(self.conn)
        ManualSource(self.conn, self.config).migrate()

    # ------------------------------------------------------------------ run
    def run(self, dry_run: bool = False) -> dict[str, Any]:
        self.migrate()
        run_id = db.start_run(self.conn, "property")
        areas = self.config.get("areas") or []
        summary: dict[str, Any] = {
            "areas": len(areas), "scanned": 0, "new": 0, "price_drops": 0,
            "gone": 0, "rejected": 0, "sources": [], "warnings": [],
        }

        listings, flags = self._collect(areas, summary)
        if not listings:
            summary["warnings"].append("no listings returned from any source")
            db.finish_run(self.conn, run_id, summary)
            return summary

        survivors, rejected = self._apply_hard_filters(listings, run_id)
        summary["rejected"] = len(rejected)

        scored = self._score(survivors, run_id)
        self.conn.commit()

        cfg_digest = self.config.get("digest", {})
        limit = int(cfg_digest.get("max_listings", 12))
        shortlist = scored[:limit]
        for item in shortlist:
            item.update(flags.get(item["id"], {}))

        summary["shortlisted"] = len(shortlist)
        summary["top_score"] = shortlist[0]["detail"]["total"] if shortlist else None

        near_misses = rejected[:5] if cfg_digest.get("include_near_misses") else []
        self._maybe_email(shortlist, near_misses, summary, cfg_digest, dry_run)

        db.finish_run(self.conn, run_id, summary)
        return summary

    # -------------------------------------------------------------- collect
    def _collect(self, areas: list[dict], summary: dict) -> tuple[list[Listing], dict]:
        sources = []
        idealista = IdealistaSource(self.conn, self.config)
        if idealista.available():
            sources.append(idealista)
        else:
            summary["warnings"].append(
                "idealista credentials missing -- running on manual listings only"
            )
        sources.append(ManualSource(self.conn, self.config))

        listings: list[Listing] = []
        seen_ids: set[str] = set()
        flags: dict[str, dict] = {}

        for src in sources:
            summary["sources"].append(src.name)
            targets = areas if src.name != "manual" else [{"id": "manual", "center": (0, 0)}]
            for area in targets:
                try:
                    found = src.fetch(area)
                except QuotaExceeded as exc:
                    log.warning("%s", exc)
                    summary["warnings"].append(str(exc))
                    break
                except Exception as exc:  # one bad source must not kill the run
                    log.exception("source %s failed on area %s", src.name, area.get("id"))
                    summary["warnings"].append(f"{src.name}/{area.get('id')}: {exc}")
                    continue
                for listing in found:
                    if listing.id in seen_ids:
                        continue
                    seen_ids.add(listing.id)
                    result = store.upsert(self.conn, listing)
                    flags[listing.id] = result
                    summary["new"] += int(result["is_new"])
                    if (result.get("price_change") or 0) < 0:
                        summary["price_drops"] += 1
                    listings.append(listing)

        summary["scanned"] = len(listings)
        summary["gone"] = store.mark_gone(
            self.conn, seen_ids, [a["id"] for a in areas]
        )
        self.conn.commit()
        return listings, flags

    # --------------------------------------------------------------- filter
    def _apply_hard_filters(self, listings: list[Listing], run_id: int):
        hard = (self.config.get("scoring") or {}).get("hard_filters", {})
        excludes = (self.config.get("property") or {}).get("exclude_keywords", [])
        survivors: list[Listing] = []
        rejected: list[dict[str, Any]] = []
        for listing in listings:
            reason = score.hard_filter(listing, hard, excludes)
            if reason:
                store.save_score(self.conn, listing.id, run_id, None, rejected=reason)
                rejected.append(
                    {
                        "id": listing.id, "title": listing.title, "url": listing.url,
                        "price": listing.price, "municipality": listing.municipality,
                        "rejected": reason,
                    }
                )
            else:
                survivors.append(listing)
        return survivors, rejected

    # ---------------------------------------------------------------- score
    def _score(self, survivors: list[Listing], run_id: int) -> list[dict[str, Any]]:
        weights = (self.config.get("scoring") or {}).get("weights", {})
        hard = (self.config.get("scoring") or {}).get("hard_filters", {})
        medians = score.area_medians(survivors)

        ratios = [
            item.size_sqm / item.price
            for item in survivors
            if item.price and item.size_sqm and item.price > 0
        ]
        spe_range = (min(ratios), max(ratios)) if len(ratios) > 1 else None

        # One OMI lookup per comune rather than per listing.
        market: dict[str, dict | None] = {}
        for item in survivors:
            if item.municipality not in market:
                market[item.municipality] = omi.lookup(
                    self.conn, item.municipality, item.province
                )

        # Cheap numeric pass first, so the LLM only sees plausible candidates.
        prelim = sorted(
            survivors,
            key=lambda l: score.score_listing(
                l, medians, weights, hard, None, spe_range, market.get(l.municipality)
            )["total"],
            reverse=True,
        )
        assessments = self._assess(prelim[:LLM_BUDGET])

        results = []
        for listing in survivors:
            detail = score.score_listing(
                listing, medians, weights, hard, assessments.get(listing.id), spe_range,
                market.get(listing.municipality),
            )
            store.save_score(self.conn, listing.id, run_id, detail)
            results.append(
                {
                    "id": listing.id, "title": listing.title, "url": listing.url,
                    "price": listing.price, "size_sqm": listing.size_sqm,
                    "rooms": listing.rooms, "municipality": listing.municipality,
                    "province": listing.province, "area_id": listing.area_id,
                    "detail": detail,
                }
            )
        results.sort(key=lambda r: r["detail"]["total"], reverse=True)
        return results

    def _assess(self, candidates: list[Listing]) -> dict[str, dict]:
        from retirement.core import llm

        if not llm.available() or not candidates:
            return {}
        brief = self.config.get("prompt", "") or ""
        hints = llm.parse_prompt(brief)
        if hints:
            brief += "\n\nStructured reading of the brief:\n" + str(hints)
        payload = [
            {
                "id": c.id, "title": c.title, "municipality": c.municipality,
                "province": c.province, "price": c.price, "size_sqm": c.size_sqm,
                "rooms": c.rooms, "condition": c.condition, "description": c.description,
            }
            for c in candidates
        ]
        # Batch so a single oversized request cannot fail the whole assessment.
        out: dict[str, dict] = {}
        for start in range(0, len(payload), 10):
            out.update(llm.assess_listings(brief, payload[start:start + 10]))
        return out

    # ---------------------------------------------------------------- email
    def _maybe_email(self, shortlist, near_misses, summary, cfg_digest, dry_run):
        if dry_run:
            summary["email"] = "skipped (dry run)"
            return
        if cfg_digest.get("only_send_if_new") and not summary["new"] and not summary["price_drops"]:
            summary["email"] = "skipped (nothing new)"
            return
        if not notify.configured():
            summary["email"] = "skipped (SMTP not configured)"
            return
        subject, html, text = digest.render(shortlist, near_misses, summary)
        try:
            notify.send(subject, html, text)
            summary["email"] = f"sent: {subject}"
        except Exception as exc:
            summary["email"] = f"failed: {exc}"
            summary["warnings"].append(f"email failed: {exc}")
