"""Resolve geographic locations for context plugins."""

from __future__ import annotations

import ipaddress
import re
import time
from dataclasses import dataclass
from typing import Any

from agentforge.config import settings
from agentforge.context.base import ContextRequest
from agentforge.context.http_utils import fetch_json

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
IP_GEOLOCATION_URL = "https://ipwho.is"

LOCAL_LOCATION_PATTERN = re.compile(
    r"\b("
    r"hier|here|bei mir|wo ich bin|my location|near me|in der nähe|"
    r"local(?:ly)?|aktueller standort|current location|where i am"
    r")\b",
    re.I,
)

CITY_FROM_MESSAGE_PATTERN = re.compile(
    r"(?:wetter|weather|forecast|vorhersage|temperatur|temperature)"
    r"(?:\s+\([^)]+\))?"
    r"\s+(?:in|in der|für|for|at)\s+"
    r"([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\s\-']{1,40}?)"
    r"(?:\?|\.|$|\s+(?:heute|today|morgen|tomorrow))",
    re.I,
)

GENERIC_IN_CITY_PATTERN = re.compile(
    r"\b(?:in|in der|für|for|at)\s+"
    r"([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\s\-']{1,40}?)"
    r"(?:\?|\.|$|\s+(?:heute|today|morgen|tomorrow))",
    re.I,
)

_ip_geo_cache: dict[str, tuple[float, "ResolvedLocation"]] = {}


@dataclass(frozen=True)
class ResolvedLocation:
    """Coordinates and label for weather-like plugins."""

    latitude: float
    longitude: float
    label: str
    source: str
    timezone: str | None = None
    country_code: str | None = None
    city: str | None = None


def is_private_ip(ip_address: str) -> bool:
    """
    Return whether an IP belongs to a local or private network.

    :param ip_address: IPv4 or IPv6 address
    :return: True for loopback or private ranges
    """
    value = (ip_address or "").strip()
    if not value:
        return True
    try:
        return ipaddress.ip_address(value).is_private or ipaddress.ip_address(value).is_loopback
    except ValueError:
        return True


def extract_city_from_text(text: str) -> str | None:
    """
    Extract an explicit city from a weather-related user message.

    :param text: User message text
    :return: City name or None
    """
    normalized = (text or "").strip()
    if not normalized:
        return None

    for pattern in (CITY_FROM_MESSAGE_PATTERN, GENERIC_IN_CITY_PATTERN):
        match = pattern.search(normalized)
        if not match:
            continue
        city = match.group(1).strip(" .?!,")
        if city and city.lower() not in {"hier", "here", "mir", "me"}:
            return city
    return None


def wants_local_location(text: str) -> bool:
    """
    Detect whether the user asks for weather at their current location.

    :param text: User message text
    :return: True when local/IP-based location is requested
    """
    return bool(LOCAL_LOCATION_PATTERN.search(text or ""))


def should_prefer_ip_location(text: str) -> bool:
    """
    Decide whether IP geolocation should be used before configured defaults.

    :param text: User message text
    :return: True when no explicit city is named or local intent is present
    """
    if wants_local_location(text):
        return True
    if extract_city_from_text(text):
        return False
    return True


async def geocode_city(city: str) -> ResolvedLocation | None:
    """
    Resolve a city name to coordinates via Open-Meteo geocoding.

    :param city: City name
    :return: Resolved location or None
    """
    name = city.strip()
    if not name:
        return None

    payload = await fetch_json(GEOCODING_URL, params={"name": name, "count": 1, "language": "en"})
    results = payload.get("results") or []
    if not results:
        return None

    hit = results[0]
    label_parts = [str(hit.get("name") or name)]
    if hit.get("country_code"):
        label_parts.append(str(hit["country_code"]))
    return ResolvedLocation(
        latitude=float(hit["latitude"]),
        longitude=float(hit["longitude"]),
        label=", ".join(label_parts),
        source="user_city",
        timezone=str(hit.get("timezone") or "") or None,
        country_code=str(hit.get("country_code") or "") or None,
        city=str(hit.get("name") or name),
    )


