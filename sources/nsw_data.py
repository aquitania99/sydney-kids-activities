import pandas as pd
from pathlib import Path
from models import Venue
from config import NSW_DATA_FILES


def load_nsw_venues() -> list[Venue]:
    venues = []

    for venue_type, path in NSW_DATA_FILES.items():
        if not Path(path).exists():
            print(f"  [nsw] missing {path} — skip")
            continue

        df = pd.read_csv(path)
        df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")

        print(f"  [nsw] {path} columns: {list(df.columns)}")

        for _, row in df.iterrows():
            name = _first(row, ["name", "facility_name", "title", "site_name"])
            lat = _first(row, ["latitude", "lat", "y", "lat_wgs84"])
            lon = _first(row, ["longitude", "lon", "long", "x", "lng_wgs84"])

            if pd.isna(name) or pd.isna(lat) or pd.isna(lon):
                continue

            try:
                lat, lon = float(lat), float(lon)
            except (ValueError, TypeError):
                continue

            suburb = _first(row, ["suburb", "city", "lga", "locality"])
            postcode = _first(row, ["postcode", "post_code"])
            ext_id = _first(row, ["objectid", "id", "feature_id"]) or str(abs(hash(str(name) + str(lat))))

            venues.append(Venue(
                source="nsw",
                external_id=str(ext_id),
                name=str(name),
                latitude=lat,
                longitude=lon,
                address_suburb=str(suburb) if suburb and not pd.isna(suburb) else None,
                address_postcode=str(postcode) if postcode and not pd.isna(postcode) else None,
                venue_type=venue_type,
            ))

    return venues


def _first(row, keys):
    for k in keys:
        v = row.get(k)
        if v is not None and not (isinstance(v, float) and pd.isna(v)):
            return v
    return None
