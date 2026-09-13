"""What B can see.

Each scope turns a part of the project into a compact, factual block of text,
plus a plain sentence saying what that block contains. The sentence matters as
much as the data: the point of the B button is that you can tell, before you
ask anything, exactly what the model is and is not looking at.

Nothing here invents or summarises. It selects.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def _euro(value: Any) -> str:
    try:
        return f"€{float(value):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "n/a"


# ── scopes ─────────────────────────────────────────────────────────────────
def brief(conn: sqlite3.Connection, config: dict[str, Any], **_: Any) -> dict[str, Any]:
    areas = config.get("areas") or []
    weights = (config.get("scoring") or {}).get("weights", {})
    hard = (config.get("scoring") or {}).get("hard_filters", {})
    lines = [
        "WHAT WE ARE LOOKING FOR (written by us):",
        (config.get("prompt") or "").strip(),
        "",
        f"Budget: {_euro((config.get('budget') or {}).get('min'))} to "
        f"{_euro((config.get('budget') or {}).get('max'))}.",
        f"Minimum size: {(config.get('property') or {}).get('min_size_sqm', 'any')} m².",
        f"Hard limit: no more than {hard.get('max_airport_km', 'n/a')} km from an "
        "international airport.",
        "",
        "SEARCH AREAS:",
        *[f"- {a.get('label')} ({a.get('radius_km')} km radius). {a.get('note', '')}".rstrip()
          for a in areas],
        "",
        "HOW WE WEIGHT VALUE:",
        *[f"- {k.replace('_', ' ')}: {v}" for k, v in weights.items()],
    ]
    return {
        "title": "The brief",
        "explains": (
            f"Your written brief, the budget, the {len(areas)} search areas and the six "
            "scoring weights. No listings."
        ),
        "context": "\n".join(lines),
        "rows": len(areas),
        "suggestions": [
            "Is this brief internally consistent, or am I asking for two different houses?",
            "Which of these three areas is the odd one out, and why?",
            "What have I not specified that will cost me time on the ground?",
        ],
    }


def shortlist(conn: sqlite3.Connection, config: dict[str, Any], limit: int = 20, **_: Any):
    from retirement.modules.property import store

    items = store.latest_shortlist(conn, limit)
    lines = ["CURRENT SHORTLIST (highest value score first):"]
    for i, item in enumerate(items, 1):
        detail = item.get("detail") or {}
        comps = detail.get("components") or {}
        airport = detail.get("airport") or {}
        lines.append(
            f"{i}. {item.get('title', 'untitled')} — {_euro(item.get('price'))}"
            f" · {item.get('size_sqm') or '?'} m²"
            f" · {item.get('municipality', '')} {item.get('province', '')}"
            f" · area: {item.get('area_id', '')}"
        )
        lines.append(
            f"   score {detail.get('total', 0)}"
            + (f" · €{detail.get('price_per_sqm')}/m²" if detail.get("price_per_sqm") else "")
            + (f" (area median €{detail.get('area_median_per_sqm')}/m²)"
               if detail.get("area_median_per_sqm") else "")
            + (f" · {airport.get('km')} km to {airport.get('iata')}" if airport else "")
        )
        if comps:
            lines.append("   " + " · ".join(f"{k}={v:.0f}" for k, v in comps.items()))
        if detail.get("note"):
            lines.append(f"   note: {detail['note']}")
        if detail.get("concerns"):
            lines.append(f"   concerns: {', '.join(detail['concerns'])}")
        if item.get("verdict"):
            lines.append(f"   our verdict so far: {item['verdict']}")
    return {
        "title": "The shortlist",
        "explains": (
            f"The {len(items)} listings currently ranked, with price, size, price per m² "
            "against each area's median, every score component, and any verdict we have "
            "already recorded. Not the full listing text."
        ),
        "context": "\n".join(lines) if items else "The shortlist is empty.",
        "rows": len(items),
        "suggestions": [
            "Which two of these are actually comparable, and which is a different decision?",
            "Where is the scoring flattering a listing that should not be near the top?",
            "If we could only visit three towns in March, which three and in what order?",
        ],
    }


def listing(conn: sqlite3.Connection, config: dict[str, Any], listing_id: str = "", **_: Any):
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if row is None:
        return {"title": "Listing", "explains": "That listing is no longer in the database.",
                "context": "", "rows": 0, "suggestions": []}
    score_row = conn.execute(
        """SELECT total, detail FROM listing_scores WHERE listing_id = ?
           ORDER BY scored_at DESC LIMIT 1""", (listing_id,)
    ).fetchone()
    detail = json.loads(score_row["detail"]) if score_row and score_row["detail"] else {}
    history = conn.execute(
        "SELECT seen_at, price FROM price_history WHERE listing_id = ? ORDER BY seen_at",
        (listing_id,),
    ).fetchall()

    lines = [
        f"LISTING: {row['title']}",
        f"{_euro(row['price'])} · {row['size_sqm'] or '?'} m² · "
        f"{row['rooms'] or '?'} rooms · {row['municipality']} ({row['province']})",
        f"Condition as listed: {row['condition'] or 'not stated'}",
        f"Score {detail.get('total', 'n/a')} — "
        + " · ".join(f"{k}={v}" for k, v in (detail.get("components") or {}).items()),
        "",
        "PRICE HISTORY:",
        *[f"- {h['seen_at'][:10]}: {_euro(h['price'])}" for h in history],
        "",
        "THE AGENT'S OWN DESCRIPTION (Italian, verbatim):",
        (row["description"] or "")[:3000],
    ]
    return {
        "title": row["title"] or "Listing",
        "explains": (
            "This one listing: the figures, its score breakdown, every price we have seen "
            "it at, and the agent's own description in full."
        ),
        "context": "\n".join(lines),
        "rows": 1,
        "suggestions": [
            "Translate the description and tell me what it is quietly not saying.",
            "What should we ask the agent before we book a viewing?",
            "What would this cost to make habitable, and what is the range of error?",
        ],
    }


def board(conn: sqlite3.Connection, config: dict[str, Any], **_: Any):
    from retirement.modules.board import store

    cards = store.by_column(conn)
    labels = {c["id"]: c["label"] for c in (config.get("columns") or [])}
    lines = ["THE BOARD:"]
    total = 0
    for column_id, items in cards.items():
        lines.append(f"\n{labels.get(column_id, column_id).upper()}:")
        for card in items:
            total += 1
            bits = [f"- {card['title']}"]
            if card["tag"]:
                bits.append(f"[{card['tag']}]")
            if card["due"]:
                bits.append(f"due {card['due']}")
            if card["done_at"]:
                bits.append(f"done {card['done_at'][:10]}")
            lines.append(" ".join(bits))
            if card["notes"]:
                lines.append(f"    {card['notes'][:300]}")
    target = config.get("target") or {}
    if target:
        lines.append(f"\nTARGET: {target.get('label')} on {target.get('date')}.")
    return {
        "title": "The board",
        "explains": f"All {total} cards with their column, tag and dates. Nothing else.",
        "context": "\n".join(lines) if total else "The board is empty.",
        "rows": total,
        "suggestions": [
            "What is actually blocking what here?",
            "What is missing that someone who has bought in Italy would tell me to add?",
            "Given the trip date, what order should the 'before the trip' column be in?",
        ],
    }


def finance(conn: sqlite3.Connection, config: dict[str, Any], **_: Any):
    from retirement.modules.finance import store

    snapshot = store.latest(conn)
    if snapshot is None:
        return {"title": "Net worth", "explains": "No export has been imported yet.",
                "context": "", "rows": 0, "suggestions": []}
    prior = store.previous(conn, snapshot["id"])
    lines = [
        f"NET WORTH AS OF {snapshot['as_of']} (imported from {snapshot['source']}):",
        f"Assets {_euro(snapshot['assets']).replace('€', snapshot['currency'] + ' ')}"
        f" · Liabilities {snapshot['liabilities']:,.0f}"
        f" · Net worth {snapshot['net_worth']:,.0f} {snapshot['currency']}",
    ]
    if prior:
        lines.append(
            f"Previous snapshot {prior['as_of']}: net worth {prior['net_worth']:,.0f} "
            f"({snapshot['net_worth'] - prior['net_worth']:+,.0f})"
        )
    lines.append("\nACCOUNTS:")
    for account in snapshot["accounts"]:
        kind = "liability" if account["is_liability"] else account["category"]
        lines.append(
            f"- {account['name']}"
            + (f" ({account['institution']})" if account["institution"] else "")
            + f" · {kind} · {account['balance']:,.0f}"
        )
    purchase = config.get("purchase") or {}
    if purchase.get("budget_eur"):
        lines.append(
            f"\nThe Italian purchase we are planning: €{purchase['budget_eur']:,.0f} "
            f"plus about {purchase.get('closing_cost_pct', 10)}% closing costs, "
            f"at roughly {purchase.get('eur_usd', 1)} USD per EUR."
        )
    return {
        "title": "Net worth",
        "explains": (
            f"The {snapshot['as_of']} snapshot: every account and balance from the export, "
            "the assets/liabilities split, and the previous snapshot for comparison. "
            "These are real balances — they go to whichever model you pick below."
        ),
        "context": "\n".join(lines),
        "rows": len(snapshot["accounts"]),
        "suggestions": [
            "What does buying this house at this price actually do to the balance sheet?",
            "Which accounts would the purchase sensibly come out of, and in what order?",
            "What has moved most since the previous snapshot?",
        ],
    }


def value(conn: sqlite3.Connection, config: dict[str, Any], istat: str = "",
          listing_id: str = "", **_: Any):
    """What the official record says — and, just as explicitly, what it does not.

    The value model is only as good as the files that have been imported, so
    this block leads with coverage. A model that cannot see "no OMI quotation
    is loaded for this comune" will cheerfully reason about a band that does
    not exist; one that can see it says so instead.
    """
    from retirement.modules.value import store as value_store

    coverage = value_store.coverage(conn)
    lines = [
        "WHAT OFFICIAL DATA IS LOADED (nothing below is scraped or estimated):",
        f"- OMI quotations: {coverage['omi_rows']:,} rows across {coverage['omi_comuni']} "
        f"comuni, semester {coverage['semester'] or 'none'}, "
        f"{coverage['omi_with_rent']:,} of them carrying a rent band.",
        f"- OMI zone polygons: {coverage['zones']:,} "
        f"({'point-in-zone matching is possible' if coverage['zones'] else 'no zone matching — bands fall back to comune grain'}).",
        f"- Comune indicators: {coverage['stats']:,} figures"
        + (f" ({', '.join(coverage['stat_metrics'])})" if coverage["stat_metrics"] else "")
        + ".",
        f"- Listings scored: {coverage['scored']:,}.",
    ]

    codes = [istat] if istat else [
        row["istat"] for row in conn.execute(
            "SELECT DISTINCT istat FROM comune_stats ORDER BY istat LIMIT 6"
        ).fetchall()
    ]
    for code in [c for c in codes if c]:
        comune = value_store.resolve_comune(conn, istat=code) or {}
        stats = value_store.stats_for(conn, code)
        if not stats:
            continue
        lines.append(f"\n{(comune.get('name') or code).upper()} "
                     f"({code}{', ' + comune['prov'] if comune.get('prov') else ''}):")
        for metric, item in sorted(stats.items()):
            year = f" ({item['year']})" if item["year"] else ""
            lines.append(f"- {metric}{year}: {item['value']:,.2f}".rstrip("0").rstrip(".")
                         + f"  [{item['source']}]")
        bands = value_store.comune_bands(conn, istat=code)
        if bands:
            sale = [b for b in bands if b["compr_min"]]
            rent = [b for b in bands if b["loc_min"]]
            if sale:
                lines.append(
                    f"- OMI sale band across {len({b['linkzona'] for b in sale})} zone(s): "
                    f"€{min(b['compr_min'] for b in sale):,.0f}–"
                    f"{max(b['compr_max'] for b in sale):,.0f} per m²  [Agenzia Entrate — OMI]"
                )
            if rent:
                lines.append(
                    f"- OMI rent band: €{min(b['loc_min'] for b in rent):,.2f}–"
                    f"{max(b['loc_max'] for b in rent):,.2f} per m² per MONTH  "
                    "[Agenzia Entrate — OMI]"
                )
        else:
            lines.append("- No OMI quotation is loaded for this comune, so no price or "
                         "yield component can be computed here.")

    try:
        scored = conn.execute(
            """SELECT v.listing_id, v.score, v.breakdown, l.title, l.price, l.size_sqm,
                      l.municipality
               FROM value_scores v JOIN listings l ON l.id = v.listing_id
               WHERE l.active = 1 AND (? = '' OR v.listing_id = ?)
               ORDER BY v.score DESC LIMIT 12""",
            (listing_id, listing_id),
        ).fetchall()
    except sqlite3.OperationalError:
        scored = []          # no listings table yet: the property module never ran
    if scored:
        lines.append("\nSCORED LISTINGS (0-100, with the share of the intended weight that "
                     "actually had data behind it):")
        for row in scored:
            breakdown = json.loads(row["breakdown"]) if row["breakdown"] else {}
            per_sqm = (f"€{row['price'] / row['size_sqm']:,.0f}/m²"
                       if row["price"] and row["size_sqm"] else "no €/m²")
            lines.append(
                f"- {row['title'] or row['listing_id']} · {row['municipality']} · "
                f"{_euro(row['price'])} · {per_sqm} · score "
                + (f"{row['score']:.1f} ({breakdown.get('band', '?')})"
                   if row["score"] is not None
                   else f"WITHHELD — {breakdown.get('why_not', 'not enough data')}")
                + f", confidence {round((breakdown.get('confidence') or 0) * 100)}%"
            )
            for component in breakdown.get("components", []):
                state = "no data" if component["score"] is None else f"{component['score']:.0f}"
                lines.append(f"    · {component['label']}: {state} — {component['detail']}")
            risk = breakdown.get("risk") or {}
            if risk.get("score"):
                lines.append(f"    · {risk['label']}: -{risk['score']} — {risk['detail']}")

    lines.append(
        "\nHOW TO READ THIS. OMI publishes a BAND for a zone and a typology, not an "
        "appraisal of a building, and its rent figures are per m² per MONTH. Listing "
        "prices are asking prices, not transaction prices. A component marked 'no data' "
        "was not scored at all and its weight was redistributed — it is not a zero, and "
        "it must not be read as a bad result."
    )

    return {
        "title": "The official record",
        "explains": (
            "Everything the value model can currently see: which official files are "
            f"loaded ({coverage['omi_rows']:,} OMI rows, {coverage['stats']:,} comune "
            "figures), the OMI sale and rent bands and the ISPRA/ISTAT/MEF indicators "
            "for each comune we hold, and the full score breakdown for every scored "
            "listing — including which components had no data. Sources are named "
            "against every figure."
        ),
        "context": "\n".join(lines),
        "rows": coverage["stats"] + coverage["omi_rows"],
        "suggestions": [
            "Which of these scores is doing real work, and which is mostly missing data?",
            "What would change most if I imported the OMI file for these comuni?",
            "Given the bands and the hazard figures, where would you actually look first?",
            "What is this data NOT able to tell us about these places?",
        ],
    }


SCOPES = {
    "brief": brief,
    "finance": finance,
    "shortlist": shortlist,
    "listing": listing,
    "board": board,
    "value": value,
}


def build(scope: str, conn: sqlite3.Connection, config: dict[str, Any], **kwargs: Any):
    builder = SCOPES.get(scope)
    if builder is None:
        raise KeyError(f"unknown context scope: {scope}")
    result = builder(conn, config, **kwargs)
    result["scope"] = scope
    result["chars"] = len(result.get("context", ""))
    return result


def prompt_for(context: dict[str, Any], question: str) -> str:
    """The full prompt sent to whichever backend answers."""
    return (
        "You are helping a couple who are looking for a house in Italy to use now and "
        "retire into later. Answer using ONLY the data below. If the data does not "
        "settle the question, say so plainly and say what would. Be direct and specific; "
        "no preamble, no restating the question, no bullet-point padding.\n\n"
        f"=== {context['title'].upper()} ===\n{context['context']}\n=== END ===\n\n"
        f"QUESTION: {question.strip()}"
    )
