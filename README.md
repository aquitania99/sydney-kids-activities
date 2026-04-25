# Sydney Kids Activities — Data Pipeline Guide

A complete reference for junior developers. Covers **why** every decision was made, **what** each piece does, **how** to work with it, and the **real-world gotchas** we hit building it.

> This guide was written alongside the actual build. Every bug, fix, and dead end is documented here — because that's how real projects work.

---

## Table of Contents

1. [The Problem We're Solving](#1-the-problem-were-solving)
2. [Architecture Overview](#2-architecture-overview)
3. [Architecture Deep Dive — The Whys and Hows](#3-architecture-deep-dive--the-whys-and-hows)
4. [Why These Data Sources?](#4-why-these-data-sources)
5. [Project Structure](#5-project-structure)
6. [Data Models](#6-data-models-modelspy)
7. [Source: OpenTripMap](#7-source-opentripmappy)
8. [Source: Overpass (OSM)](#8-source-overpasspy)
9. [Source: NSW Open Data CSVs](#9-source-nsw_datapy)
10. [Source: Eventbrite](#10-source-eventbritepy)
11. [Database Layer](#11-database-layer-dbpy)
12. [Pipeline Orchestrator](#12-pipeline-orchestrator-pipelinepy)
13. [Configuration & Secrets](#13-configuration--secrets-configpy)
14. [Real-World Bugs We Hit (and Fixed)](#14-real-world-bugs-we-hit-and-fixed)
15. [Cheatsheet: Common Tasks](#15-cheatsheet-common-tasks)
16. [How to Get API Keys](#16-how-to-get-api-keys)
17. [Running the Pipeline](#17-running-the-pipeline)
18. [Querying the Database](#18-querying-the-database)
19. [Tradeoffs & Known Limitations](#19-tradeoffs--known-limitations)
20. [Extending the Pipeline](#20-extending-the-pipeline)

---

## 1. The Problem We're Solving

Parents in Sydney want to find activities for their kids, filtered by age. The challenge is data — there is no single "kids activities" database. We need to pull from multiple sources, normalise everything into one shape, and store it locally.

**Three types of data we need:**

| Type | Example | Source |
|------|---------|--------|
| Venues/Places | Playgrounds, pools, libraries | OpenTripMap + Overpass + NSW CSVs |
| Scheduled Events | Weekend workshops, classes | Eventbrite *(limited — see section 9)* |
| Age metadata | "Suitable for ages 3–8" | Eventbrite tags + regex parsing |

**Final result after a full run:**
```
venues [opentripmap]: 478
venues [osm]:         786
venues total:        1264
events total:           0  ← Eventbrite search requires paid tier (post-2023)
```

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                     pipeline.py                          │
│               (the orchestrator — runs all)              │
└──────┬───────────┬──────────────┬───────────────────────┘
       │           │              │
       ▼           ▼              ▼
 OpenTripMap   Overpass      Eventbrite        NSW CSVs
  (venues)    (venues)    (events — limited)  (venues)
       │           │              │               │
       └───────────┴──────────────┴───────────────┘
                           │
                           ▼
                    Pydantic models
                  (validate + shape)
                           │
                           ▼
                     SQLite DB
                   (activities.db)
```

**Key principle**: each source is isolated in its own file. The pipeline just calls them and saves results. If one source breaks, the others still work. This is called the **Single Responsibility Principle** — each file has one job.

---

## 3. Architecture Deep Dive — The Whys and Hows

This section explains the architectural decisions behind the project. Not just *what* the code does, but *why it was designed this way* — and what would happen if you designed it differently.

---

### 3.1 Why a Pipeline?

A **pipeline** is a sequence of steps where data flows through each stage in order:

```
fetch → validate → store → repeat for next source
```

The alternative is a **monolith** — one big script that does everything inline:

```python
# Monolith: everything tangled together
resp = httpx.get("https://api.opentripmap.com/...")
for item in resp.json()["features"]:
    name = item["properties"]["name"]
    lat = item["geometry"]["coordinates"][1]
    conn.execute("INSERT INTO venues (name, lat...) VALUES (?,?...)", (name, lat...))
    # ... 200 more lines of this
```

**Problem with a monolith**: when Overpass breaks, the whole script breaks. When you want to add a new source, you have to understand all the existing code first. When you want to re-run just one source, you can't — it's all tied together.

**Why pipeline is better**:
- Each source is independent — one breaks, others still run
- Adding a source = one new file + two lines in `pipeline.py`
- Re-running one source = import and call one function
- Testing one source = test one function in isolation

---

### 3.2 Why the Adapter Pattern?

Every source in `sources/` is an **adapter** — it takes messy external data and converts it to our clean internal format (`Venue` or `Event`). The pipeline doesn't care how each adapter works internally.

```
Overpass API  ──[overpass.py adapter]──┐
OpenTripMap   ──[opentripmap.py adapter]┤──► list[Venue] ──► db.upsert_venue()
NSW CSV       ──[nsw_data.py adapter]──┘
```

Each adapter:
1. Knows how to talk to its specific source (different URL, auth, format)
2. Returns the same type: `list[Venue]`
3. The pipeline only deals with `list[Venue]` — it doesn't care about the source

**Why this matters**: if OpenTripMap changes their API tomorrow, you update `opentripmap.py` only. The pipeline, the DB, and the other sources are untouched. This is called **loose coupling** — parts of the system don't depend on each other's internals.

---

### 3.3 Why Normalisation?

Each source returns data in a different shape:

```python
# OpenTripMap (GeoJSON feature):
{"type": "Feature", "geometry": {"coordinates": [151.2, -33.8]}, "properties": {"name": "...", "xid": "..."}}

# Overpass (OSM element):
{"type": "node", "id": 12345, "lat": -33.8, "lon": 151.2, "tags": {"name": "...", "leisure": "playground"}}

# NSW CSV (pandas row):
{"FACILITY_NAME": "...", "LATITUDE": "-33.8", "LONGITUDE": "151.2", "SUBURB": "..."}
```

Three completely different shapes. If we stored them as-is, every query would have to know which source it's reading and handle each format differently. That's unsustainable.

**Normalisation** = transform all shapes into one common shape (`Venue`) before storing:

```python
# All three sources produce this:
Venue(source="osm", name="Centennial Park", latitude=-33.8, longitude=151.2, ...)
Venue(source="opentripmap", name="Centennial Park", latitude=-33.8, longitude=151.2, ...)
```

Now queries don't care about the source. `SELECT * FROM venues WHERE is_free = 1` works on all 1264 records regardless of where they came from.

---

### 3.4 Why Pydantic for Validation?

Between the raw API response and the database there's a danger zone: what if the API returns unexpected data? A `null` where you expect a string. A string where you expect a float. A missing field. Any of these would cause a silent bug or a confusing crash deep in the pipeline.

Pydantic acts as a **checkpoint**:

```
Raw API data → [Pydantic model] → Validated, typed Python object → DB
```

If data is wrong, Pydantic raises an error immediately at the checkpoint — not silently later when you try to use it. And it auto-converts compatible types (string `"33.8"` → float `33.8`).

```python
# Pydantic catches this immediately:
Venue(latitude="not_a_number")
# ValidationError: latitude — Input should be a valid number

# Without Pydantic, this fails silently until you try to use it:
venue = {"latitude": "not_a_number"}
conn.execute("INSERT INTO venues (latitude) VALUES (?)", (venue["latitude"],))
# SQLite stores it as text, queries silently return wrong results
```

**Rule of thumb**: validate data at the boundary between external systems and your code. Pydantic is that boundary.

---

### 3.5 Why Upsert Instead of Insert?

**Insert** = always add a new row. Problem: running the pipeline twice doubles your records.

**Delete-then-insert** = wipe the table, reload everything. Problem: if one source fails mid-run, you lose all existing data.

**Upsert** = insert if new, update if exists. Best of both:
- Re-runs are safe — no duplicates, no data loss
- Partial runs are safe — if Overpass fails, OTM data stays untouched
- Incremental updates work — run one source to refresh just its records

```sql
INSERT INTO venues (source, external_id, name, ...)
VALUES (?, ?, ?, ...)
ON CONFLICT(source, external_id) DO UPDATE SET
    name=excluded.name, ...
```

The `UNIQUE(source, external_id)` constraint is what makes this work — it defines what "already exists" means. A venue is unique by its combination of source AND its ID within that source.

---

### 3.6 Why Graceful Degradation?

Every source that fails returns an empty list instead of crashing:

```python
if not OPENTRIPMAP_KEY:
    print("  [opentripmap] key not set — skip")
    return []

if resp.status_code == 404:
    print("  [eventbrite] endpoint unavailable — skip")
    break
```

**Why not just crash?** In a team of 4, not everyone will have every API key on day one. The NSW CSVs might not be downloaded yet. Eventbrite's endpoint is dead. If any of these crash the whole pipeline, nobody can run anything until every source is working.

Graceful degradation means: **run what you can, skip what you can't, report clearly**. The team can make progress even with partial data.

This is called **fault tolerance** — the system continues to operate (in a reduced capacity) when parts of it fail.

---

### 3.7 Why Feature Flags on `run()`?

```python
def run(
    use_opentripmap: bool = True,
    use_overpass: bool = True,
    eventbrite_pages: int = 5,
) -> None:
```

Instead of hardcoding which sources run, we make it configurable at call time. This gives you:

```bash
# Development: fast iteration, skip slow Overpass
python -c "from pipeline import run; run(use_overpass=False)"

# Production: maximum coverage
python pipeline.py

# Debugging events only
python -c "from pipeline import run; run(use_opentripmap=False, use_overpass=False)"

# Refresh only venues, skip events
python -c "from pipeline import run; run(eventbrite_pages=0)"
```

No code changes needed. No commenting/uncommenting. This pattern is called **configuration over code** — behaviour changes via config, not edits.

---

### 3.8 Why SQLite for This Stage?

The right database depends on the stage of the project:

| Stage | Database | Why |
|-------|----------|-----|
| Prototype / local tool | **SQLite** | Zero setup, file-based, built-in |
| Small production app | SQLite or PostgreSQL | Depends on concurrent users |
| Real production app | **PostgreSQL** | Concurrent writes, scaling, full SQL |

We're at prototype stage. SQLite gives us:
- A single file (`activities.db`) — easy to copy, share, backup
- Full SQL — same queries work in PostgreSQL later
- No server — just Python stdlib `sqlite3`

**Migration path**: when you need PostgreSQL, swap `sqlite3.connect()` for a `psycopg2` connection. The SQL is identical — SQLite and PostgreSQL both support `INSERT ... ON CONFLICT DO UPDATE`. The migration is one afternoon of work, not a rewrite.

---

### 3.9 The Full Data Flow, Step by Step

```
1. pipeline.run() called
        │
        ▼
2. fetch_otm_venues()          ← HTTP GET to OpenTripMap
   returns list[Venue]         ← Pydantic validates each item
        │
        ▼
3. for v in venues:
       upsert_venue(conn, v)   ← SQLite INSERT OR UPDATE
        │
        ▼
4. Same for Overpass, NSW CSVs, Eventbrite
        │
        ▼
5. stats(conn)                 ← COUNT queries, print summary
        │
        ▼
6. activities.db               ← 1264 venues, ready to query
```

At every step, data is validated before it moves forward. If step 2 fails (network error), step 3 never runs — but step 4 (other sources) still does. If a single record fails validation in step 2, that record is skipped — the rest of the batch continues.

---

## 4. Why These Data Sources?

### Why NOT Google Places?

Google Places is the obvious choice — great data, good AU coverage. But it **requires a credit card** to activate an API key. For a student project with 4 developers that's a blocker: one person ends up owning the billing, and the $200/month free credit disappears fast in production.

### Why OpenTripMap? (primary venue source)

- Free tier: **500 requests/day**, no credit card
- Data sourced from OpenStreetMap but **pre-cleaned and structured**
- Simple REST API — radius search, no custom query language
- Returns GeoJSON with coordinates embedded
- Covers Sydney well — parks, sports centres, museums, libraries

**Advantage over raw Overpass**: Overpass requires you to write Overpass QL (a custom query language). OpenTripMap wraps the same data in a standard REST API. Less to learn, faster to get running.

### Why Overpass (as supplement)?

OpenTripMap caps at 500 req/day. Overpass is unlimited and free. More importantly, Overpass lets you query specific OSM tags (`surface=grass`, `wheelchair=yes`) that OpenTripMap doesn't expose. We run both — different `source` values in the DB, deduped automatically via `UNIQUE(source, external_id)`.

### Why NSW Open Data CSVs?

State governments publish authoritative datasets of public facilities. This data is:
- **More reliable** than OSM for official venues — it's council-maintained
- **Free** — no API, just CSV download
- **Covers gaps** OSM misses — newer facilities, official names

Downside: manual download, not a live API. Good enough for a prototype.

### Why Eventbrite?

Venues are static. Eventbrite gives us **scheduled activities** — events with specific dates, organised by people who fill in details properly (including age ranges). The Kids & Family category (`category_id=10`) filters out 90% of irrelevant results.

**Important**: as of 2023, Eventbrite removed public search from the free API tier. The `/events/search/` endpoint returns 404 for standard API keys. The code handles this gracefully. See [section 9](#9-source-eventbritepy) for alternatives.

### Why NOT RapidAPI wrappers?

RapidAPI wraps the same APIs (OpenTripMap, Google Places) but adds:
- Another account to manage
- Rate limits that differ from the original
- Extra latency (proxy hop)
- Pricing that can change without warning

For production: RapidAPI is useful for unified billing. For a student project: go direct.

---

## 4. Project Structure

```
sydney-kids-activities/
├── models.py           ← Data shapes (what a Venue/Event looks like)
├── config.py           ← API keys loaded from .env file
├── db.py               ← Database: create tables, insert/update records
├── pipeline.py         ← Main script: runs everything in order
├── requirements.txt    ← Python packages needed
├── .env                ← Your secrets (never commit this)
├── data/               ← Drop NSW CSV files here
│   └── .gitkeep
└── sources/            ← One file per data source
    ├── __init__.py
    ├── opentripmap.py  ← Fetch venues from OpenTripMap API
    ├── overpass.py     ← Fetch venues from OpenStreetMap/Overpass
    ├── nsw_data.py     ← Load venues from NSW government CSVs
    └── eventbrite.py   ← Fetch events from Eventbrite API
```

---

## 5. Data Models (`models.py`)

```python
from pydantic import BaseModel
from typing import Optional

class AgeRange(BaseModel):
    min_age: Optional[int] = None
    max_age: Optional[int] = None

class Venue(BaseModel):
    source: str           # "osm" | "opentripmap" | "nsw"
    external_id: str      # ID from the source system
    name: str
    latitude: float
    longitude: float
    address_street: Optional[str] = None
    address_suburb: Optional[str] = None
    address_postcode: Optional[str] = None
    venue_type: Optional[str] = None
    is_free: Optional[bool] = None
    is_indoor: Optional[bool] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    wheelchair_access: Optional[str] = None
    age_range: Optional[AgeRange] = None

class Event(BaseModel):
    source: str
    external_id: str
    name: str
    start_datetime: str
    ...
    age_range: Optional[AgeRange] = None
```

### Why Pydantic?

Pydantic validates your data automatically. If a source returns `"33.8688"` (string) where you declared `latitude: float`, Pydantic converts it. If it returns `None` where you need a value, it raises an error immediately — not silently somewhere else later.

Think of models as a **contract**: "this is what a Venue must look like." Every source produces `Venue` objects. The database only accepts `Venue` objects. Nothing wrong slips through.

### Why `Optional[str] = None`?

Many fields won't be available from every source. OpenTripMap doesn't always have phone numbers. NSW CSVs don't have websites. `Optional[str] = None` means "this field can be absent — that's fine." Without `Optional`, Pydantic would raise an error for every missing field.

### Why a separate `AgeRange` model?

Age is two values: min and max. You could store them as flat columns (`age_min`, `age_max`) — and we do in SQLite — but grouping them in a model makes the Python code more expressive: `venue.age_range.min_age` is clearer than `venue.age_min`.

---

## 6. Source: OpenTripMap (`sources/opentripmap.py`)

### What it does

Calls the OpenTripMap REST API to find Points of Interest (POIs) within 30km of Sydney CBD. Returns up to 500 venues across sport, cultural, natural, and amusement categories.

### The API

Base URL: `https://api.opentripmap.com/0.1/en/places`

Key endpoints:
- `/radius` — search by centre point + radius (what we use)
- `/xid/{xid}` — get full detail for one place (address, phone, etc.)

### Key concepts

**Radius search**: instead of drawing a bounding box (rectangle), we say "give me everything within X metres of this point." Sydney CBD = `-33.8688, 151.2093`.

```python
params = {
    "radius": 30000,      # 30km in metres
    "lon": 151.2093,
    "lat": -33.8688,
    "limit": 100,         # max per request
    "offset": 0,          # pagination
    "format": "geojson",  # ← IMPORTANT: see gotcha below
    "apikey": OPENTRIPMAP_KEY,
}
```

**`kinds` — category filter**: OpenTripMap uses a hierarchical category system. Valid top-level kinds that work for this project:

| Kind | What it includes |
|------|-----------------|
| `sport` | Sports centres, gyms, courts |
| `amusements` | Theme parks, entertainment centres |
| `natural` | Parks, beaches, nature reserves |
| `cultural` | Museums, libraries, galleries |

> **Note**: `kids_places` and `leisure` look like they should work but the API returns 400 for them. Stick to the four above.

**GeoJSON format**: coordinates come as `[longitude, latitude]` — note the ORDER. Most people expect lat/lon, but GeoJSON is lon/lat. This trips up everyone the first time.

```python
geom = item.get("geometry", {}).get("coordinates", [])
lon, lat = geom[0], geom[1]  # ← lon FIRST in GeoJSON
```

**Pagination**: max 100 results per request, loop with `offset`:

```python
offset = 0
while len(venues) < limit:
    params["offset"] = offset
    # ... make request
    items = data.get("features", [])
    offset += len(items)
    if len(items) < 100:
        break  # last page
```

**`_parse_item` helper**: item parsing is extracted into its own function. This keeps `fetch_sydney_venues` readable and reduces Cognitive Complexity (a code quality metric) from 17 to ~9. SonarQube flags functions above 15.

**`enrich_venue(xid)`**: separate function to fetch full detail for one place. Each call = 1 API request. Don't call this for all 500 venues — use it only on-demand or for your top picks.

**Rate limiting**: `time.sleep(0.2)` between pages. Free tier = 500 req/day. At 100 results/req, 5 requests = 500 venues. Respect the limit — if you hit it, you're blocked for the day.

### Gotcha 1 — `kinds` must NOT be URL-encoded

httpx URL-encodes commas in `params` dicts: `","` → `"%2C"`. OpenTripMap rejects that with a 400 error.

**Wrong (httpx auto-encodes commas):**
```python
params["kinds"] = "sport,natural,cultural"
resp = httpx.get(url, params=params)
# URL becomes: ...&kinds=sport%2Cnatural%2Ccultural → 400 error
```

**Right (build kinds manually outside params dict):**
```python
query_string = str(httpx.QueryParams(params)) + f"&kinds={KIDS_KINDS}"
resp = httpx.get(f"{BASE_URL}/radius?{query_string}", timeout=30)
# URL becomes: ...&kinds=sport,natural,cultural → works
```

This is a common trap with any API that uses comma-separated values in query params.

### Gotcha 2 — use `format=geojson` not `format=json`

`format=json` returns a flat array: `[{"xid": "...", "name": "...", "kinds": "..."}]` — **no coordinates included**. You'd need a second API call per venue to get lat/lon.

`format=geojson` returns a GeoJSON FeatureCollection: `{"features": [{"geometry": {"coordinates": [lon, lat]}, "properties": {...}}]}` — coordinates included in every item. Always use `geojson`.

```python
# Wrong — no coordinates in response
params["format"] = "json"

# Right — coordinates included
params["format"] = "geojson"

# And parse accordingly:
data = resp.json()
items = data.get("features", [])
```

---

## 7. Source: Overpass (`sources/overpass.py`)

### What it does

Queries OpenStreetMap directly via the Overpass API — the "query engine" for OSM raw data. Returns named venues across Sydney using a bounding box.

### The API

URL: `https://overpass-api.de/api/interpreter`  
No auth required. Public service — be polite, add delays.

### Overpass QL — the query language

Overpass uses its own query language. The pattern we use:

```
[out:json][timeout:60];
(
  node["leisure"="playground"](-34.1,150.5,-33.5,151.4);
  way["leisure"="playground"](-34.1,150.5,-33.5,151.4);
);
out center tags;
```

Breaking it down:
- `[out:json]` — return JSON (not XML)
- `[timeout:60]` — give up after 60 seconds
- `node` — a single GPS point (small playground, bench)
- `way` — a polygon (large park with a boundary drawn around it)
- `(-34.1,150.5,-33.5,151.4)` — bounding box: south, west, north, east
- `out center tags` — for ways, return the centre point + all key=value tags

**OSM tags** are community-maintained key=value pairs. Useful ones:
- `leisure=playground`
- `amenity=swimming_pool`
- `amenity=library`
- `leisure=sports_centre`
- `leisure=park`

**Test Overpass queries in your browser** before writing Python. Go to [overpass-turbo.eu](https://overpass-turbo.eu), paste a query, and see results on a map instantly. Use this to verify your query returns what you expect.

**Why skip unnamed nodes?**
```python
name = tags.get("name")
if not name:
    continue
```
OSM has millions of unnamed nodes — benches, trees, lamp posts. No name = useless for an activity finder.

**`fee` tag mapping**:
- `fee=yes` → `is_free=False`
- `fee=no` → `is_free=True`
- missing → `is_free=None` (unknown — very common)

### Gotcha — httpx triggers 406, use urllib instead

Both GET and POST should work with Overpass, but in practice **httpx triggers a 406 Not Acceptable** error on this server regardless of headers. `urllib` (Python's built-in HTTP library) works reliably.

**Wrong (httpx — triggers 406 even with explicit headers):**
```python
resp = httpx.post(OVERPASS_URL, data={"data": query})          # 406
resp = httpx.get(OVERPASS_URL, params={"data": query})         # 406
```

**Right (urllib with User-Agent header):**
```python
import urllib.parse, urllib.request, json

payload = urllib.parse.urlencode({"data": query}).encode()
req = urllib.request.Request(
    OVERPASS_URL,
    data=payload,
    headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "SydneyKidsActivities/1.0 (student project)",
        "Accept": "application/json",
    },
)
with urllib.request.urlopen(req, timeout=120) as r:
    elements = json.loads(r.read())["elements"]
```

The `User-Agent` header is critical — Overpass blocks requests that look like anonymous scrapers.

### When to use Overpass vs OpenTripMap

| Situation | Use |
|-----------|-----|
| Have OTM key, first run | OpenTripMap |
| No OTM key | Overpass |
| Want maximum coverage | Both |
| Need specific OSM tags (`surface`, `wheelchair`) | Overpass only |
| Hitting OTM 500/day limit | Overpass |
| Need more than 500 venues | Overpass (unlimited) |

---

## 8. Source: NSW Open Data (`sources/nsw_data.py`)

### What it does

Reads CSV files downloaded from [data.nsw.gov.au](https://data.nsw.gov.au). No API call — pure local file processing with pandas.

### Why manual download?

NSW Open Data has an API but their dataset URLs change frequently. For a 4-week project: download once, commit the CSV, done. Don't build fragile automation around a government portal.

### Column name normalisation

Every NSW dataset has different column names. Parks might have `LATITUDE`, libraries `lat_wgs84`, aquatic centres `Y`. We handle this by:

1. Normalise all column names to lowercase + underscores
2. Try a list of known alternatives for each field

```python
df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")

lat = _first(row, ["latitude", "lat", "y", "lat_wgs84"])
lon = _first(row, ["longitude", "lon", "long", "x", "lng_wgs84"])
name = _first(row, ["name", "facility_name", "title", "site_name"])
```

`_first()` returns the first non-null value from the list. If none match, it returns `None` and the row gets skipped.

**Debug tip when adding a new CSV:**
```python
import pandas as pd
df = pd.read_csv("data/your_file.csv")
print(df.columns.tolist())  # see actual column names
print(df.head(3))           # see first 3 rows
print(df.dtypes)            # see data types
```
Never guess column names. Look first.

### Which NSW datasets to download

Search [data.nsw.gov.au](https://data.nsw.gov.au) for:
- **"open spaces"** → parks and reserves
- **"libraries"** → public library locations
- **"aquatic centres"** → public swimming pools

Download as CSV → save in `data/` → filenames must match `config.py`:
```python
NSW_DATA_FILES = {
    "parks":    "data/nsw_open_spaces.csv",
    "libraries":"data/nsw_libraries.csv",
    "aquatic":  "data/nsw_aquatic_centres.csv",
}
```

---

## 9. Source: Eventbrite (`sources/eventbrite.py`)

### ⚠️ Important: public search is dead on free tier

As of 2023, Eventbrite removed the `/events/search/` endpoint from their free API tier. It returns **404**. Our code handles this gracefully:

```python
if resp.status_code == 404:
    print("  [eventbrite] search endpoint unavailable")
    break
```

The code is still there in case they reinstate it or you upgrade to a paid plan.

### What the code does (when it works)

Searches for Kids & Family events (category 10) within 20km of Sydney, extracts age ranges from event text via regex.

### Authentication

Eventbrite uses Bearer token auth:
```python
headers = {"Authorization": f"Bearer {EVENTBRITE_TOKEN}"}
```

Get a token: eventbrite.com → Account → Developer Links → API Keys → Request a new key. Fill in contact info + app details (Application URL can be `http://localhost:3000` for a prototype).

### `expand=venue` — avoiding N+1 requests

By default, event responses don't include venue details. Without `expand=venue`, you'd need one extra request per event to get its location — called the **N+1 problem**. With `expand=venue`, Eventbrite embeds the venue inside each event response:

```python
params["expand"] = "venue"
# Now: event["venue"]["address"]["city"] works directly
```

### Age extraction — regex heuristic

```python
def _extract_age_range(text: str) -> AgeRange | None:
    # "3-8 years" or "3–8 years"
    m = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*year', text, re.I)
    if m:
        return AgeRange(min_age=int(m.group(1)), max_age=int(m.group(2)))
    # "ages 5+" or "aged 5+"
    m = re.search(r'age[sd]?\s+(\d+)\+', text, re.I)
    if m:
        return AgeRange(min_age=int(m.group(1)))
    # "under 12"
    m = re.search(r'under\s+(\d+)', text, re.I)
    if m:
        return AgeRange(max_age=int(m.group(1)))
    return None
```

**Coverage ~60%** — catches common patterns, misses unusual phrasings. Good enough for a prototype.

### Alternatives to Eventbrite for events

Since Eventbrite search is dead on free tier, consider:
- **Meetup API** — has free tier, good for community events
- **Humanitix API** — Australian-founded ticketing platform, developer-friendly
- **Scraping eventbrite.com.au** — technically against ToS but works for prototypes
- **Council event feeds** — some Sydney councils publish event RSS/JSON feeds

---

## 10. Database Layer (`db.py`)

### Why SQLite?

| Feature | SQLite | PostgreSQL |
|---------|--------|------------|
| Setup | Zero — single file | Requires server |
| Install | Built into Python | `pip install psycopg2` + server |
| Concurrent writes | Poor | Excellent |
| Max practical size | ~1M rows | Billions |
| Tools | DB Browser (free GUI) | pgAdmin, DBeaver |
| Good for | Prototypes, local tools | Production apps |

For this project: SQLite. If you grow to a real multi-user app, the migration to PostgreSQL is a one-day job — SQLAlchemy makes it almost transparent.

### Table design

**`venues` table**: permanent places (playgrounds, pools, libraries). Static data.

**`events` table**: scheduled activities with start/end dates. Temporal data.

Why separate? Different shapes, different query patterns, different expiry logic. Combining them would require many nullable columns and messy queries.

### Upsert pattern

```sql
INSERT INTO venues (source, external_id, name, ...)
VALUES (?, ?, ?, ...)
ON CONFLICT(source, external_id) DO UPDATE SET
    name=excluded.name,
    latitude=excluded.latitude,
    ...
```

**Upsert** = INSERT if new, UPDATE if already exists. The `UNIQUE(source, external_id)` constraint is the key — it means "no two rows from the same source can have the same ID."

Running the pipeline twice? Safe — records update, no duplicates.

`excluded.name` = SQLite syntax for "the value we were trying to insert" in the ON CONFLICT clause.

### Why `conn.row_factory = sqlite3.Row`?

Without it, sqlite3 returns tuples: `(1, "osm", "Centennial Park", ...)` — you'd access by index `row[2]`.

With it, results behave like dicts: `row["name"]` — much more readable and safe if column order changes.

```python
conn = sqlite3.connect("activities.db")
conn.row_factory = sqlite3.Row

row = conn.execute("SELECT * FROM venues LIMIT 1").fetchone()
print(row["name"])    # ✓ clear
print(row[2])         # ✗ fragile
```

---

## 11. Pipeline Orchestrator (`pipeline.py`)

### What it does

Runs all sources in sequence, upserts everything into the DB, prints a summary.

### Feature flags

```python
def run(
    use_opentripmap: bool = True,
    use_overpass: bool = True,
    eventbrite_pages: int = 5,
) -> None:
```

Turn sources on/off without touching source code:

```python
# No OTM key
python -c "from pipeline import run; run(use_opentripmap=False)"

# Only Overpass (fast, no rate limits)
python -c "from pipeline import run; run(use_opentripmap=False)"

# No events (skip Eventbrite entirely)
python -c "from pipeline import run; run(eventbrite_pages=0)"

# Maximum events fetch
python -c "from pipeline import run; run(eventbrite_pages=20)"
```

### Why order matters

Venues load first, events last. Not technically required now, but if you later add logic that links events to venue records ("find the DB venue closest to this Eventbrite venue"), venues must exist before you try to link them.

---

## 12. Configuration & Secrets (`config.py`)

```python
import os
from dotenv import load_dotenv

load_dotenv()  # reads .env file automatically

EVENTBRITE_TOKEN = os.environ.get("EVENTBRITE_TOKEN", "")
OPENTRIPMAP_KEY  = os.environ.get("OPENTRIPMAP_KEY", "")
```

### The `.env` file

Create `.env` in the project root:

```
OPENTRIPMAP_KEY=your_key_here
EVENTBRITE_TOKEN=your_token_here
```

Rules:
- No quotes around values
- No spaces around `=`
- **Never commit `.env` to Git** — add it to `.gitignore`

`load_dotenv()` in `config.py` reads this file at import time. You don't need to `export` anything — just run `python pipeline.py`.

### Why `.get()` not `["KEY"]`

```python
# This crashes if key isn't set
OPENTRIPMAP_KEY = os.environ["OPENTRIPMAP_KEY"]  # KeyError

# This returns "" if key isn't set
OPENTRIPMAP_KEY = os.environ.get("OPENTRIPMAP_KEY", "")  # safe
```

Each source checks `if not OPENTRIPMAP_KEY: return []` — so missing keys skip gracefully instead of crashing the whole pipeline. In a team where not everyone has every key, this matters.

### Why environment variables at all?

API keys committed to Git are **exposed forever** — even if you delete them in a later commit, they exist in the git history. Anyone who clones the repo can find them. GitHub's secret scanning bot will email you. Some services (AWS) will automatically deactivate keys found in public repos.

The pattern: secrets live in environment, code reads from environment.

---

## 13. Real-World Bugs We Hit (and Fixed)

This section documents every bug encountered during the actual build. Reading bug histories is one of the best ways to learn.

---

### Bug 1 — Overpass 406 Not Acceptable (httpx)

**Symptom**: `httpx.HTTPStatusError: Client error '406 Not Acceptable'`

**What we tried first**: Adding `Accept: */*` and `Content-Type` headers to httpx POST. Still 406.

**What we tried second**: Switching httpx to GET with `params={"data": query}`. Still 406.

**Root cause**: httpx sends request headers that the Overpass server rejects, regardless of POST or GET. The exact header causing it is hard to pin down — Overpass is picky.

**Fix**: Drop httpx entirely for this request. Use Python's stdlib `urllib` instead, with an explicit `User-Agent` header (Overpass blocks anonymous-looking requests):

```python
import urllib.parse, urllib.request, json

payload = urllib.parse.urlencode({"data": query}).encode()
req = urllib.request.Request(OVERPASS_URL, data=payload, headers={
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "SydneyKidsActivities/1.0 (student project)",
    "Accept": "application/json",
})
with urllib.request.urlopen(req, timeout=120) as r:
    elements = json.loads(r.read())["elements"]
```

**Lesson**: when an HTTP library misbehaves with a specific server, switching to a lower-level library is sometimes the right move. Don't spend hours debugging headers — try a different tool.

---

### Bug 2 — OpenTripMap 400 Bad Request (`kinds` encoding)

**Symptom**: `httpx.HTTPStatusError: Client error '400 Bad Request'`

**URL in error**: `...&kinds=sport%2Cnatural%2Ccultural` (commas URL-encoded)

**Root cause**: httpx URL-encodes ALL values in the `params` dict, including commas. OpenTripMap expects literal commas in `kinds`.

**Fix**: Build `kinds` outside the params dict and append manually:

```python
query_string = str(httpx.QueryParams(params)) + f"&kinds={KIDS_KINDS}"
resp = httpx.get(f"{BASE_URL}/radius?{query_string}", timeout=30)
```

**Lesson**: URL encoding is a common source of bugs with APIs. When you see a 400 error, look at the full URL in the error message — often the problem is visible right there.

---

### Bug 3 — OpenTripMap returns 0 venues (`format=json` has no coordinates)

**Symptom**: API returns 200 OK, 100 items, but all get skipped → 0 venues loaded.

**Debug**: added a print of the first raw response item:
```
{'xid': 'Q56604580', 'name': 'Theatre Royal, Sydney', 'dist': 20.96, 'kinds': '...'}
```

**Root cause**: `format=json` returns a flat array — no `geometry` or `properties` keys. Our parser looked for `item["geometry"]["coordinates"]` which didn't exist. All items silently skipped.

**Fix**: switch to `format=geojson`:
```python
params["format"] = "geojson"
# Response: {"features": [{"geometry": {"coordinates": [lon,lat]}, "properties": {...}}]}
data = resp.json()
items = data.get("features", [])
```

**Lesson**: always print a raw sample of an API response before writing a parser. Never assume the shape of the data — verify it.

---

### Bug 4 — `httpx.QueryParams` has no `.encode()` method

**Symptom**: `AttributeError: 'QueryParams' object has no attribute 'encode'`

**Root cause**: assumed the httpx API. `QueryParams` objects serialise via `str()`, not `.encode()`.

**Fix**: `str(httpx.QueryParams(params))` instead of `httpx.QueryParams(params).encode()`.

**Lesson**: when in doubt, `print(type(x))` and `print(dir(x))` — check what methods actually exist before assuming.

---

### Bug 5 — Eventbrite 404 on `/events/search/`

**Symptom**: `httpx.HTTPStatusError: Client error '404 NOT FOUND'`

**Root cause**: Eventbrite deprecated public event search for free API keys after 2023. The endpoint is simply gone for standard accounts.

**Fix**: handle 404 gracefully — log and move on:
```python
if resp.status_code == 404:
    print("  [eventbrite] search endpoint unavailable — requires paid tier")
    break
```

**Lesson**: APIs change. Always handle HTTP errors explicitly. A 404 on a search endpoint usually means the feature has been paywalled, not that your code is wrong.

---

### Bug 6 — `kids_places` and `leisure` kinds return 400

**Symptom**: 400 Bad Request when `kinds` included `kids_places` or `leisure`.

**Root cause**: these aren't valid top-level OpenTripMap kind values, even though they look like they should be.

**Fix**: use only verified kinds: `sport,amusements,natural,cultural`.

**Lesson**: always verify API parameter values against actual behaviour, not just documentation. Docs are often out of date or incomplete.

---

## 14. Cheatsheet: Common Tasks

### Add a new venue source

1. Create `sources/my_source.py`
2. Write `def fetch_venues() -> list[Venue]:` — return a list of `Venue` objects
3. In `pipeline.py`: import it, call it, upsert results
4. That's it — DB handles dedup automatically

### Add a new field to Venue

1. Add to `class Venue` in `models.py` (use `Optional[type] = None`)
2. Add column to `CREATE TABLE venues` in `db.py`
3. Add to `INSERT INTO venues` column list and `ON CONFLICT DO UPDATE SET` in `db.py`
4. Set the field in whichever source files have that data

### Re-run just one source

```python
# In terminal: python -c "..."
python -c "
from sources.opentripmap import fetch_sydney_venues
from db import get_conn, upsert_venue
conn = get_conn()
venues = fetch_sydney_venues()
for v in venues: upsert_venue(conn, v)
conn.close()
print(f'Done: {len(venues)} venues')
"
```

### Debug what an API actually returns

```python
python -c "
import httpx
resp = httpx.get('https://api.opentripmap.com/0.1/en/places/radius', params={
    'radius': 1000, 'lon': 151.2093, 'lat': -33.8688,
    'limit': 1, 'format': 'geojson', 'apikey': 'your_key'
})
import json; print(json.dumps(resp.json(), indent=2))
"
```

### Inspect a CSV before loading

```python
import pandas as pd
df = pd.read_csv("data/your_file.csv")
print(df.columns.tolist())  # actual column names
print(df.head(3))           # first 3 rows
print(df.dtypes)            # data types per column
print(df.isnull().sum())    # count nulls per column
```

### Test Overpass query in browser

Go to [overpass-turbo.eu](https://overpass-turbo.eu), paste:

```
[out:json][timeout:30];
node["leisure"="playground"](-33.9,151.1,-33.8,151.2);
out tags;
```

Results appear on a map. Use this to validate queries before writing Python.

---

## 15. How to Get API Keys

### OpenTripMap (free, no CC)

1. Go to [dev.opentripmap.org](https://dev.opentripmap.org)
2. Register (email only, no credit card)
3. Copy API key from dashboard
4. Add to `.env`: `OPENTRIPMAP_KEY=your_key`

Free tier: **500 requests/day**. At 100 venues/request, that's 500 venues per day — enough to seed the DB in one run.

### Eventbrite (free, no CC — but search is paywalled)

1. Create account at eventbrite.com
2. Account menu → Developer Links → API Keys → Request a new key
3. Fill in: name, `http://localhost:3000` as URL, any description
4. Copy the API Key
5. Add to `.env`: `EVENTBRITE_TOKEN=your_key`

Note: the key works for reading your own events. Public search (`/events/search/`) requires a paid approved plan.

### Overpass API

No key. Public service. Limit your request rate — don't send more than 1 request per second.

### NSW Open Data

No key. Download CSVs directly from [data.nsw.gov.au](https://data.nsw.gov.au).

---

## 16. Running the Pipeline

### First time setup

```bash
cd sydney-kids-activities

# Create virtual environment — keeps packages isolated from your system Python
python -m venv .venv
source .venv/bin/activate    # Mac/Linux
# .venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Create .env with your keys
echo "OPENTRIPMAP_KEY=your_key_here" > .env
echo "EVENTBRITE_TOKEN=your_token_here" >> .env

# Run
python pipeline.py
```

### Expected output (actual results from a real run)

```
Fetching venues via OpenTripMap...
  loaded 478 OpenTripMap venues
Fetching OSM venues via Overpass (fallback/supplement)...
  loaded 786 OSM venues
Loading NSW Open Data CSVs...
  [nsw] missing data/nsw_open_spaces.csv — skip
  [nsw] missing data/nsw_libraries.csv — skip
  [nsw] missing data/nsw_aquatic_centres.csv — skip
  loaded 0 NSW venues
Fetching Eventbrite events...
  [eventbrite] search endpoint unavailable — Eventbrite removed public search from free tier (post-2023)
  loaded 0 events

DB stats:
  venues [opentripmap]: 478
  venues [osm]: 786
  venues total: 1264
  events total: 0

Done → activities.db
```

### Run without specific sources

```bash
# No OTM key (Overpass only)
python -c "from pipeline import run; run(use_opentripmap=False)"

# Skip all venues, only events
python -c "from pipeline import run; run(use_opentripmap=False, use_overpass=False)"

# Skip events
python -c "from pipeline import run; run(eventbrite_pages=0)"
```

---

## 17. Querying the Database

### Using the CLI

```bash
sqlite3 activities.db
```

```sql
-- Count by source
SELECT source, count(*) FROM venues GROUP BY source;

-- Count by venue type
SELECT venue_type, count(*) FROM venues
GROUP BY venue_type ORDER BY count(*) DESC;

-- Free venues in a suburb
SELECT name, venue_type, website
FROM venues
WHERE address_suburb LIKE '%Parramatta%'
  AND is_free = 1;

-- Wheelchair accessible venues
SELECT name, address_suburb, venue_type
FROM venues
WHERE wheelchair_access = 'yes';

-- Events with age ranges
SELECT name, age_min, age_max, address_suburb
FROM events
WHERE age_min IS NOT NULL
ORDER BY age_min;

-- Events this week
SELECT name, start_datetime, address_suburb
FROM events
WHERE date(start_datetime) BETWEEN date('now') AND date('now', '+7 days');
```

### Using Python

```python
import sqlite3

conn = sqlite3.connect("activities.db")
conn.row_factory = sqlite3.Row  # results as dicts

rows = conn.execute("""
    SELECT name, venue_type, address_suburb
    FROM venues
    WHERE is_free = 1
    ORDER BY venue_type
""").fetchall()

for row in rows:
    print(f"{row['name']} ({row['venue_type']}) — {row['address_suburb']}")

conn.close()
```

### GUI tool

[DB Browser for SQLite](https://sqlitebrowser.org) — free, Mac/Windows/Linux. Open `activities.db`, browse tables, run queries visually.

---

## 18. Tradeoffs & Known Limitations

| Limitation | Impact | Fix in v2 |
|------------|--------|-----------|
| OSM unnamed nodes skipped | Lose minor venues | Not worth fixing — no name = unusable |
| NSW CSVs = manual download | Data goes stale | NSW Open Data API automation |
| Cross-source duplicates | Same venue appears in both OTM + OSM | Geo-distance dedup (< 50m = same place) |
| Eventbrite search = paywalled | 0 events on free tier | Meetup API, Humanitix, council feeds |
| Eventbrite age = regex heuristic | ~60% coverage when it works | Structured age field or ML classifier |
| SQLite = single writer | No concurrent writes | PostgreSQL for production |
| No caching | Re-fetches everything each run | Add `last_synced_at`, skip recent records |
| OTM kinds: `kids_places` invalid | Miss some kids-specific POIs | Check OTM taxonomy docs for valid values |
| 30km radius fixed | Misses outer suburbs | Configurable radius or multi-centre search |
| `format=json` has no coordinates | Would return 0 venues | Always use `format=geojson` |

---

## 19. Extending the Pipeline

### Add geo-distance dedup (v2)

After loading all sources, find venues within 50m of each other and keep only one:

```python
from math import radians, sin, cos, sqrt, atan2

def haversine_metres(lat1, lon1, lat2, lon2) -> float:
    R = 6371000  # Earth radius in metres
    φ1, φ2 = radians(lat1), radians(lat2)
    Δφ = radians(lat2 - lat1)
    Δλ = radians(lon2 - lon1)
    a = sin(Δφ/2)**2 + cos(φ1)*cos(φ2)*sin(Δλ/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

# If haversine_metres(v1.lat, v1.lon, v2.lat, v2.lon) < 50: same place
```

### Add a REST API with FastAPI

```python
# api.py
from fastapi import FastAPI
import sqlite3

app = FastAPI()

@app.get("/venues")
def get_venues(suburb: str | None = None, free: bool | None = None, age: int | None = None):
    conn = sqlite3.connect("activities.db")
    conn.row_factory = sqlite3.Row

    query = "SELECT * FROM venues WHERE 1=1"
    params = []

    if suburb:
        query += " AND address_suburb LIKE ?"
        params.append(f"%{suburb}%")
    if free is not None:
        query += " AND is_free = ?"
        params.append(1 if free else 0)
    if age is not None:
        query += " AND (age_min IS NULL OR age_min <= ?) AND (age_max IS NULL OR age_max >= ?)"
        params.extend([age, age])

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]
```

```bash
pip install fastapi uvicorn
uvicorn api:app --reload
# http://localhost:8000/venues?suburb=Parramatta&free=true&age=5
```

### Schedule daily refresh

```bash
# crontab -e  (runs at 6am every day)
0 6 * * * cd /path/to/sydney-kids-activities && source .venv/bin/activate && python pipeline.py >> logs/pipeline.log 2>&1
```

Upsert logic means re-runs are safe — no duplicates, records update in place.

### Add Meetup API as events source (Eventbrite replacement)

```python
# sources/meetup.py
import httpx

MEETUP_URL = "https://api.meetup.com/find/upcoming_events"

def fetch_sydney_events() -> list[Event]:
    resp = httpx.get(MEETUP_URL, params={
        "lat": -33.8688, "lon": 151.2093,
        "radius": 20, "topic_category": "family",
        "page": 50,
    })
    # ... parse and return Event objects
```

---

*Built for the Sydney Kids Activities project — Apr 2026. Every bug in section 13 was real.*
