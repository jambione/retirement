"""Local web UI: set the parameters, read the shortlist, record verdicts.

Binds to 127.0.0.1 by default. If you expose it through the cloudflared tunnel,
put Cloudflare Access in front of it -- there is no login here on purpose.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from retirement.core import db
from retirement.core.config import Profile, dump_yaml, load_dotenv, load_yaml
from retirement.core.module import REGISTRY, load_registry

load_dotenv()
load_registry()

BASE = Path(__file__).parent
app = FastAPI(title="Retirement project")
templates = Jinja2Templates(directory=str(BASE / "templates"))

_run_lock = threading.Lock()
_last_run: dict[str, Any] = {}

PROPERTY_CONFIG = "config/property.yaml"


def _config() -> dict[str, Any]:
    return load_yaml(PROPERTY_CONFIG)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    from retirement.modules.property import store

    conn = db.connect()
    store.migrate(conn)
    config = _config()
    # Starlette's current signature is (request, name, context); the old
    # (name, {"request": ...}) form silently misreads the first argument.
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "config": config,
            "shortlist": store.latest_shortlist(conn, 40),
            "last_run": _last_run,
            "modules": Profile.load().enabled_modules(),
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
    config = _config()
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
        conn = db.connect()
        module = REGISTRY["property"](_config(), conn)
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


@app.get("/healthz")
def healthz():
    return {"ok": True}
