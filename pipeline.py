from sources.overpass import fetch_sydney_venues as fetch_osm_venues
from sources.opentripmap import fetch_sydney_venues as fetch_otm_venues
from sources.nsw_data import load_nsw_venues
from sources.eventbrite import fetch_sydney_events
from db import get_conn, upsert_venue, upsert_event, stats


def run(
    use_opentripmap: bool = True,
    use_overpass: bool = True,
    eventbrite_pages: int = 5,
) -> None:
    """
    Run the full pipeline. By default both venue sources are active.
    Set use_opentripmap=False to skip OpenTripMap (e.g. no API key).
    Set use_overpass=False to skip raw OSM Overpass.
    Running both gives maximum coverage with dedup via UNIQUE(source, external_id).
    """
    conn = get_conn()

    if use_opentripmap:
        print("Fetching venues via OpenTripMap...")
        otm_venues = fetch_otm_venues(radius_km=30, limit=500)
        for v in otm_venues:
            upsert_venue(conn, v)
        print(f"  loaded {len(otm_venues)} OpenTripMap venues")

    if use_overpass:
        print("Fetching OSM venues via Overpass (fallback/supplement)...")
        osm_venues = fetch_osm_venues()
        for v in osm_venues:
            upsert_venue(conn, v)
        print(f"  loaded {len(osm_venues)} OSM venues")

    print("Loading NSW Open Data CSVs...")
    nsw_venues = load_nsw_venues()
    for v in nsw_venues:
        upsert_venue(conn, v)
    print(f"  loaded {len(nsw_venues)} NSW venues")

    print("Fetching Eventbrite events...")
    events = fetch_sydney_events(pages=eventbrite_pages)
    for e in events:
        upsert_event(conn, e)
    print(f"  loaded {len(events)} events")

    print("\nDB stats:")
    stats(conn)
    conn.close()
    print("\nDone → activities.db")


if __name__ == "__main__":
    run()
