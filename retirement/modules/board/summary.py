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


SUMMARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS board_summaries (
    fingerprint TEXT PRIMARY KEY,
    sentence    TEXT NOT NULL,
    made_at     TEXT NOT NULL
);
"""


def fingerprint(cards: dict[str, list[dict]]) -> str:
    """What the sentence is about. Two boards with the same cards in the same
    columns deserve the same sentence, and asking a model to write it twice is
    six seconds and a subscription call spent on an answer we already have."""
    import hashlib

    material = "|".join(
        f"{column}:{c.get('title','')}:{c.get('due') or ''}:{(c.get('notes') or '')[:120]}"
        for column in sorted(cards)
        for c in cards[column]
    )
    return hashlib.sha1(material.encode("utf-8")).hexdigest()


def cached_sentence(conn: sqlite3.Connection, mark: str) -> str:
    conn.executescript(SUMMARY_SCHEMA)
    row = conn.execute(
        "SELECT sentence FROM board_summaries WHERE fingerprint = ?", (mark,)
    ).fetchone()
    return row["sentence"] if row else ""


def remember_sentence(conn: sqlite3.Connection, mark: str, sentence: str) -> None:
    from retirement.core.db import utcnow

    conn.executescript(SUMMARY_SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO board_summaries (fingerprint, sentence, made_at) VALUES (?,?,?)",
        (mark, sentence, utcnow()),
    )
    # One row per board state is enough history; the rest is clutter.
    conn.execute(
        "DELETE FROM board_summaries WHERE fingerprint NOT IN "
        "(SELECT fingerprint FROM board_summaries ORDER BY made_at DESC LIMIT 20)"
    )
    conn.commit()


def write_sentence(conn: sqlite3.Connection, config: dict[str, Any]) -> dict[str, Any]:
    """Ask the model, once, for this board state. Called by /board/summary after
    the page has already rendered -- never in the request that draws it."""
    cards = store.by_column(conn)
    mark = fingerprint(cards)
    hit = cached_sentence(conn, mark)
    if hit:
        return {"sentence": hit, "cached": True}

    terminal = next((c["id"] for c in config.get("columns", []) if c.get("terminal")), "done")
    window = int((config.get("summary") or {}).get("due_soon_days", 45))
    written = _ask_claude(cards, due_soon(conn, window, terminal), config)
    if written:
        remember_sentence(conn, mark, written)
    return {"sentence": written, "cached": False}


def build(conn: sqlite3.Connection, config: dict[str, Any],
          cards: dict[str, list[dict]] | None = None) -> dict[str, Any]:
    terminal = next(
        (c["id"] for c in config.get("columns", []) if c.get("terminal")), "done"
    )
    window = int((config.get("summary") or {}).get("due_soon_days", 45))
    cards = cards if cards is not None else store.by_column(conn)
    numbers = store.counts(conn, terminal)
    soon = due_soon(conn, window, terminal)

    target = _parse(str((config.get("target") or {}).get("date") or ""))
    days_to_target = (target - date.today()).days if target else None

    from retirement.core import llm

    sentence = cached_sentence(conn, fingerprint(cards))
    pending = not sentence and llm.available()
    if not sentence:
        sentence = _plain(cards, soon)

    return {
        "counts": numbers,
        "due_soon": soon,
        "overdue": [c for c in soon if c["days_left"] < 0],
        "target_label": (config.get("target") or {}).get("label", ""),
        "days_to_target": days_to_target,
        "sentence": sentence,
        # True when the model has not written one for this board state yet. The
        # page renders the deterministic line immediately and asks for the
        # written one afterwards, so nothing waits on a subprocess.
        "sentence_pending": pending,
    }


def _plain(cards: dict[str, list[dict]], soon: list[dict]) -> str:
    """No model, no invention: the most useful ordering of what is there. This
    is what the page shows while a written sentence is being fetched, and what
    it keeps showing when no model is installed."""
    in_progress = cards.get("doing", [])
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
