import httpx
import time
from models import Venue
from config import OPENTRIPMAP_KEY

BASE_URL = "https://api.opentripmap.com/0.1/en/places"

# Sydney CBD centre — we paginate outward via offset
SYDNEY_LAT = -33.8688
SYDNEY_LON = 151.2093

# Relevant kinds for kids activities
# Full taxonomy: https://dev.opentripmap.org/docs
KIDS_KINDS = ",".join([
    "sport",
    "amusements",
    "natural",
    "cultural",
])


def fetch_sydney_venues(radius_km: int = 30, limit: int = 500) -> list[Venue]:
    """
    Fetch POIs within radius_km of Sydney CBD.
    Returns up to `limit` venues across all KIDS_KINDS categories.
    Uses the /radius endpoint — simpler than bbox, handles pagination via offset.
    """
    if not OPENTRIPMAP_KEY:
        print("  [opentripmap] OPENTRIPMAP_KEY not set — skip")
        return []

    venues = []
    offset = 0
    page_size = 100  # API max per request

    while len(venues) < limit:
        params = {
            "radius": radius_km * 1000,
            "lon": SYDNEY_LON,
            "lat": SYDNEY_LAT,
            "limit": min(page_size, limit - len(venues)),
            "offset": offset,
            "format": "geojson",
            "apikey": OPENTRIPMAP_KEY,
        }
        # kinds must NOT be URL-encoded — build URL manually to keep literal commas
        query_string = str(httpx.QueryParams(params)) + f"&kinds={KIDS_KINDS}"
        resp = httpx.get(f"{BASE_URL}/radius?{query_string}", timeout=30)

        if resp.status_code == 429:
            print("  [opentripmap] rate limited — stopping")
            break
        resp.raise_for_status()

        data = resp.json()
        items = data.get("features", []) if isinstance(data, dict) else data
        if not items:
            break

        for item in items:
            venue = _parse_item(item)
            if venue:
                venues.append(venue)

        offset += len(items)

        if len(items) < page_size:
            break  # last page

        time.sleep(0.2)  # be polite — free tier rate limit

    return venues


def _parse_item(item: dict) -> Venue | None:
    props = item.get("properties", {})
    name = props.get("name")
    if not name or not name.strip():
        return None
    geom = item.get("geometry", {}).get("coordinates", [])
    if len(geom) < 2:
        return None
    lon, lat = geom[0], geom[1]  # GeoJSON order: lon first
    return Venue(
        source="opentripmap",
        external_id=props.get("xid", ""),
        name=name.strip(),
        latitude=float(lat),
        longitude=float(lon),
        venue_type=_primary_kind(props.get("kinds", "")),
    )


def enrich_venue(xid: str) -> dict:
    """
    Fetch full detail for a single POI by xid.
    Returns raw dict with address, website, phone, opening_hours etc.
    Call this selectively — costs 1 req per venue.
    """
    if not OPENTRIPMAP_KEY:
        return {}

    resp = httpx.get(
        f"{BASE_URL}/xid/{xid}",
        params={"apikey": OPENTRIPMAP_KEY},
        timeout=15,
    )
    if resp.status_code != 200:
        return {}

    data = resp.json()
    addr = data.get("address", {})

    return {
        "address_street": addr.get("road"),
        "address_suburb": addr.get("suburb") or addr.get("city_district"),
        "address_postcode": addr.get("postcode"),
        "website": data.get("url") or data.get("wikipedia"),
        "phone": data.get("phone"),
        "is_indoor": data.get("preview", {}).get("indoor"),
        "wheelchair_access": data.get("wheelchair"),
    }


def _primary_kind(kinds: str) -> str:
    """Return the most specific kind from a comma-separated kinds string."""
    priority = [
        "kids_places", "amusements", "sport", "leisure",
        "natural", "cultural", "other",
    ]
    kind_list = [k.strip() for k in kinds.split(",")]
    for p in priority:
        if p in kind_list:
            return p
    return kind_list[0] if kind_list else "unknown"
