from pydantic import BaseModel
from typing import Optional


class AgeRange(BaseModel):
    min_age: Optional[int] = None
    max_age: Optional[int] = None


class Venue(BaseModel):
    source: str
    external_id: str
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
    description: Optional[str] = None
    start_datetime: str
    end_datetime: Optional[str] = None
    venue_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address_suburb: Optional[str] = None
    is_free: bool = False
    url: Optional[str] = None
    age_range: Optional[AgeRange] = None
