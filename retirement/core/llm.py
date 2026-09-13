"""The structured-AI layer: prompts in, parsed JSON out.

It owns NO credentials and knows nothing about any vendor. Every call goes
through `ask`, the same provider layer the B button uses, so the scoring and
the board summary run on whichever subscription CLI is installed on the
machine -- `claude -p`, `grok`, `agy` -- rather than billing an API key.

That was the bug this replaced: the B button used the subscription, and the
nightly scoring quietly used the Anthropic SDK. Same models, two credential
paths, and the one that cost money was the one running unattended at 07:15.

Everything degrades to a no-op when no backend is installed, so the pipeline
still runs and still scores on the numeric signals.
"""
from __future__ import annotations

import json
import re
from typing import Any

# A CLI is a process, not an HTTP call: assessment of a batch is tens of
# seconds, not hundreds of milliseconds. Generous, but bounded.
ASSESS_TIMEOUT = 300.0
PARSE_TIMEOUT = 120.0


def available() -> bool:
    """True when ANY backend can answer — CLI or API key."""
    from retirement.core import ask

    return ask.default_provider() is not None


def backend() -> str | None:
    from retirement.core import ask

    return ask.default_provider()


def _ask(system: str, user: str, max_tokens: int = 1200, timeout: float | None = None) -> str:
    """One turn. The CLIs take a single prompt, so the system framing is folded
    into it rather than passed separately."""
    from retirement.core import ask

    _ = max_tokens  # the CLIs size their own output
    result = ask.ask(f"{system}\n\n{user}", timeout=timeout or PARSE_TIMEOUT)
    return result["answer"]


def _extract_json(text: str) -> dict[str, Any]:
    """CLIs wrap answers in prose more often than the API does, so take the
    outermost JSON object rather than trusting the whole response."""
    match = re.search(r"\{.*\}", text or "", re.S)
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
No preamble, no explanation, no code fence. Do not invent budgets, sizes or
locations that the brief does not state."""


def parse_prompt(prompt: str) -> dict[str, Any]:
    if not available() or not prompt.strip():
        return {}
    try:
        return _extract_json(_ask(PARSE_SYSTEM, prompt.strip(), timeout=PARSE_TIMEOUT))
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
Return ONLY a JSON object {"assessments": [...]}. No preamble, no code fence.
Judge only from the text you are given. If the text is too thin to judge, use
50 and say so in the note."""


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
        data = _extract_json(_ask(ASSESS_SYSTEM, user, timeout=ASSESS_TIMEOUT))
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in data.get("assessments", []):
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"])] = item
    return out
