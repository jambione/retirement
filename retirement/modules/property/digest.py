"""The email you actually read over coffee."""
from __future__ import annotations

from datetime import date
from typing import Any

CSS = """
body{margin:0;background:#f6f5f2;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1f2421;}
.wrap{max-width:680px;margin:0 auto;padding:24px 16px;}
h1{font-size:20px;margin:0 0 4px;letter-spacing:-0.01em;}
.sub{color:#6b7280;font-size:13px;margin:0 0 24px;}
.card{background:#fff;border:1px solid #e6e3dd;border-radius:10px;padding:16px;margin-bottom:12px;}
.card h2{font-size:15px;margin:0 0 2px;}
.card h2 a{color:#1f2421;text-decoration:none;}
.meta{color:#6b7280;font-size:13px;margin:0 0 10px;}
.price{font-size:18px;font-weight:600;}
.score{float:right;background:#1f2421;color:#fff;border-radius:999px;padding:3px 10px;font-size:13px;font-weight:600;}
.bars{font-size:12px;color:#4b5563;margin-top:8px;}
.bar{display:inline-block;margin-right:10px;white-space:nowrap;}
.note{font-size:13px;color:#374151;margin:8px 0 0;font-style:italic;}
.concern{font-size:12px;color:#92400e;background:#fef3c7;border-radius:4px;padding:2px 6px;display:inline-block;margin:6px 4px 0 0;}
.tag{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#6b7280;}
.new{color:#065f46;background:#d1fae5;border-radius:4px;padding:2px 6px;font-size:11px;font-weight:600;}
.drop{color:#9a3412;background:#ffedd5;border-radius:4px;padding:2px 6px;font-size:11px;font-weight:600;}
h3{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:#6b7280;margin:28px 0 10px;}
.foot{color:#9ca3af;font-size:12px;margin-top:28px;line-height:1.5;}
"""


def _euro(value: Any) -> str:
    try:
        return f"€{float(value):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "price on request"


def _card(item: dict[str, Any]) -> str:
    detail = item.get("detail") or {}
    components = detail.get("components") or {}
    airport = detail.get("airport") or {}

    badges = []
    if item.get("is_new"):
        badges.append('<span class="new">NEW</span>')
    if item.get("price_change") and item["price_change"] < 0:
        badges.append(f'<span class="drop">▼ {_euro(abs(item["price_change"]))}</span>')

    bars = "".join(
        f'<span class="bar">{label}: <b>{components.get(key, 0):.0f}</b></span>'
        for key, label in (
            ("price_vs_area_median", "value"),
            ("town_character", "character"),
            ("condition", "condition"),
            ("airport_access", "access"),
            ("rental_potential", "rental"),
        )
        if key in components
    )

    ppsqm = detail.get("price_per_sqm")
    mark = detail.get("benchmark") or {}
    comparison = ""
    if ppsqm and mark.get("per_sqm"):
        delta = 100 * (ppsqm / mark["per_sqm"] - 1)
        against = ("the OMI value for the comune" if mark.get("source") == "omi"
                   else "the area median")
        comparison = f" · €{ppsqm:,.0f}/m² ({delta:+.0f}% vs {against})".replace(",", ".")

    concerns = "".join(
        f'<span class="concern">{c}</span>' for c in (detail.get("concerns") or [])
    )
    note = f'<p class="note">{detail["note"]}</p>' if detail.get("note") else ""
    access = f" · {airport['km']} km to {airport['iata']}" if airport else ""
    size = f"{item['size_sqm']:.0f} m²" if item.get("size_sqm") else "size n/a"
    rooms = f" · {item['rooms']} rooms" if item.get("rooms") else ""

    return f"""
    <div class="card">
      <span class="score">{detail.get('total', 0):.0f}</span>
      <h2><a href="{item.get('url','#')}">{item.get('title') or 'Untitled listing'}</a></h2>
      <p class="meta">{item.get('municipality','')} {item.get('province','')}{access} · <span class="tag">{item.get('area_id','')}</span> {' '.join(badges)}</p>
      <div class="price">{_euro(item.get('price'))}</div>
      <p class="meta">{size}{rooms}{comparison}</p>
      <div class="bars">{bars}</div>
      {note}
      {concerns}
    </div>"""


def render(
    shortlist: list[dict[str, Any]],
    near_misses: list[dict[str, Any]],
    summary: dict[str, Any],
) -> tuple[str, str, str]:
    """Returns (subject, html, plain_text)."""
    new_count = sum(1 for item in shortlist if item.get("is_new"))
    subject = (
        f"Italy property · {len(shortlist)} to look at"
        + (f" · {new_count} new" if new_count else "")
        + f" · {date.today():%d %b}"
    )

    cards = "".join(_card(item) for item in shortlist) or "<p>Nothing cleared the filters this run.</p>"
    misses = ""
    if near_misses:
        misses = "<h3>Near misses</h3>" + "".join(
            f'<div class="card"><h2><a href="{m.get("url","#")}">{m.get("title","")}</a></h2>'
            f'<p class="meta">{_euro(m.get("price"))} · {m.get("municipality","")} — '
            f'excluded: {m.get("rejected","")}</p></div>'
            for m in near_misses
        )

    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body><div class="wrap">
  <h1>Italy property shortlist</h1>
  <p class="sub">{summary.get('scanned', 0)} listings scanned across {summary.get('areas', 0)} areas ·
     {summary.get('new', 0)} new · {summary.get('price_drops', 0)} price drops ·
     {summary.get('gone', 0)} off market</p>
  {cards}
  {misses}
  <p class="foot">Value is measured against the Agenzia delle Entrate's OMI figures
  for the comune where those have been imported, and against the stock currently
  listed in the search area where they have not — the label after each price says
  which. Market values: Agenzia Entrate — OMI.</p>
</div></body></html>"""

    text_lines = [f"{i+1}. {item.get('title','')} — {_euro(item.get('price'))} — "
                  f"score {((item.get('detail') or {}).get('total', 0)):.0f} — {item.get('url','')}"
                  for i, item in enumerate(shortlist)]
    return subject, html, "\n".join(text_lines)
