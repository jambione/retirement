"""Optional Anthropic layer.

Two jobs:
  1. turn the plain-English `prompt` in the config into structured filters;
  2. judge a listing's own words against the parts of the brief that are not
     numeric -- community feel, authenticity, whether it reads like a rental.

Everything here degrades to a no-op when ANTHROPIC_API_KEY is unset, so the
pipeline still runs (and still scores on the numeric signals) without it.
"""
from __future__ import annotations

import json
import re
from typing import Any

from retirement.core.config import env

_CLIENT = None


def available() -> bool:
    return bool(env("ANTHROPIC_API_KEY"))


def _client():
    global _CLIENT
    if _CLIENT is None:
        import anthropic

        _CLIENT = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    return _CLIENT


def _model() -> str:
    return env("RETIREMENT_LLM_MODEL", "claude-sonnet-4-5") or "claude-sonnet-4-5"


def _ask(system: str, user: str, max_tokens: int = 1200) -> str:
    msg = _client().messages.create(
        model=_model(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in msg.content if block.type == "text")


def _extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


PARSE_SYSTEM = """You convert a house-hunting brief into structured search hints.
Return ONLY a JSON object with these keys:
  must_have:      list of short phrases the property should have
  nice_to_have:   list of short phrases
  deal_breakers:  list of short phrases
  character:      one sentence describing the kind of town and house wanted
Do not invent budgets, sizes or locations that the brief does not state."""


def parse_prompt(prompt: str) -> dict[str, Any]:
    """Plain English brief -> structured hints. Returns {} if unavailable."""
    if not available() or not prompt.strip():
        return {}
    try:
        return _extract_json(_ask(PARSE_SYSTEM, prompt.strip(), max_tokens=800))
    except Exception:
        return {}


ASSESS_SYSTEM = """You assess Italian property listings against a buyer's brief.
For each listing return an object with:
  id:               the listing id you were given
  character_score:  0-100, how well the town and house match the brief's feel
  condition_score:  0-100, 100 = move-in ready, 0 = full structural renovation
  rental_score:     0-100, likely short-let demand given location and features
  note:             one sentence, max 25 words, the single most useful thing
  concerns:         list of short phrases, or []
Return ONLY a JSON object {"assessments": [...]}. Judge only from the text you
are given. If the text is too thin to judge, use 50 and say so in the note."""


def assess_listings(brief: str, listings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Returns {listing_id: assessment}. Empty dict when unavailable."""
    if not available() or not listings:
        return {}
    payload = [
        {
            "id": item["id"],
            "title": (item.get("title") or "")[:200],
            "municipality": item.get("municipality"),
            "province": item.get("province"),
            "price_eur": item.get("price"),
            "size_sqm": item.get("size_sqm"),
            "rooms": item.get("rooms"),
            "status": item.get("condition"),
            "description": (item.get("description") or "")[:1500],
        }
        for item in listings
    ]
    user = (
        f"BUYER'S BRIEF:\n{brief.strip()}\n\n"
        f"LISTINGS:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        data = _extract_json(_ask(ASSESS_SYSTEM, user, max_tokens=4000))
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in data.get("assessments", []):
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"])] = item
    return out
