# Data sources

Every number this app shows about an Italian property comes from one of the
sources below. All of them are free. None of them is scraped from a portal, and
none needs a paid API key.

Three things are recorded for each: **what it gives you**, **how to get it** —
a URL where one exists, a click-path where the file sits behind a login — and
the **attribution** its licence requires. Attribution is not decoration here:
OMI and ISPRA both require it, so the strings below travel inside the API
responses (`attribution`, and `source` on every score component), not just in
the page footer.

Reachability was checked on **2026-09-13**. Where a URL is marked *verified*, it
answered on that date from this machine; where it is marked *click-path*, the
file is behind a login or a per-release URL that rots, and the app imports it
from a drop folder instead of pretending it can fetch it.

---

## 1. Agenzia delle Entrate — OMI (Quotazioni Immobiliari)

**What it gives you.** For every OMI micro-zone of every comune, twice a year: a
min/max €/m² band for buying and a min/max €/m²/**month** band for renting, by
typology (abitazioni civili, ville e villini, …) and by condition (normale,
ottimo, scadente). This is the anchor of the whole value model — the difference
between "cheap compared to the other things listed this week" and "cheap
compared to what this zone is worth".

**Also published:** NTN (*numero di transazioni normalizzate*) — how many sales
actually happened, per comune, per year. That is the only honest liquidity
signal available for free, and the score leaves its component empty until you
load it rather than substituting population for it.

**How to get it — click-path.** The full CSV set is free but sits behind a
Fisconline/Entratel registration at `telematici.agenziaentrate.gov.it`:
*Servizi → Consultazioni e ricerca → Banca dati quotazioni immobiliari →
Scarico dati*. You want, per semester, the pair:

| File | What it is |
|---|---|
| `QI_<n>_<yyyys>_VALORI_<enc>.csv` | the bands — this is the one the app parses |
| `QI_<n>_<yyyys>_ZONE_<enc>.csv` | zone descriptions and microzone metadata |

The public consultation at <https://www1.agenziaentrate.gov.it/servizi/Consultazione/ricerca.htm>
shows the last six semesters a zone at a time, without a login — useful for
checking one address, useless for bulk.

**Zone polygons — click-path.** The geometry is published through **Geopoi**
(<https://www.geopoi.it/>), which serves the OMI zone boundaries as KML per
comune. Community mirrors exist: **ondata/quotazioni-immobiliari-agenzia-entrate**
(<https://github.com/ondata/quotazioni-immobiliari-agenzia-entrate>, *verified*)
processes the official dumps into friendlier formats and is a reasonable
bootstrap. Treat any mirror as a bootstrap only: the importer takes the official
layout, so the official file drops straight in on top of it.

**Drop it in** `data/omi/` and run:

```bash
./retire value omi --scan          # imports every file in data/omi/
```

**Attribution (required):** `Agenzia Entrate — OMI`. Printed next to every band
the app shows.

**Limits to keep saying out loud.** A band is for a zone and a typology, not an
appraisal of a building. Asking prices are not transaction prices. Trento,
Bolzano and parts of Gorizia and Trieste run the *sistema tavolare* and their
transaction volumes are incomplete — the scorer attaches a caveat automatically
for those provinces.

---

## 2. ISPRA — IdroGEO (flood and landslide hazard, plus census population)

**What it gives you.** Per comune: the share of its area, population, households
and buildings inside each flood hazard band (P1/P2/P3) and each landslide band
(P1–P4), from the national mosaics and the IFFI landslide inventory — plus 2011
and 2021 census population and the age split.

**How to get it — API, no key, no login** (*verified*):

```
GET https://idrogeo.isprambiente.it/api/pir/comuni/{codice_istat}
```

OpenAPI description: <https://idrogeo.isprambiente.it/openapi/> (*verified*).
Checked against Cisternino (`074005`) on 2026-09-13: 134 fields returned.

```bash
./retire value ispra --prov 074 --stats   # every comune in a province, figures and all
./retire value ispra --prov 074           # one call: names, codes and bounding boxes only
./retire value ispra 074005 074001        # or name the comuni
```

`--prov` without `--stats` is the free comune registry: `GET /pir/comuni?cod_prov=74`
returns every comune in the province with its ISTAT code, name, region and
bounding box. That is what lets a listing which says only "Cisternino" resolve
to `074005` — and therefore to its figures — without anyone downloading the
ISTAT registry first. The per-comune calls that `--stats` adds are paced.

**Attribution (required, CC-BY 4.0):** `ISPRA — IdroGEO`.

**What P3 means**, since the score leans on it: for floods, P3 is the *elevata*
scenario (roughly a 20–50 year return period), P2 the *media* (100–200 years).
For landslides the national reports use P3+P4 together, and so does this.

---

## 3. ISTAT

**What it gives you.**

| Dataset | Used for |
|---|---|
| Elenco dei comuni italiani | the spine: ISTAT code, name, province, region, Belfiore cadastral code |
| Elenco dei comuni soppressi | mergers — which retired code became which live one |
| Popolazione e struttura per età | population change, young/elderly share |
| Censimento permanente — abitazioni | dwelling stock, occupancy (denominator for turnover) |
| Permessi di costruire | new supply |
| Capacità e movimento degli esercizi ricettivi | tourist beds per comune; arrivals and presences |

**How to get it — click-path.** Codes and mergers:
<https://www.istat.it/classificazione/codici-dei-comuni-delle-province-e-delle-regioni/>
(*verified*). Indicators: IstatData (<https://esploradati.istat.it/>), Demo
(<https://demo.istat.it/>), and the Atlante Statistico del Territorio. ISTAT's
file URLs are versioned per release, so the app does not hard-code them — a
hard-coded URL that silently 404s is worse than a documented click-path.

```bash
./retire value istat Elenco-comuni-italiani.csv                 # the registry
./retire value istat Elenco-comuni-soppressi.csv --mergers
./retire value istat posti-letto-2024.csv \
    --metric tourism_beds --year 2024 --value-column "posti letto"
```

The last form takes *any* table with a comune-code column and a value column.
That is deliberate: ISTAT reorganises its outputs, and a generic importer plus
an explicit `--metric` name survives reorganisation in a way that a dozen
bespoke parsers would not.

**Attribution:** `ISTAT`. CC-BY 3.0/4.0 depending on the dataset — check the
page you downloaded from.

---

## 4. MEF — Dipartimento delle Finanze (IRPEF by comune)

**What it gives you.** Per comune, per year: number of taxpayers, total and
average declared income, the bracket distribution, and *reddito da fabbricati* —
income declared from buildings, the only free official signal of how much local
housing actually earns.

**How to get it — click-path.** The Finanze open-data pages under
<https://www.finanze.gov.it/> → *Opendata* → *Redditi e principali variabili
IRPEF su base comunale*. One CSV per year.

```bash
./retire value mef redditi-2023.csv --year 2023
```

**Attribution (required):** `MEF — Dipartimento delle Finanze`.

**Limit.** Declared income understates real income, unevenly by region. Comuni
below the disclosure threshold have cells suppressed — the importer stores those
as absent, never as zero.

---

## 5. Seismic classification (DPC / INGV)

**What it gives you.** Every comune in zone 1 (highest hazard) to 4 (lowest).
The underlying hazard model is INGV's; the classification is maintained by the
Dipartimento della Protezione Civile with the regions, **published per region**,
which is why there is no single URL and this is a drop-folder import.

```bash
./retire value seismic classificazione-sismica-puglia.xlsx --year 2025
```

**Attribution:** `DPC / INGV — classificazione sismica`.

**How the score treats it.** Zone 1 and 2 are a penalty, not a veto: half of
Italy is zone 2. A retrofitted 1990s build and an unreinforced 1890 farmhouse
sit in the same zone and are not the same risk.

---

## 6. OpenStreetMap (amenities)

**What it gives you.** Distance to the nearest station, bus stop, supermarket,
school, pharmacy, hospital, doctor and restaurant cluster.

**How to get it — API, no key** (Overpass): `https://overpass-api.de/api/interpreter`.

Overpass is donated infrastructure. This app caches every answer for 30 days in
~1 km cells and enforces a minimum gap between calls; a bulk rescore reads cache
only and never calls out (`fetch_amenities=False`, the default).

**Attribution (required, ODbL):** `© OpenStreetMap contributors`.

**Limit.** Straight-line distance is not drive time. Treated as a coarse "is
there anything here" signal, which is what it can support.

---

## 7. Catasto — documented, not implemented

The national cadastral cartography (every *particella*, GML, CC-BY 4.0, plus
WFS/WMS) and **ANNCSU** (streets and civic numbers, for geocoding rural
addresses) are the right long-term join. They are not in this deliverable, for
reasons written out in `retirement/modules/value/ingestion/catasto.py`: volume
(tens of GB nationally), the tavolare provinces missing by design, and the fact
that *rendita catastale* is a fiscal figure that must never be divided into a
price and called a yield.

---

## Listings

There is **no free nationwide API of ordinary for-sale listings** in Italy — no
MLS, and the big portals block automated clients at the edge. Checked on
2026-09-13: `idealista.it` and `immobiliare.it` return **403** with a bot-wall
to any non-browser client, including their RSS paths; `casa.it/rss` and
`gate-away.com/feed` return 404.

What does work, in order of how much data it yields per unit of effort:

| Provider | Status | Notes |
|---|---|---|
| **PVP — Portale Vendite Pubbliche** | official, free, no key (*verified*) | Every judicial sale notice in Italy. `POST https://pvp.giustizia.it/ric-*/ric-ms/ricerca/vendite` answered a plain `curl` with `{}` and reported 328,183 lots. Ministry of Justice, public by law. Caveat: the path carries a build hash that changes on deploys, so it has to be re-read from the page config when it breaks. |
| **Saved-search alert emails** | free, no key | The portals' own mechanism — set a saved search, point the alerts at a mailbox, and `property/sources/alerts.py` pulls the URLs out. Covers portals that have no API at all. Only sees listings posted after you set it up. |
| **Your own CSV / GeoJSON** | free | `POST /api/value/listings/upload`, or the upload box on `/value`. Columns: `url, title, price, size_m2, lat, lon, comune, province, typology, condition, rent_month`. |
| **Manual URLs** | free | Paste anything; it is fetched, parsed and scored beside the rest. |
| **Idealista Search API** | official, needs approval | <https://developers.idealista.com/access-request>. Free tier is small and access is granted to vetted partners, not individuals. Free to ask; do not plan around it. |
| **Third-party wrappers** (RapidAPI, Apify) | self-service, paid, scraping | Behind a flag. They scrape the portals, so they break when a portal changes, and the ToS question is theirs and yours. Not enabled by default. |

---

## Update cadence

| Source | Revised | What to do |
|---|---|---|
| OMI quotations | twice a year (semester 1 and 2) | drop the new pair in `data/omi/`, `./retire value omi --scan`. Semesters sit side by side; nothing is overwritten. |
| OMI zone geometry | rarely | re-import when a comune's zones are redrawn |
| ISPRA IdroGEO | on each mosaic release | `./retire value ispra` again |
| ISTAT population | yearly | re-import with `--year` |
| ISTAT tourism | yearly (comune), quarterly (province) | as above |
| MEF IRPEF | yearly, ~18 months in arrears | as above |
| Seismic classification | on regional decree | rare |
| OSM | continuously | cache expires after 30 days |

## Checking what is actually loaded

```bash
./retire value coverage
```

and `GET /api/value/coverage`. Both print per-source row counts, because a score
computed on missing data is the failure mode this whole design is arranged to
prevent: a component with no data behind it is reported as *no data*, its weight
is redistributed over the components that do have data, and the response says
which ones were dropped and what share of the intended weight survived
(`confidence`).
