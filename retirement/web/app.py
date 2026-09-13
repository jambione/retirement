"""Local web UI: the brief, the shortlist, and the board.

Binds to 127.0.0.1 by default. If you expose it through the cloudflared tunnel,
put Cloudflare Access in front of it -- there is no login here on purpose.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime
from urllib.parse import quote
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from retirement.core import db, notify
from retirement.core.config import Profile, dump_yaml, load_dotenv, load_yaml
from retirement.core.module import REGISTRY, load_registry

load_dotenv()
load_registry()

BASE = Path(__file__).parent
app = FastAPI(title="Retirement project")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

# The value finder is a package of its own: ingestion, scoring and an API that
# has no opinion about HTML. Mounted here rather than defined here.
from retirement.modules.value.api import router as value_router  # noqa: E402

app.include_router(value_router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Browsers ask for this by path whatever the <link> tags say."""
    from fastapi.responses import FileResponse

    return FileResponse(BASE / "static" / "favicon-32.png", media_type="image/png")

_run_lock = threading.Lock()
_last_run: dict[str, Any] = {}

PROPERTY_CONFIG = "config/property.yaml"
BOARD_CONFIG = "config/board.yaml"
FINANCE_CONFIG = "config/finance.yaml"

# Steps almost every Italian purchase goes through, offered from the board's
# empty state rather than seeded -- a board you did not write is a board you
# do not trust.
STARTER_CARDS = [
    ("Apply for a codice fiscale", "paperwork", "someday"),
    ("Open an Italian bank account", "paperwork", "someday"),
    ("Decide how the purchase is financed", "finance", "someday"),
    ("Shortlist towns worth a full day each", "property", "soon"),
    ("Contact agents about off-portal stock", "property", "soon"),
    ("Book flights and a car", "trip", "soon"),
    ("Find a geometra for the survey", "property", "someday"),
    ("Understand the 9-11% closing costs", "finance", "someday"),
    ("Check what health cover costs as a resident", "healthcare", "someday"),
]


def _property_config() -> dict[str, Any]:
    return load_yaml(PROPERTY_CONFIG)


def _board_config() -> dict[str, Any]:
    return load_yaml(BOARD_CONFIG)


def _finance_config() -> dict[str, Any]:
    return load_yaml(FINANCE_CONFIG)


def _chrome() -> dict[str, Any]:
    """Values the shared header needs on every page."""
    board_cfg = _board_config()
    target = (board_cfg.get("target") or {})
    days = None
    raw = target.get("date")
    if raw:
        try:
            parsed = raw if isinstance(raw, date) else datetime.strptime(str(raw), "%Y-%m-%d").date()
            days = (parsed - date.today()).days
        except ValueError:
            days = None
    return {
        "modules": Profile.load().enabled_modules(),
        "target_label": target.get("label", ""),
        "days_to_target": days,
    }


# ── property ───────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    from retirement.modules.property import store

    conn = db.connect()
    store.migrate(conn)
    return templates.TemplateResponse(
        request,
        "property.html",
        {
            "active": "property",
            "config": _property_config(),
            "shortlist": store.latest_shortlist(conn, 40),
            "last_run": _last_run,
            **_chrome(),
        },
    )


@app.post("/criteria")
def save_criteria(
    prompt: str = Form(""),
    min_price: int = Form(0),
    max_price: int = Form(0),
    min_size: int = Form(0),
    min_bedrooms: int = Form(0),
    max_airport_km: int = Form(90),
    w_value: float = Form(0.30),
    w_character: float = Form(0.20),
    w_airport: float = Form(0.15),
    w_condition: float = Form(0.10),
    w_rental: float = Form(0.10),
    w_size: float = Form(0.15),
):
    config = _property_config()
    config["prompt"] = prompt
    config.setdefault("budget", {}).update({"min": min_price, "max": max_price})
    config.setdefault("property", {}).update(
        {"min_size_sqm": min_size, "min_bedrooms": min_bedrooms}
    )
    scoring = config.setdefault("scoring", {})
    scoring.setdefault("hard_filters", {})["max_airport_km"] = max_airport_km
    scoring["weights"] = {
        "price_vs_area_median": w_value,
        "size_per_euro": w_size,
        "airport_access": w_airport,
        "town_character": w_character,
        "condition": w_condition,
        "rental_potential": w_rental,
    }
    dump_yaml(PROPERTY_CONFIG, config)
    return RedirectResponse("/", status_code=303)


