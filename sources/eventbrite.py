import re
import httpx
from models import Event, AgeRange
from config import EVENTBRITE_TOKEN

BASE_URL = "https://www.eventbriteapi.com/v3"


def fetch_sydney_events(pages: int = 5) -> list[Event]:
    if not EVENTBRITE_TOKEN:
        print("  [eventbrite] EVENTBRITE_TOKEN not set — skip")
        return []

    headers = {"Authorization": f"Bearer {EVENTBRITE_TOKEN}"}
    events = []

    params = {
        "location.address": "Sydney, NSW, Australia",
        "location.within": "20km",
        "categories": "10",  # Kids & Family
        "expand": "venue",
        "page_size": 50,
    }

    for page in range(1, pages + 1):
        params["page"] = page
        resp = httpx.get(f"{BASE_URL}/events/search/", headers=headers, params=params, timeout=30)

        if resp.status_code == 429:
            print("  [eventbrite] rate limited — stopping pagination")
            break
        if resp.status_code == 401:
            print("  [eventbrite] invalid token — skip")
            break
        if resp.status_code == 404:
            print("  [eventbrite] search endpoint unavailable — Eventbrite removed public search from free tier (post-2023)")
            break
        resp.raise_for_status()

        data = resp.json()
        for ev in data.get("events", []):
            venue = ev.get("venue") or {}
            addr = venue.get("address") or {}

            name_text = ev.get("name", {}).get("text", "")
            desc_text = ev.get("description", {}).get("text", "")
            age_range = _extract_age_range(name_text + " " + desc_text)

            lat = venue.get("latitude")
            lon = venue.get("longitude")

            events.append(Event(
                source="eventbrite",
                external_id=ev["id"],
                name=name_text,
                description=desc_text[:500] if desc_text else None,
                start_datetime=ev["start"]["utc"],
                end_datetime=ev.get("end", {}).get("utc"),
                venue_name=venue.get("name"),
                latitude=float(lat) if lat else None,
                longitude=float(lon) if lon else None,
                address_suburb=addr.get("city"),
                is_free=ev.get("is_free", False),
                url=ev.get("url"),
                age_range=age_range,
            ))

        if not data.get("pagination", {}).get("has_more_items"):
            break

    return events


def _extract_age_range(text: str) -> AgeRange | None:
    m = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*year', text, re.I)
    if m:
        return AgeRange(min_age=int(m.group(1)), max_age=int(m.group(2)))
    m = re.search(r'age[sd]?\s+(\d+)\+', text, re.I)
    if m:
        return AgeRange(min_age=int(m.group(1)))
    m = re.search(r'under\s+(\d+)', text, re.I)
    if m:
        return AgeRange(max_age=int(m.group(1)))
    m = re.search(r'(\d+)\+\s*year', text, re.I)
    if m:
        return AgeRange(min_age=int(m.group(1)))
    return None
