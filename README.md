# retirement

A planning project for the two of us. Modular on purpose — property is the
first workstream, not the only one.

Three modules so far:

| Module | What it does |
|---|---|
| **property** | Pulls Italian listings, filters them against what we actually want, scores them for value, and emails a ranked shortlist. |
| **board** | Four columns, one card shape. What is done, what is next, and one written line saying what is blocking what. |
| **finance** | Net worth from the eMoney export: dated snapshots, the assets/liabilities split, and what the house would take out of it. |

And **Ask B** — the round B beside any section opens a panel that says, in
plain words and then verbatim, exactly what an AI is about to read, and runs
your prompt against it using the claude, grok or agy CLI already on the
machine.

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
| **Saved-search alerts** | works now, no key | The portals email you new matches; the listing URLs are pulled out and scored automatically. Their own mechanism — no approval, no scraping. |
| **Manual URLs** | works now, no key | Paste anything you find anywhere. It gets fetched, parsed, scored and ranked alongside everything else. |
| **RapidAPI (unofficial)** | works now, self-service key | A third-party wrapper around idealista. Listings today, but it scrapes the portal and breaks when the portal changes. |

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
| `./retire scan board` / `finance` | Run another module's cycle |

## How scoring works

"Best value" is not "cheapest". Each listing gets six 0–100 components, weighted
by `config/property.yaml`:

- **price vs market** — price per m² against the **Agenzia delle Entrate's OMI
  figures** for that comune when they have been imported, and against the stock
  currently listed in the search area when they have not. The label after each
  price says which. The stock is a small, self-selecting sample: if three
  overpriced trulli are listed this week the fourth looks like a bargain, and
  there is a test for exactly that. OMI is the recorded market value a notary
  and a mortgage valuer work from.
- **size per euro** — raw m² per euro, normalised across the run.
- **airport access** — straight-line distance to the nearest international
  airport × 1.3 for roads. A ranking signal, not a routing answer.
- **town character** — Claude reads the listing text against the brief in
  `prompt`, through the subscription CLI. Skipped when no backend is installed;
  everything else still scores.
- **condition** — move-in ready vs needs everything.
- **rental potential** — pool, historic centre, lake or sea view, terrace.

Every component is stored and shown, in the email and the UI, so a ranking can
be argued with. Hard filters (airport distance, photos, excluded phrases) knock
listings out entirely, but they land in the digest's "near misses" rather than
vanishing — a house 95 km from Bari that is otherwise perfect is a thing you
want to see, not a thing to silently drop.

Scores are **relative to each area's current stock**, so a 90 means good value
*for that area*. They are not comparable to a 90 in a different market.

## Ask B

Every section header carries a round **B**. It opens a drawer that first tells
you what that section's data actually contains, then — if you want to check —
shows the exact text that will be sent, before anything is sent. Then you pick
a backend and ask.

**The same layer does the scoring.** Town character, condition and rental
assessment, the plain-English brief parsing, and the board's summary sentence
all go through it too — so the nightly cycle runs on your subscription rather
than billing an API key. Nothing needs configuring: it finds what is installed.

This project runs on the **Antigravity (`agy`) subscription** —
`ASK_DEFAULT_PROVIDER=agy` pins it, so a missing `agy` is reported rather than
silently spending the trading desk's Claude login instead. Backends, in
preference order when nothing is pinned:

| Backend | How it authenticates |
|---|---|
| `agy` | `agy -p`, Gemini subscription — **what this project uses** |
| `claude_cli` | `claude -p`, the subscription login |
| `grok` | the Grok CLI, SuperGrok login |
| `anthropic_api` | `ANTHROPIC_API_KEY`, billed per call — last resort, for a machine with no CLI |

All three CLIs are agentic coding tools pointed at a working directory, so each
is invoked here with its file and shell tools explicitly disallowed and its cwd
set to `var/ask/` — "summarise this shortlist" must never turn into a commit.

**On login shells:** `zsh -lc` reads `.zshenv`, `.zprofile` and `.zlogin` but
**not** `.zshrc`, which is where most PATH edits actually live. So no login
shell is a reliable way to find a binary from a launchd job. Pinned absolute
paths are, which is why the deploy writes them.

If a backend shows as *not found* while it works fine from your Terminal, that
is PATH, not installation: npm, bun and Homebrew put these binaries where an
ssh or launchd shell never looks. The deploy resolves each one through a login
shell and pins the **absolute** path into `.env` (`ASK_AGY_BIN=...`), after
which it does not matter which shell asks. `./retire doctor` prints where each
was found.