@app.post("/add")
def add_listing(url: str = Form(...), area: str = Form("manual")):
    from retirement.modules.property.sources.manual import ManualSource

    ManualSource(db.connect(), {}).add(url.strip(), area)
    return RedirectResponse("/", status_code=303)


@app.post("/decision")
def decision(listing_id: str = Form(...), verdict: str = Form(...), note: str = Form("")):
    from retirement.modules.property import store

    store.set_decision(db.connect(), listing_id, verdict, note)
    return RedirectResponse("/", status_code=303)


def _do_run(dry_run: bool) -> None:
    if not _run_lock.acquire(blocking=False):
        return
    try:
        module = REGISTRY["property"](_property_config(), db.connect())
        _last_run.clear()
        _last_run.update(module.run(dry_run=dry_run))
    except Exception as exc:  # surfaced in the UI rather than lost to the log
        _last_run.clear()
        _last_run.update({"error": str(exc)})
    finally:
        _run_lock.release()


@app.post("/run")
def trigger_run(background: BackgroundTasks, dry_run: str = Form("")):
    background.add_task(_do_run, bool(dry_run))
    return RedirectResponse("/", status_code=303)


# ── board ──────────────────────────────────────────────────────────────────
@app.get("/board", response_class=HTMLResponse)
def board(request: Request):
    from retirement.modules.board import store, summary

    conn = db.connect()
    store.migrate(conn)
    config = _board_config()
    # One read of the cards for both the columns and the summary, and no model
    # call on this path at all -- the written sentence arrives by fetch.
    cards = store.by_column(conn)
    return templates.TemplateResponse(
        request,
        "board.html",
        {
            "active": "board",
            "config": config,
            "columns": config.get("columns", []),
            "cards": cards,
            "summary": summary.build(conn, config, cards=cards),
            "today": date.today().isoformat(),
            **_chrome(),
        },
    )


@app.get("/board/summary")
def board_summary():
    """The written sentence, asked for after the board has already drawn.

    This is the only path that talks to a model, it is cached per board state,
    and a failure here costs the page nothing -- it keeps the plain sentence it
    rendered with.
    """
    from retirement.modules.board import store, summary

    conn = db.connect()
    store.migrate(conn)
    try:
        return JSONResponse(summary.write_sentence(conn, _board_config()))
    except Exception as exc:
        return JSONResponse({"sentence": "", "error": str(exc)}, status_code=502)


@app.post("/board/card")
def board_add(
    title: str = Form(...),
    column: str = Form("someday"),
    tag: str = Form(""),
    due: str = Form(""),
):
    from retirement.modules.board import store

    conn = db.connect()
    store.migrate(conn)
    if title.strip():
        store.add(conn, title, column, tag, due or None)
    return RedirectResponse("/board", status_code=303)


@app.post("/board/move")
async def board_move(request: Request):
    """Called by the drag handler; also usable as a plain form post."""
    from retirement.modules.board import store

    payload: dict[str, Any]
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
    else:
        payload = dict(await request.form())

    conn = db.connect()
    store.migrate(conn)
    terminal = tuple(
        c["id"] for c in _board_config().get("columns", []) if c.get("terminal")
    ) or ("done",)
    store.move(
        conn,
        int(payload["card_id"]),
        str(payload["column"]),
        int(payload["before_id"]) if payload.get("before_id") else None,
        terminal_columns=terminal,
    )
    if request.headers.get("content-type", "").startswith("application/json"):
        return JSONResponse({"ok": True})
    return RedirectResponse("/board", status_code=303)


