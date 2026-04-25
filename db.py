import sqlite3
from models import Venue, Event


def get_conn(path: str = "activities.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS venues (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source            TEXT NOT NULL,
            external_id       TEXT NOT NULL,
            name              TEXT NOT NULL,
            latitude          REAL,
            longitude         REAL,
            address_street    TEXT,
            address_suburb    TEXT,
            address_postcode  TEXT,
            venue_type        TEXT,
            is_free           INTEGER,
            is_indoor         INTEGER,
            website           TEXT,
            phone             TEXT,
            wheelchair_access TEXT,
            age_min           INTEGER,
            age_max           INTEGER,
            UNIQUE(source, external_id)
        );

        CREATE TABLE IF NOT EXISTS events (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            source           TEXT NOT NULL,
            external_id      TEXT NOT NULL,
            name             TEXT NOT NULL,
            description      TEXT,
            start_datetime   TEXT,
            end_datetime     TEXT,
            venue_name       TEXT,
            latitude         REAL,
            longitude        REAL,
            address_suburb   TEXT,
            is_free          INTEGER,
            url              TEXT,
            age_min          INTEGER,
            age_max          INTEGER,
            UNIQUE(source, external_id)
        );
    """)
    conn.commit()


def upsert_venue(conn: sqlite3.Connection, v: Venue) -> None:
    conn.execute("""
        INSERT INTO venues (
            source, external_id, name, latitude, longitude,
            address_street, address_suburb, address_postcode, venue_type,
            is_free, is_indoor, website, phone, wheelchair_access, age_min, age_max
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source, external_id) DO UPDATE SET
            name=excluded.name,
            latitude=excluded.latitude,
            longitude=excluded.longitude,
            is_free=excluded.is_free,
            website=excluded.website,
            phone=excluded.phone
    """, (
        v.source, v.external_id, v.name, v.latitude, v.longitude,
        v.address_street, v.address_suburb, v.address_postcode, v.venue_type,
        v.is_free, v.is_indoor, v.website, v.phone, v.wheelchair_access,
        v.age_range.min_age if v.age_range else None,
        v.age_range.max_age if v.age_range else None,
    ))
    conn.commit()


def upsert_event(conn: sqlite3.Connection, e: Event) -> None:
    conn.execute("""
        INSERT INTO events (
            source, external_id, name, description, start_datetime,
            end_datetime, venue_name, latitude, longitude, address_suburb,
            is_free, url, age_min, age_max
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source, external_id) DO UPDATE SET
            name=excluded.name,
            start_datetime=excluded.start_datetime,
            end_datetime=excluded.end_datetime
    """, (
        e.source, e.external_id, e.name, e.description, e.start_datetime,
        e.end_datetime, e.venue_name, e.latitude, e.longitude, e.address_suburb,
        e.is_free, e.url,
        e.age_range.min_age if e.age_range else None,
        e.age_range.max_age if e.age_range else None,
    ))
    conn.commit()


def stats(conn: sqlite3.Connection) -> None:
    for row in conn.execute("SELECT source, count(*) as n FROM venues GROUP BY source"):
        print(f"  venues [{row['source']}]: {row['n']}")
    total_venues = conn.execute("SELECT count(*) FROM venues").fetchone()[0]
    total_events = conn.execute("SELECT count(*) FROM events").fetchone()[0]
    print(f"  venues total: {total_venues}")
    print(f"  events total: {total_events}")