If `agy` reports itself logged out, run `agy` with no arguments from a Terminal
**on the mini** — its credential lives in the console user's login Keychain,
which an ssh session cannot read. That is the same problem the trading desk
hits, and why these jobs are bootstrapped into `gui/<uid>`.

## Net worth

**eMoney has no client-side API.** It is licensed to advisory *firms* and
authenticated with an X.509 certificate the firm registers — there is no
individual tier, so a key would have to come from your advisor's firm. This
module therefore reads the balance-sheet / net-worth **export** from the client
portal: CSV, TSV or XLSX, drag it in and it parses.

The parser is tolerant rather than exact — it finds the header row by looking
for the columns it knows, ignores the export's own total rows in favour of
adding up the accounts itself, treats debt as debt whichever sign the export
uses, and reports anything it had to guess in `warnings` on the page. Every
import is a new dated snapshot, so the trend survives a bad export and a bad
import can be deleted without taking the history.

Balances stay in the local SQLite file. They leave the machine only when you
ask B a question with the finance scope selected, and the panel shows you
exactly what goes.

## Getting the export in without clicking Import

Two routes, both optional, both idempotent, both run by the nightly cycle:

**A folder.** Drop an export into `var/inbox/` — by hand, from a Downloads
rule, or via a synced folder from another machine — and the next cycle imports
it and files it into `processed/`.

**A mailbox.** If eMoney can email you a scheduled report, put `imap_*` in
`config/secrets.json`, set `pickup.mailbox.enabled` in `config/finance.yaml`,
and the attachments are pulled by IMAP. Use a dedicated mailbox, not your main
one.

Every file is fingerprinted by content hash **before** it is parsed, so the
same export arriving twice — re-dropped, re-sent, sitting in a folder that
syncs — makes one snapshot rather than two. That matters because the trend is
built from snapshots: a duplicated August would draw a flat month that never
happened. The same check covers the manual upload button, so dragging in a file
that was already picked up tells you so instead of double-counting it.

## Email

Configuration is deliberately the same shape as `trading-helper`'s
`email_service.py`: `config/secrets.json` first, then the environment, with the
same key names (`smtp_host`, `smtp_user`, `smtp_pass`, ...). Set SMTP up once
the same way in both projects, or point this one at the trading desk's file:

```bash
RETIREMENT_SECRETS=/Users/jambimac/repo/trading-helper/config/secrets.json
```

One file means one place to rotate a password. It also means this app can read
every secret the trading desk holds — which is why a separate file is the
default. `./retire doctor` prints what is configured, without printing any of
it.

## Alert emails as a feed

Set a saved search on each portal for each area, point the alerts at the
mailbox in `config/secrets.json`, and flip `alerts.enabled` in
`config/property.yaml`. Every cycle pulls the listing URLs out of whatever
arrived and queues them for scoring.

Two details that decide whether this works: portals wrap links in
click-tracking redirects, so the real URL is read out of the query string as
well as the body; and a message is only marked read once a URL has been taken
from it, so a format change shows up as unread mail rather than being silently
consumed. Anything that yields no links is reported in the run summary.

## RapidAPI (unofficial)

`rapidapi_key` in `config/secrets.json` switches it on. **Its response shape is
not verified** — RapidAPI does not publish one without a key, and shipping a
guessed JSON mapping is how you get a source that returns nothing and blames
the network. The normaliser reads defensively across the spellings these
wrappers use, and when it recognises nothing it says which keys it *did* get
and points here:

```bash
./retire probe rapidapi     # one real call, raw response written to var/
```

One round trip and the mapping can be made exact.

## Official market values (OMI)

The Agenzia delle Entrate publishes *Quotazioni Immobiliari* — €/m² purchase
bands for every micro-zone of every comune, revised twice a year. Importing
them turns "cheap compared to its neighbours" into "cheap compared to what this
zone is actually worth".

It is free but needs one registration: the file is not published at a URL that
can be fetched unattended. Download the current semester's **VALORI** export
from `telematici.agenziaentrate.gov.it`, then:

```bash
./retire omi ~/Downloads/QI_..._VALORI_....csv   # import
./retire omi                                     # what is loaded
```

Twice a year, when a new semester is published. Only residential types are kept
— garages, warehouses and shops are in the same file and averaging them in
would quietly drag every benchmark down. A comune's benchmark is the median of
its zones, with the spread across them kept so you can see how much the zone
matters.

Attribution is required where the figures appear: *Agenzia Entrate — OMI*. The
digest carries it.

**What it is not:** a valuation of any specific house. A band for a type of
property in a zone, which a house can legitimately sit outside. A listing far
below the band is a question, not a bargain.

