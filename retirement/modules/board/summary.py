"""The line at the top of the board.

The numbers are always computed. The sentence is the part worth reading, and
it says what is BLOCKING -- not a restatement of the counts, which are already
on screen two inches away.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any

from retirement.modules.board import store


def _parse(value: str | None) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else None
    except ValueError:
        return None


def due_soon(conn: sqlite3.Connection, within_days: int, terminal: str = "done") -> list[dict]:
    today = date.today()
    out = []
    for row in conn.execute(
        "SELECT * FROM cards WHERE due IS NOT NULL AND column_id != ? ORDER BY due", (terminal,)
    ):
        due = _parse(row["due"])
        if due and (due - today).days <= within_days:
            item = dict(row)
            item["days_left"] = (due - today).days
            out.append(item)
    return out


def build(conn: sqlite3.Connection, config: dict[str, Any]) -> dict[str, Any]:
    terminal = next(
        (c["id"] for c in config.get("columns", []) if c.get("terminal")), "done"
    )
    window = int((config.get("summary") or {}).get("due_soon_days", 45))
    cards = store.by_column(conn)
    numbers = store.counts(conn, terminal)
    soon = due_soon(conn, window, terminal)

    target = _parse(str((config.get("target") or {}).get("date") or ""))
    days_to_target = (target - date.today()).days if target else None

    return {
        "counts": numbers,
        "due_soon": soon,
        "overdue": [c for c in soon if c["days_left"] < 0],
        "target_label": (config.get("target") or {}).get("label", ""),
        "days_to_target": days_to_target,
        "sentence": _sentence(cards, soon, config),
    }


def _sentence(cards: dict[str, list[dict]], soon: list[dict], config: dict[str, Any]) -> str:
    from retirement.core import llm

    in_progress = cards.get("doing", [])
    if llm.available():
        written = _ask_claude(cards, soon, config)
        if written:
            return written

    # Fallback: no invention, just the most useful ordering of what is there.
    if soon:
        first = soon[0]
        when = "overdue" if first["days_left"] < 0 else f"due in {first['days_left']} days"
        return f"Nearest deadline: {first['title']} ({when})."
    if in_progress:
        return f"In flight: " + "; ".join(c["title"] for c in in_progress[:2]) + "."
    return "Nothing is blocked and nothing is due. Good time to add what comes next."


def _ask_claude(cards: dict[str, list[dict]], soon: list[dict], config: dict[str, Any]) -> str:
    from retirement.core import llm

    payload = {
        column: [
            {"title": c["title"], "tag": c["tag"], "due": c["due"], "notes": c["notes"][:200]}
            for c in items
        ]
        for column, items in cards.items()
    }
    system = (
        "You summarise a small personal planning board in ONE short paragraph, "
        "at most 45 words. Say what is blocking what, and what deserves attention "
        "next. Do not restate counts -- the numbers are already on screen. Do not "
        "invent tasks or dates. Plain sentences, no bullet points, no preamble."
    )
    user = (
        f"Goal: {(config.get('target') or {}).get('label', 'a move abroad')}.\n"
        f"Board by column: {payload}\n"
        f"Due soon: {[(c['title'], c['days_left']) for c in soon]}"
    )
    try:
        return llm._ask(system, user, max_tokens=200).strip()
    except Exception:
        return ""
