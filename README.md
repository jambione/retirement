# retirement

A planning project for the two of us. Modular on purpose — property is the
first workstream, not the only one.

**Module 1: property.** Pull Italian listings, filter them against what we
actually want, score them for value, and email a ranked shortlist. Built to
answer one question before next spring: *which towns are worth a week of our
time, and what does the money buy there?*

---

## The honest part first

**Italy has no MLS.** There is no broker feed to subscribe to, no equivalent of
what a US agent plugs into. Listings live in walled-garden portals —
Immobiliare.it, Idealista.it, Casa.it, Gate-away — and the same house appears
on three of them at two different prices with a fourth price when you walk into
the agency in person.

What this project uses instead:

| Source | Status | Notes |
|---|---|---|
| **Idealista Search API** | official, needs a key | The one major Italian portal with a real developer API. Small free allowance. [Request access](https://developers.idealista.com/access-request) |
| **Manual URLs** | works now, no key | Paste anything you find anywhere. It gets fetched, parsed, scored and ranked alongside the API results. |

The design consequence: the pipeline is source-agnostic. Adding a portal later
means writing one `fetch()` method in `retirement/modules/property/sources/` —
scoring, storage, the digest and the UI do not change.

**Expect coverage gaps.** Rural southern Italian listings under €150k are
disproportionately agency-only and never touch a portal API. This tool narrows
the field and tells you where to spend your attention. It does not replace
walking into an agency in Cisternino and asking what they have not listed.

---

## Quick start

```bash
git clone git@github.com:jambione/retirement.git
cd retirement
./retire setup                 # .venv + deps + .env from the example
$EDITOR .env                   # Idealista key, SMTP, optionally Anthropic
./retire scan --dry-run        # one cycle, scores, no email
./retire start                 # web UI on http://127.0.0.1:8891
```

Without an Idealista key it still runs — on manually added URLs only, and it
says so in the run summary rather than failing.

## Commands

| Command | What it does |
|---|---|
| `./retire setup` | Create `.venv`, install, seed `.env` |
| `./retire start` / `stop` / `restart` / `status` | The web UI |
| `./retire scan [--dry-run]` | One pipeline cycle now |
| `./retire list` | Current shortlist in the terminal |
| `retirement add <url>` | Queue a listing found elsewhere |
| `retirement digest --preview` | Render the email to `var/` without sending |

## How scoring works

"Best value" is not "cheapest". Each listing gets six 0–100 components, weighted
by `config/property.yaml`:

- **price vs area median** — price per m² against the stock currently listed in
  that same search area. This is the core of it: a €280k house is good value in
  Locorotondo and a bargain on Lake Garda, and one number cannot say both.
- **size per euro** — raw m² per euro, normalised across the run.
- **airport access** — straight-line distance to the nearest international
  airport × 1.3 for roads. A ranking signal, not a routing answer.
- **town character** — Claude reads the listing text against the brief in
  `prompt`. Skipped without `ANTHROPIC_API_KEY`; everything else still scores.
- **condition** — move-in ready vs needs everything.
- **rental potential** — pool, historic centre, lake or sea view, terrace.

Every component is stored and shown, in the email and the UI, so a ranking can
be argued with. Hard filters (airport distance, photos, excluded phrases) knock
listings out entirely, but they land in the digest's "near misses" rather than
vanishing — a house 95 km from Bari that is otherwise perfect is a thing you
want to see, not a thing to silently drop.

Scores are **relative to each area's current stock**, so a 90 means good value
*for that area*. They are not comparable to a 90 in a different market.

## Configuration

`config/profile.yaml` — household-level, shared by every module, and where
future modules get switched on.

`config/property.yaml` — everything about the search: the plain-English
`prompt`, budget, search areas, scoring weights, hard filters, digest settings.
The web UI writes back to this file, so edit it in either place.

Each search area costs API calls on every run. Three areas is 3–6 calls per
cycle; see the budget note in `scripts/com.jambi.retirement-scan.plist` before
adding more.

## Running on the mini

Same loop as `trading-helper`: work on the MacBook, ship with a script.

```bash
./scripts/deploy_mini.sh              # push + pull on mini + restart
./scripts/deploy_mini.sh --pull-only  # config-only changes
./scripts/deploy_mini.sh --status     # remote health
```

On the mini, once: `scripts/install_agents.sh` bootstraps two LaunchAgents into
`gui/<uid>` — the web UI (kept alive) and the 07:15 scan. Same GUI-domain
reasoning as `com.jambi.trading-desk`; the plists explain it.

Public hostname: `scripts/cloudflare_setup.md`. Short version — add an ingress
rule to the tunnel you already run, route the DNS name to that same tunnel, and
**put Cloudflare Access in front of it first**. There is no login in this app.

## Layout

```
retirement/
  core/          config, sqlite, email, the optional Claude layer, Module base
  modules/
    property/    sources/ · models · normalise · score · store · digest · pipeline
  web/           FastAPI UI
config/          profile.yaml · property.yaml
scripts/         deploy_mini.sh · LaunchAgents · cloudflare_setup.md
```

## Adding the next module

Subclass `Module`, implement `migrate()` and `run()`, decorate with
`@register("name")`, import it in `core/module.py:load_registry`, and add it to
`config/profile.yaml`. It inherits the CLI, the database, the scheduler and the
email path. Candidates already sketched in `profile.yaml`: drawdown and tax
modelling, healthcare coverage comparison, residency pathway tracking.
