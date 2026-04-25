import urllib.parse
import urllib.request
from models import Venue
from config import SYDNEY_BBOX

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

QUERY_TEMPLATE = """
[out:json][timeout:60];
(
  node["leisure"="playground"]({bbox});
  node["amenity"="swimming_pool"]({bbox});
  node["amenity"="library"]({bbox});
  node["leisure"="sports_centre"]({bbox});
  node["leisure"="park"]({bbox});
  way["leisure"="playground"]({bbox});
  way["amenity"="swimming_pool"]({bbox});
  way["leisure"="sports_centre"]({bbox});
);
out center tags;
"""


def fetch_sydney_venues() -> list[Venue]:
    bbox = ",".join(map(str, SYDNEY_BBOX))
    query = QUERY_TEMPLATE.format(bbox=bbox)

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
        import json
        elements = json.loads(r.read())["elements"]

    venues = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if not lat or not lon:
            continue

        venue_type = tags.get("leisure") or tags.get("amenity") or "unknown"

        fee_tag = tags.get("fee")
        is_free = False if fee_tag == "yes" else (True if fee_tag == "no" else None)

        venues.append(Venue(
            source="osm",
            external_id=f"{el['type']}/{el['id']}",
            name=name,
            latitude=float(lat),
            longitude=float(lon),
            address_street=tags.get("addr:street"),
            address_suburb=tags.get("addr:suburb"),
            address_postcode=tags.get("addr:postcode"),
            venue_type=venue_type,
            is_free=is_free,
            is_indoor=tags.get("indoor") == "yes" or None,
            website=tags.get("website"),
            phone=tags.get("phone"),
            wheelchair_access=tags.get("wheelchair"),
        ))

    return venues