@app.post("/board/delete")
def board_delete(card_id: int = Form(...)):
    from retirement.modules.board import store

    store.delete(db.connect(), card_id)
    return RedirectResponse("/board", status_code=303)


@app.post("/board/starter")
def board_starter():
    from retirement.modules.board import store

    conn = db.connect()
    store.migrate(conn)
    if not store.counts(conn)["open"]:
        for title, tag, column in STARTER_CARDS:
            store.add(conn, title, column, tag)
    return RedirectResponse("/board", status_code=303)


# ── finance ────────────────────────────────────────────────────────────────
@app.get("/finance", response_class=HTMLResponse)
def finance(request: Request):
    from retirement.modules.finance import pickup, store

    conn = db.connect()
    store.migrate(conn)
    pickup.migrate(conn)
    config = _finance_config()
    snapshot = store.latest(conn)
    prior = store.previous(conn, snapshot["id"]) if snapshot else None
    inbox, _processed = pickup.folder_paths(config)
    purchase = config.get("purchase") or {}
    cost_usd = (
        float(purchase.get("budget_eur", 0)) * float(purchase.get("eur_usd", 1))
        * (1 + float(purchase.get("closing_cost_pct", 0)) / 100)
    ) if purchase.get("budget_eur") else None

    return templates.TemplateResponse(
        request,
        "finance.html",
        {
            "active": "finance",
            "config": config,
            "snapshot": snapshot,
            "prior": prior,
            "history": store.history(conn),
            "categories": store.by_category(snapshot) if snapshot else [],
            "purchase_cost_usd": cost_usd,
            # Shown on screen, so tilde-shorten it rather than printing a
            # forty-character home directory.
            "inbox": str(inbox).replace(str(Path.home()), "~"),
            "mailbox_on": bool(((config.get("pickup") or {}).get("mailbox") or {}).get("enabled")
                               and pickup.mailbox_configured()),
            "email": notify.status(),
            "import_error": request.query_params.get("error", ""),
            **_chrome(),
        },
    )


@app.post("/finance/import")
async def finance_import(file: UploadFile = File(...)):
    from retirement.modules.finance import pickup, store

    conn = db.connect()
    store.migrate(conn)
    data = await file.read()
    # Same path as the automatic routes, so a file you drag in after it has
    # already been picked up from the folder does not double-count.
    result = pickup.ingest(conn, data, file.filename or "export.csv", "upload",
                           _finance_config())
    if result["status"] == "unreadable":
        return RedirectResponse(f"/finance?error={quote(result['error'])}", status_code=303)
    if result["status"] == "duplicate":
        return RedirectResponse(
            "/finance?error=" + quote("Already imported — that file is byte-identical to one "
                                      "picked up earlier, so nothing was added."),
            status_code=303)
    return RedirectResponse("/finance", status_code=303)


@app.post("/finance/pickup")
def finance_pickup():
    from retirement.modules.finance import pickup

    result = pickup.run(db.connect(), _finance_config())
    problems = result["problems"]
    if problems:
        first = problems[0]
        return RedirectResponse(
            "/finance?error=" + quote(f"{first['filename']}: {first.get('error', 'failed')}"),
            status_code=303)
    return RedirectResponse("/finance", status_code=303)


@app.post("/finance/delete")
def finance_delete(snapshot_id: int = Form(...)):
    from retirement.modules.finance import store

    store.delete_snapshot(db.connect(), snapshot_id)
    return RedirectResponse("/finance", status_code=303)


# ── ask B ──────────────────────────────────────────────────────────────────
# One question, one section's data, whichever AI is installed. The context is
# fetched separately from the answer so the panel can show what B is about to
# read BEFORE anything is sent anywhere.
def _context_config(scope: str) -> dict[str, Any]:
    return {"board": _board_config, "finance": _finance_config}.get(
        scope, _property_config
    )()