## Value finder — scoring against the official record

The property module answers *is this cheap compared with the other things
listed near it*. The value module answers a harder question — *is this cheap
compared with what the Italian state records this place as being worth* — and
it does it on free, official data only. No paid API, no scraping.

```bash
./retire value coverage                      # what is loaded, and what is not
./retire value ispra --prov 074 --stats      # a whole province, free, no login
./retire value ispra 074005                  # or one comune
./retire value omi --scan                    # imports everything in data/omi/
./retire value score --lat 40.743 --lng 17.426 --price 180000 --size 120
```

The web page is `/value`: a coverage strip, a single-property scorer with the
full breakdown, and an upload box for a CSV or GeoJSON of your own listings.
`GET /api/value/coverage`, `POST /api/value/score`, `GET /api/value/search`,
`/api/value/zones` and `/api/value/stats/comune/{code}` are the same thing
without the HTML.

**Six components, visible weights**: price against the OMI band (30), gross
rental yield from the OMI rent band (20), demand and demographics (20),
market liquidity from NTN (10), amenities from OpenStreetMap (10), minus a
hazard penalty of up to 25 from ISPRA's flood and landslide mosaics and the
seismic classification.

The nightly cycle scores every listing twice: the fit score you already had,
ranked against what else is listed nearby, and the value score, ranked against
the official record. Both appear on the Property card and in the digest, and
neither is folded into the other — they answer different questions.

**A component with no data is reported as "no data", never as zero.** Its
weight is redistributed over the components that do have data, and the answer
carries a `confidence` figure — the share of the intended weight that actually
had something behind it — plus a caveat naming what was dropped. A score of 33
at 22% confidence is not the same claim as 33 at 90%, and the app never lets
the two look alike.

Every figure carries its source, because most of these licences require it:
`Agenzia Entrate — OMI`, `ISPRA — IdroGEO`, `ISTAT`, `MEF — Dipartimento delle
Finanze`, `DPC / INGV`, `© OpenStreetMap contributors`. Where each file comes
from, what it costs (nothing), how often it changes and which ones need a
manual download is written out in **[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)**.

The **B** button on that page reads the same data — coverage, bands, indicators
and every score breakdown including the gaps — so a question about a place is
answered from the loaded record rather than from the model's memory of Italy.

## Configuration

`config/profile.yaml` — household-level, shared by every module, and where
future modules get switched on.

`config/board.yaml` — columns, tags, the trip date the board counts down to.

`config/finance.yaml` — currency, how account names map to buckets, which words
mean debt, and the purchase figure the "what the house would take" panel uses.

`config/property.yaml` — everything about the search: the plain-English
`prompt`, budget, search areas, scoring weights, hard filters, digest settings.
The web UI writes back to this file, so edit it in either place.

Each search area costs API calls on every run. Three areas is 3–6 calls per
cycle; see the budget note in `scripts/com.jambi.retirement-scan.plist` before
adding more.

## Running on the mini

First time and every time after, one command:

```bash
./scripts/ship.sh          # push, deploy, route the tunnel, health-check
./scripts/ship.sh --check  # report state, change nothing
```

It is idempotent — rerun it after any failure. On a first run it clones to the
mini, sets up the venv and bootstraps three LaunchAgents (web, nightly cycle,
tunnel); after that it is a push-and-restart.

This project runs **its own cloudflared tunnel**, separate from the trading
desk's. The trading tunnel's ingress list is a file tracked in that repo, so
adding a hostname to it would break that repo's `git pull --ff-only` and couple
two unrelated projects' restarts. A tunnel is free; isolation is worth more.
See `scripts/cloudflare_setup.md`.

Underneath it is the same loop as `trading-helper`: work on the MacBook, ship
with a script.

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
  core/          config · sqlite · email · llm · ask (the B backends) · context · Module base
  modules/
    property/    sources/ · models · score · store · digest · pipeline
    board/       store · summary · pipeline
    finance/     importer · store · pipeline
  web/           FastAPI UI — base · property · board · finance templates
config/          profile.yaml · property.yaml · board.yaml · finance.yaml
scripts/         deploy_mini.sh · LaunchAgents · cloudflare_setup.md
```

## Adding the next module

Subclass `Module`, implement `migrate()` and `run()`, decorate with
`@register("name")`, import it in `core/module.py:load_registry`, and add it to
`config/profile.yaml`. It inherits the CLI, the database, the scheduler and the
email path. `board` and `finance` are the worked examples. Still sketched in
`profile.yaml`: healthcare coverage comparison and residency pathway tracking.