async def geolocate_from_ip(client_ip: str = "") -> ResolvedLocation | None:
    """
    Resolve approximate location from the client or server public IP.

    When the client connects from localhost, the lookup uses the machine's
    public egress IP, which matches a typical local desktop install.

    :param client_ip: Optional client IP from the incoming request
    :return: Resolved location or None
    """
    if not settings.context_ip_geolocation_enabled:
        return None

    cache_key = client_ip.strip() if client_ip and not is_private_ip(client_ip) else "local"
    ttl = max(60.0, float(settings.context_ip_geolocation_cache_seconds))
    cached = _ip_geo_cache.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < ttl:
        return cached[1]

    url = IP_GEOLOCATION_URL
    if client_ip and not is_private_ip(client_ip):
        url = f"{IP_GEOLOCATION_URL}/{client_ip}"

    payload = await fetch_json(url)
    if not payload.get("success"):
        return None

    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    if latitude is None or longitude is None:
        return None

    city = str(payload.get("city") or "").strip()
    region = str(payload.get("region") or "").strip()
    country = str(payload.get("country") or "").strip()
    country_code = str(payload.get("country_code") or "").strip() or None
    timezone_value = payload.get("timezone")
    timezone: str | None
    if isinstance(timezone_value, dict):
        timezone = str(timezone_value.get("id") or "") or None
    elif isinstance(timezone_value, str):
        timezone = timezone_value or None
    else:
        timezone = None
    label_parts = [part for part in (city, region, country_code or country) if part]
    label = ", ".join(label_parts) if label_parts else country or "Current location"

    resolved = ResolvedLocation(
        latitude=float(latitude),
        longitude=float(longitude),
        label=label,
        source="ip_geolocation",
        timezone=timezone,
        country_code=country_code,
        city=city or None,
    )
    _ip_geo_cache[cache_key] = (now, resolved)
    return resolved


async def resolve_location(request: ContextRequest) -> ResolvedLocation | None:
    """
    Resolve the best available location for weather-like plugins.

    Priority:
    1. Explicit latitude/longitude from settings
    2. City extracted from the user message
    3. IP geolocation when the user asks locally or names no city
    4. Configured default city

    :param request: Context request payload
    :return: Resolved location or None
    """
    lat = settings.context_latitude
    lon = settings.context_longitude
    if lat is not None and lon is not None:
        label = settings.context_city.strip() or settings.context_country_code
        return ResolvedLocation(
            latitude=lat,
            longitude=lon,
            label=label,
            source="config_coordinates",
            timezone=settings.context_timezone.strip() or None,
            country_code=settings.context_country_code or None,
            city=settings.context_city.strip() or None,
        )

    user_text = request.user_content or ""
    city_from_message = extract_city_from_text(user_text)
    if city_from_message:
        geocoded = await geocode_city(city_from_message)
        if geocoded is not None:
            return geocoded

    if should_prefer_ip_location(user_text):
        ip_location = await geolocate_from_ip(request.client_ip)
        if ip_location is not None:
            return ip_location

    configured_city = settings.context_city.strip()
    if configured_city:
        geocoded = await geocode_city(configured_city)
        if geocoded is not None:
            return ResolvedLocation(
                latitude=geocoded.latitude,
                longitude=geocoded.longitude,
                label=geocoded.label,
                source="config_city",
                timezone=geocoded.timezone or settings.context_timezone.strip() or None,
                country_code=geocoded.country_code or settings.context_country_code or None,
                city=geocoded.city or configured_city,
            )

    return await geolocate_from_ip(request.client_ip)


def location_source_label(source: str) -> str:
    """
    Return a short human-readable label for a location source.

    :param source: Internal source identifier
    :return: Display label
    """
    labels = {
        "config_coordinates": "configured coordinates",
        "config_city": "configured city",
        "user_city": "named city",
        "ip_geolocation": "IP geolocation",
    }
    return labels.get(source, source)
