"""Command line entry point: `retirement <command>`."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from retirement.core import db
from retirement.core.config import Profile, load_dotenv, load_yaml
from retirement.core.module import REGISTRY, load_registry


def _setup(verbose: bool = False) -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    load_registry()


def _module(name: str, conn):
    profile = Profile.load()
    enabled = profile.enabled_modules()
    if name not in enabled:
        raise SystemExit(f"module '{name}' is not enabled in config/profile.yaml")
    if name not in REGISTRY:
        raise SystemExit(f"module '{name}' has no implementation registered")
    config = load_yaml(enabled[name]["config"])
    return REGISTRY[name](config, conn)


def cmd_run(args) -> int:
    conn = db.connect()
    names = (
        list(Profile.load().enabled_modules())
        if args.module == "all" else [args.module]
    )
    summaries = {}
    for name in names:
        if name not in REGISTRY:
            print(f"warning: module '{name}' has no implementation", file=sys.stderr)
            continue
        summaries[name] = _module(name, conn).run(dry_run=args.dry_run)
    print(json.dumps(summaries if len(summaries) != 1 else next(iter(summaries.values())),
                     indent=2, default=str))
    for summary in summaries.values():
        for warning in summary.get("warnings", []):
            print(f"warning: {warning}", file=sys.stderr)
    return 0


def cmd_doctor(args) -> int:
    """What is configured on this machine, without printing a single secret."""
    from retirement.core import ask, notify

    conn = db.connect()
    print(f"database   {db.db_path()}")
    print(f"idealista  {db.usage_this_month(conn, 'idealista')} calls used this month")

    status = notify.status()
    print(f"secrets    {status['secrets_file']}")
    print("email      " + (
        f"ready — {status['smtp_user']}@{status['smtp_host']}:{status['smtp_port']} "
        f"-> {', '.join(status['digest_to'])}"
        if status["configured"] else "NOT configured (smtp_host / smtp_user / smtp_pass / digest_to)"
    ))

    for entry in ask.available():
        mark = "✓" if entry["ready"] else "·"
        where = entry.get("path") or ("" if entry["ready"] else "not found")
        print(f"ai         {mark} {entry['label']:<18} {where}")
    print(f"           default: {ask.default_provider() or 'none available'}")
    note = ask.preference_note()
    if note:
        print(f"           ! {note}")
    print("scoring    " + (
        f"listing assessment and the board summary run on {ask.default_provider()}"
        if ask.default_provider() else
        "NO backend — listings score on numeric signals only, board summary is deterministic"
    ))

    import subprocess
    from retirement.core.config import project_root

    cfg = project_root() / "config" / "cloudflared-config.yml"
    if cfg.exists():
        tunnel_id = ""
        for line in cfg.read_text(encoding="utf-8").splitlines():
            if line.startswith("tunnel:"):
                tunnel_id = line.split(":", 1)[1].strip()
        running = subprocess.run(["pgrep", "-f", "cloudflared.*retirement"],
                                 capture_output=True, text=True).stdout.strip()
        print(f"tunnel     {'✓ running' if running else '✗ NOT running'}  {tunnel_id}")
        if not running:
            print("           logs/tunnel.log says why; Cloudflare shows 1033 while it is down")
    else:
        print("tunnel     not set up here (scripts/tunnel_setup.sh)")

    for name in Profile.load().enabled_modules():
        print(f"module     {name}")
    return 0


def cmd_list(args) -> int:
    conn = db.connect()
    from retirement.modules.property import store

    for i, item in enumerate(store.latest_shortlist(conn, args.limit), 1):
        detail = item.get("detail") or {}
        price = f"€{item['price']:,.0f}".replace(",", ".") if item.get("price") else "n/a"
        print(
            f"{i:>2}. [{detail.get('total', 0):>5.1f}] {price:>10}  "
            f"{(item.get('municipality') or '')[:22]:<22} {item.get('url', '')}"
        )
    return 0


def cmd_add(args) -> int:
    conn = db.connect()
    from retirement.modules.property.sources.manual import ManualSource

    source = ManualSource(conn, {})
    for url in args.urls:
        source.add(url, args.area)
        print(f"queued {url}")
    return 0


def cmd_digest(args) -> int:
    """Re-send the last shortlist without re-fetching anything."""
    conn = db.connect()
    from retirement.modules.property import digest, store
    from retirement.core import notify

    shortlist = store.latest_shortlist(conn, args.limit)
    subject, html, text = digest.render(shortlist, [], {"scanned": len(shortlist)})
    if args.preview:
        out = "var/digest-preview.html"
        from retirement.core.config import project_root

        path = project_root() / out
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        print(f"wrote {path}")
        return 0
    notify.send(subject, html, text)
    print(f"sent: {subject}")
    return 0


def cmd_email_test(args) -> int:
    """Prove the path end to end.

    `doctor` says the credentials are PRESENT; only a real send says they WORK.
    An app-specific password that has been revoked, a port the mini's network
    blocks, a from-address the provider will not accept -- none of those show
    up until something tries.
    """
    from retirement.core import notify

    status = notify.status()
    if not status["configured"]:
        print("✗ email is not configured.")
        print(f"  secrets file: {status['secrets_file']}")
        for key, value in (("smtp_host", status["smtp_host"]),
                           ("smtp_user", status["smtp_user"]),
                           ("smtp_pass", "set" if status["has_password"] else None),
                           ("digest_to", ", ".join(status["digest_to"]) or None)):
            print(f"    {key:<10} {value or 'MISSING'}")
        return 1

    to = args.to or ", ".join(status["digest_to"])
    print(f"sending a test message to {to}")
    print(f"  via {status['smtp_user']}@{status['smtp_host']}:{status['smtp_port']}")

    html = (
        '<div style="font-family:-apple-system,Helvetica,sans-serif;max-width:520px">'
        '<p style="font-size:19px;margin:0 0 10px">Email works.</p>'
        '<p style="color:#6b7280;font-size:13.5px;line-height:1.55;margin:0">'
        "This is a test from the retirement project on the mini. If you are reading it, "
        "the nightly digest can reach you. Nothing else was sent."
        "</p></div>"
    )
    try:
        notify.send("Retirement — test message", html,
                    "Email works. This is a test from the retirement project.")
    except Exception as exc:
        print(f"✗ send failed: {exc}")
        print("  App-specific password revoked? Port blocked? From-address rejected?")
        return 1
    print("✓ sent — check the inbox")
    return 0


def cmd_omi(args) -> int:
    """Import an Agenzia delle Entrate OMI VALORI export."""
    from pathlib import Path as _Path

    from retirement.modules.property import omi

    conn = db.connect()
    if not args.file:
        state = omi.status(conn)
        if not state["imported"]:
            print("No OMI data imported yet.")
            print("  Download the current semester's VALORI file from")
            print("  telematici.agenziaentrate.gov.it (free, one registration), then:")
            print("    ./retire omi ~/Downloads/QI_..._VALORI_....csv")
            return 1
        for row in state["files"]:
            print(f"{row['semester'] or '?':<8} {row['comuni']:>6,} comuni  "
                  f"{row['rows']:>8,} rows  {row['filename']}")
        return 0

    path = _Path(args.file).expanduser()
    if not path.exists():
        print(f"✗ no such file: {path}")
        return 1
    try:
        result = omi.import_file(conn, path.read_bytes(), path.name)
    except ValueError as exc:
        print(f"✗ {exc}")
        return 1
    print(f"✓ imported {result['rows']:,} residential rows across "
          f"{result['comuni']:,} comuni (semester {result['semester'] or 'unknown'})")
    print("  Source: Agenzia Entrate — OMI. Scoring now benchmarks against it.")
    return 0


def cmd_ai_test(args) -> int:
    """One trivial prompt through the chosen backend, with the exact command
    and the raw error if it fails. `doctor` says a CLI is installed; this says
    whether it answers."""
    from retirement.core import ask

    provider = args.provider or ask.default_provider()
    if not provider:
        print("✗ no AI backend available. `./retire doctor` shows where it looked.")
        return 1

    entry = next((e for e in ask.available() if e["id"] == provider), None)
    print(f"backend  {provider}" + (f"  ({entry.get('path')})" if entry and entry.get("path") else ""))
    note = ask.preference_note()
    if note:
        print(f"         ! {note}")

    try:
        result = ask.ask("Reply with exactly: OK", provider=provider, timeout=90)
    except Exception as exc:
        print("✗ the call failed:\n")
        for line in str(exc).splitlines():
            print(f"    {line}")
        binary = (entry or {}).get("path") or provider
        message = str(exc).lower()

        if "model" in message:
            print(f"\n  Unknown model id. Ask the CLI what it has:  {binary} models")
            print(f"  Then set ASK_{provider.split('_')[0].upper()}_MODEL in .env to one of them,")
            print("  or leave it unset and the CLI uses its own default.")
        elif any(word in message for word in ("auth", "logged in", "sign in", "login")):
            print(f"\n  Not logged in. Run `{binary}` with no arguments from a Terminal")
            print("  ON the mini — the credential is in that user's login Keychain.")
            print("\n  NOTE: running this over ssh cannot read that Keychain either, so a")
            print("  login error here is not proof the SERVICE is logged out. The agents")
            print("  run in gui/<uid>, which can. The honest test is the B button on the")
            print("  site itself.")
        else:
            print(f"\n  The CLI did not say much. Try it by hand to see the full output:")
            print(f"      {binary} -p 'Reply with exactly: OK'")
            print(f"      {binary} models        # if it is a model-id problem")
            print("  And check it is logged in from a Terminal on the mini.")
        return 1

    print(f"✓ answered in {result['seconds']}s: {result['answer'][:200]}")
    return 0


def cmd_probe(args) -> int:
    """Make one real call to a source and write the raw response out.

    For a source whose response shape is not published, this is the difference
    between correcting a field mapping in one round trip and guessing at it."""
    import json as _json
    from pathlib import Path as _Path

    from retirement.core.config import load_yaml, project_root
    from retirement.modules.property.sources.rapidapi import RapidApiSource, find_elements

    if args.source != "rapidapi":
        print(f"no probe for '{args.source}'")
        return 1

    config = load_yaml("config/property.yaml")
    source = RapidApiSource(db.connect(), config)
    if not source.available():
        print("✗ no rapidapi_key set.")
        print("  1. rapidapi.com — sign up (free)")
        print("  2. subscribe to the 'Idealista' API by apidojo, Basic/free plan")
        print("  3. copy the X-RapidAPI-Key from its dashboard, then either:")
        print('       config/secrets.json:  "rapidapi_key": "..."')
        print("       .env:                 RAPIDAPI_KEY=...")
        print("  4. rerun this. It makes ONE read-only call and writes the raw")
        print("     response to var/, which is what the field mapping needs.")
        return 1

    area = (config.get("areas") or [{}])[0]
    print(f"calling {source._host()} for area '{area.get('id', '?')}'")
    try:
        payload = source.request(area)
    except Exception as exc:
        print(f"✗ request failed: {exc}")
        return 1

    out = project_root() / "var" / "rapidapi-probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(payload, indent=2, ensure_ascii=False)[:2_000_000],
                   encoding="utf-8")
    print(f"✓ wrote {out}")

    if isinstance(payload, dict):
        print(f"  top-level keys: {', '.join(sorted(payload)[:14])}")
    try:
        elements = find_elements(payload)
        print(f"  found {len(elements)} listings")
        if elements:
            print(f"  first listing's fields: {', '.join(sorted(elements[0])[:20])}")
    except Exception as exc:
        print(f"  ! {exc}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("retirement.web.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retirement", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run one module cycle")
    p_run.add_argument("module", nargs="?", default="property",
                       help="module name, or 'all' for every enabled module")
    p_run.add_argument("--dry-run", action="store_true", help="score but do not email")
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list", help="print the current shortlist")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=cmd_list)

    p_add = sub.add_parser("add", help="queue listing URLs found elsewhere")
    p_add.add_argument("urls", nargs="+")
    p_add.add_argument("--area", default="manual")
    p_add.set_defaults(func=cmd_add)

    p_digest = sub.add_parser("digest", help="re-send or preview the last shortlist")
    p_digest.add_argument("--limit", type=int, default=12)
    p_digest.add_argument("--preview", action="store_true", help="write HTML to var/ instead")
    p_digest.set_defaults(func=cmd_digest)

    p_serve = sub.add_parser("serve", help="run the local web UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8891)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_doctor = sub.add_parser("doctor", help="what is configured on this machine")
    p_doctor.set_defaults(func=cmd_doctor)

    p_omi = sub.add_parser("omi", help="import official OMI market values, or show what is loaded")
    p_omi.add_argument("file", nargs="?", default="", help="path to a ..._VALORI_....csv")
    p_omi.set_defaults(func=cmd_omi)

    p_ai = sub.add_parser("ai-test", help="send one prompt to the AI backend and show the result")
    p_ai.add_argument("--provider", default="", help="agy | claude_cli | grok | anthropic_api")
    p_ai.set_defaults(func=cmd_ai_test)

    p_probe = sub.add_parser("probe", help="call a source once and dump the raw response")
    p_probe.add_argument("source", nargs="?", default="rapidapi")
    p_probe.set_defaults(func=cmd_probe)

    p_email = sub.add_parser("email-test", help="send one real message to prove the path")
    p_email.add_argument("--to", default="", help="override the configured recipient")
    p_email.set_defaults(func=cmd_email_test)

    args = parser.parse_args(argv)
    _setup(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
