"""Listings you found yourself.

The API covers one portal. When either of you finds something on
Immobiliare.it, Gate-away, an agency site or a Facebook group, paste the URL
into the web UI (or into config/manual_listings.txt) and it joins the same
pipeline: fetched, normalised, scored and ranked against everything else.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import httpx

from retirement.core.config import project_root
from retirement.modules.property.models import Listing
from retirement.modules.property.sources.base import Source

QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_queue (
    url        TEXT PRIMARY KEY,
    area_id    TEXT,
    added_at   TEXT,
    fetched_at TEXT
);
"""

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 retirement-project/0.1"


class ManualSource(Source):
    name = "manual"

    def migrate(self) -> None:
        self.conn.executescript(QUEUE_SCHEMA)
        self.conn.commit()

    def urls(self) -> list[tuple[str, str]]:
        self.migrate()
        pairs = [
            (row["url"], row["area_id"] or "manual")
            for row in self.conn.execute("SELECT url, area_id FROM manual_queue")
        ]
        seen = {url for url, _ in pairs}
        flat = project_root() / "config" / "manual_listings.txt"
        if flat.exists():
            for line in flat.read_text(encoding="utf-8").splitlines():
                url = line.strip()
                if url and not url.startswith("#") and url not in seen:
                    pairs.append((url, "manual"))
        return pairs

    def add(self, url: str, area_id: str = "manual") -> None:
        from retirement.core.db import utcnow

        self.migrate()
        self.conn.execute(
            "INSERT OR IGNORE INTO manual_queue (url, area_id, added_at) VALUES (?,?,?)",
            (url, area_id, utcnow()),
        )
        self.conn.commit()

    def fetch(self, area: dict[str, Any]) -> list[Listing]:
        """`area` is ignored -- the queue carries its own area ids."""
        out: list[Listing] = []
        for url, area_id in self.urls():
            try:
                listing = self._fetch_one(url, area_id)
            except Exception:
                continue
            if listing:
                out.append(listing)
        return out

    def _fetch_one(self, url: str, area_id: str) -> Listing | None:
        resp = httpx.get(url, headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        data = _extract_structured(html)
        external_id = re.sub(r"\W+", "-", url)[-64:]
        return Listing(
            id=f"manual:{external_id}",
            source="manual",
            external_id=external_id,
            url=url,
            title=data.get("title", url),
            description=data.get("description", ""),
            price=data.get("price"),
            size_sqm=data.get("size_sqm"),
            lat=data.get("lat"),
            lng=data.get("lng"),
            municipality=data.get("municipality", ""),
            area_id=area_id,
            photos=1 if data.get("image") else 0,
            thumbnail=data.get("image", ""),
            raw={"html_bytes": len(html)},
        )


def _extract_structured(html: str) -> dict[str, Any]:
    """JSON-LD first (most portals publish it), then OpenGraph, then regex."""
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    out: dict[str, Any] = {}

    for node in tree.css('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.text())
        except (json.JSONDecodeError, TypeError):
            continue
        for block in payload if isinstance(payload, list) else [payload]:
            if not isinstance(block, dict):
                continue
            out.setdefault("title", block.get("name"))
            out.setdefault("description", block.get("description"))
            offer = block.get("offers") or {}
            if isinstance(offer, dict) and offer.get("price"):
                out.setdefault("price", _num(offer["price"]))
            geo = block.get("geo") or {}
            if isinstance(geo, dict):
                out.setdefault("lat", _num(geo.get("latitude")))
                out.setdefault("lng", _num(geo.get("longitude")))
            address = block.get("address") or {}
            if isinstance(address, dict):
                out.setdefault("municipality", address.get("addressLocality"))

    for prop, key in (("og:title", "title"), ("og:description", "description"), ("og:image", "image")):
        node = tree.css_first(f'meta[property="{prop}"]')
        if node and node.attributes.get("content"):
            out.setdefault(key, node.attributes["content"])

    text = tree.text(separator=" ")[:20000]
    if "price" not in out or not out.get("price"):
        match = re.search(r"€\s*([\d.]{4,})", text)
        if match:
            out["price"] = _num(match.group(1).replace(".", ""))
    if not out.get("size_sqm"):
        match = re.search(r"(\d{2,4})\s*(?:m²|mq|m2)", text, re.I)
        if match:
            out["size_sqm"] = _num(match.group(1))

    return {k: v for k, v in out.items() if v not in (None, "")}


def _num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("€", "").strip())
    except (TypeError, ValueError):
        return None
