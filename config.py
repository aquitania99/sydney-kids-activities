import os
from dotenv import load_dotenv

load_dotenv()

EVENTBRITE_TOKEN = os.environ.get("EVENTBRITE_TOKEN", "")
OPENTRIPMAP_KEY = os.environ.get("OPENTRIPMAP_KEY", "")

SYDNEY_BBOX = (-34.1, 150.5, -33.5, 151.4)  # south, west, north, east

NSW_DATA_FILES = {
    "parks": "data/nsw_open_spaces.csv",
    "libraries": "data/nsw_libraries.csv",
    "aquatic": "data/nsw_aquatic_centres.csv",
}
