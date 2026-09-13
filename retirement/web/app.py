"""Local web UI: the brief, the shortlist, and the board.

Binds to 127.0.0.1 by default. If you expose it through the cloudflared tunnel,
put Cloudflare Access in front of it -- there is no login here on purpose.
"""
from __future__ import annotations

import threading
from datetime import date, datetime
from urllib.parse import quote
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from retirement.core import db
from retirement.core.config import Profile, dump_yaml, load_dotenv, load_yaml
from retirement.core.module import REGISTRY, load_registry

load_dotenv()
load_registry()

BASE = Path(__file__).parent
app = FastAPI(title="Retirement project")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

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
    return templates.TemplateResponse(
        request,
        "board.html",
        {
            "active": "board",
            "config": config,
            "columns": config.get("columns", []),
            "cards": store.by_column(conn),
            "summary": summary.build(conn, config),
            "today": date.today().isoformat(),
            **_chrome(),
        },
    )


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
    from retirement.modules.finance import store

    conn = db.connect()
    store.migrate(conn)
    config = _finance_config()
    snapshot = store.latest(conn)
    prior = store.previous(conn, snapshot["id"]) if snapshot else None
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
            "import_error": request.query_params.get("error", ""),
            **_chrome(),
        },
    )


@app.post("/finance/import")
async def finance_import(file: UploadFile = File(...)):
    from retirement.modules.finance import importer, store

    conn = db.connect()
    store.migrate(conn)
    data = await file.read()
    try:
        parsed = importer.parse(data, file.filename or "export.csv", _finance_config())
    except importer.ImportError_ as exc:
        return RedirectResponse(f"/finance?error={quote(str(exc))}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/finance?error={quote(f'Could not read that file: {exc}')}",
                                status_code=303)
    store.save_snapshot(conn, parsed)
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
        result = ask.ask(
            context.prompt_for(built, question),
            provider=payload.get("provider") or None,
        )
    except Exception as exc:
        # The panel shows this verbatim; a CLI that is merely logged out should
        # say so rather than turn into a generic 500.
        return JSONResponse({"error": str(exc)}, status_code=502)
    result["scope"] = scope
    return JSONResponse(result)


@app.get("/healthz")
def healthz():
    return {"ok": True}