@app.get("/ask/context")
def ask_context(scope: str, listing_id: str = ""):
    from retirement.core import ask, context

    conn = db.connect()
    try:
        built = context.build(scope, conn, _context_config(scope), listing_id=listing_id)
    except KeyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    built["providers"] = ask.available()
    built["default_provider"] = ask.default_provider()
    return JSONResponse(built)


@app.post("/ask")
async def ask_run(request: Request):
    from retirement.core import ask, context

    payload = await request.json()
    scope = str(payload.get("scope", "shortlist"))
    question = str(payload.get("question", "")).strip()
    if not question:
        return JSONResponse({"error": "Ask something first."}, status_code=400)

    conn = db.connect()
    try:
        built = context.build(
            scope, conn, _context_config(scope), listing_id=str(payload.get("listing_id", ""))
        )
        prompt = context.prompt_for(built, question)
        result = ask.ask(prompt, provider=payload.get("provider") or None)
        result["sent_chars"] = len(prompt)
    except Exception as exc:
        # The panel shows this verbatim; a CLI that is merely logged out should
        # say so rather than turn into a generic 500.
        return JSONResponse({"error": str(exc)}, status_code=502)
    result["scope"] = scope
    return JSONResponse(result)


@app.get("/value", response_class=HTMLResponse)
def value_page(request: Request):
    """The investor view: what is loaded, and what the official record says
    about each listing we hold."""
    from retirement.modules.value import store as value_store

    conn = db.connect()
    value_store.migrate(conn)
    # Only what YOU checked: a property typed into the box (no listing row at
    # all) or a list you uploaded. Everything the nightly scan found is scored
    # too, but it belongs on the Property page -- repeating it here made two
    # pages show the same table and neither say why.
    rows = conn.execute(
        """SELECT v.listing_id, v.score, v.ref, v.scored_at, v.breakdown,
                  l.title, l.url, l.price, l.size_sqm, l.municipality, l.province
           FROM value_scores v LEFT JOIN listings l ON l.id = v.listing_id
           WHERE l.id IS NULL OR (l.active = 1 AND l.source = 'upload')
           ORDER BY v.scored_at DESC LIMIT 100"""
    ).fetchall()
    scored = []
    for row in rows:
        item = dict(row)
        item["breakdown"] = json.loads(item["breakdown"]) if item["breakdown"] else {}
        shown = item["breakdown"].get("input") or {}
        comune = (item["breakdown"].get("comune") or {})
        item["title"] = item["title"] or comune.get("name") or "Scored from the box"
        item["price"] = item["price"] or shown.get("price")
        item["size_sqm"] = item["size_sqm"] or shown.get("size_m2")
        item["municipality"] = item["municipality"] or comune.get("name", "")
        item["province"] = item["province"] or comune.get("prov", "")
        scored.append(item)
    # The towns we can actually answer for: the ones with official values, and
    # the ones we hold figures for. Offered as a datalist so nobody types a
    # comune we have never heard of and reads the empty answer as a verdict.
    names = [r[0] for r in conn.execute(
        """SELECT DISTINCT comune FROM omi_zone_values WHERE comune <> ''
           UNION SELECT name FROM comuni WHERE name <> ''
           ORDER BY 1 LIMIT 9000"""
    ).fetchall()]

    return templates.TemplateResponse(
        request,
        "value.html",
        {
            "active": "value",
            "scored": scored,
            "comune_names": names,
            "coverage": value_store.coverage(conn),
            # How many of the scan's own listings have a value score, so this
            # page can point at the Property page rather than duplicate it.
            "scan_scored": conn.execute(
                """SELECT COUNT(*) FROM value_scores v JOIN listings l ON l.id = v.listing_id
                   WHERE l.active = 1 AND l.source <> 'upload'"""
            ).fetchone()[0],
            **_chrome(),
        },
    )


@app.get("/healthz")
def healthz():
    return {"ok": True}
