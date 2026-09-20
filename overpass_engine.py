"""
overpass_engine.py
--------------------
Phase 5: Lead Finder.

Finds real, already-existing businesses using two free, keyless, public
OpenStreetMap services - no account, no API key, no card required, and
both are meant to be used this way by any app:

  1. Nominatim  - turns a place name ("Austin, TX", a ZIP code, a city)
     into a center point (a latitude/longitude).
     https://nominatim.org/release-docs/latest/api/Search/

  2. Overpass API - the actual business search, run inside a search
     area we build ourselves around that point.
     https://overpass-api.de/

We are polite about both, on purpose:
  - only ever one request at a time, never in parallel
  - a real, descriptive User-Agent that names this app (Nominatim's
    usage policy asks for this so they know who's calling)
  - a short pause between the Nominatim call and the Overpass call
  - no retry-hammering - if a call fails, we say so and stop

SEARCH AREA - why this changed
-------------------------------
The old version asked Nominatim for a bounding box and used it directly.
That box is not consistent: for some places Nominatim hands back a huge
box (an entire metro area or county), and for others a tiny one (just a
downtown core or a single postal point). A search against a tiny box is
why a city could come back with only one real result even though it
obviously has many more businesses than that on OpenStreetMap.

So instead, we only ask Nominatim for the one thing it gives back
reliably for every place: a center point (lat/lon). We then build our
own fixed-size box around that point - the same radius every time, for
every city - so search area is never a matter of luck.

We never invent data. Every field on a result is marked
VERIFIED / FOUND / UNVERIFIED / NOT FOUND based on exactly what
OpenStreetMap has on record for that business - nothing is guessed.

  VERIFIED   - the field is present and in a clean, well-formed shape
               (e.g. a phone number that looks like a phone number, a
               website that starts with http/https, a name tag rather
               than just a brand tag).
  FOUND      - the field is present, but in a rougher shape (e.g. a
               phone number with unusual formatting).
  UNVERIFIED - we have something, but it's a weaker stand-in for the
               real field (e.g. the business name came from a "brand"
               tag rather than a proper "name" tag).
  NOT FOUND  - OpenStreetMap simply has nothing for that field. This is
               normal and common, especially for email - OSM almost
               never has business email addresses.
"""

import math
import re
import time

import requests

import config

USER_AGENT = (
    f"{config.APP_NAME}/1.0 (personal lead-gen tool, single user; "
    f"built by {config.CREATOR_NAME})"
)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# The fixed search radius used around every city's center point, in
# kilometers. ~20km (~12 miles) covers "the whole metro area", not just
# a downtown core, for essentially every city on the dropdown list -
# without also swallowing a neighboring major city for the tightly
# packed ones. Passed through discover_companies() if it ever needs to
# be adjustable per-search later.
DEFAULT_RADIUS_KM = 20

# Roughly how many km one degree of latitude covers, everywhere on
# Earth. Longitude degrees shrink toward the poles, which is why the
# longitude offset below also divides by cos(latitude).
KM_PER_DEGREE_LAT = 111.0

# A modest dictionary of common trades mapped to the actual tag OpenStreetMap
# uses for that kind of business. This is what makes results precise for a
# trade we know about. Feel free to add more rows here later if a trade you
# search for often comes back thin - that's the only file you'd need to touch.
KNOWN_TRADES = {
    "architect": [("office", "architect")],
    "engineer": [("office", "engineer")],
    "engineering": [("office", "engineer")],
    "contractor": [("office", "construction"), ("craft", "builder")],
    "general contractor": [("office", "construction"), ("craft", "builder")],
    "construction": [("office", "construction"), ("craft", "builder")],
    "builder": [("craft", "builder")],
    "electrician": [("craft", "electrician")],
    "electrical": [("craft", "electrician")],
    "plumber": [("craft", "plumber")],
    "plumbing": [("craft", "plumber")],
    "hvac": [("craft", "hvac")],
    "heating": [("craft", "hvac")],
    "roofer": [("craft", "roofer")],
    "roofing": [("craft", "roofer")],
    "painter": [("craft", "painter")],
    "painting": [("craft", "painter")],
    "landscaper": [("craft", "gardener")],
    "landscaping": [("craft", "gardener"), ("shop", "garden_centre")],
    "mason": [("craft", "stonemason")],
    "masonry": [("craft", "stonemason")],
    "carpenter": [("craft", "carpenter")],
    "carpentry": [("craft", "carpenter")],
    "flooring": [("shop", "flooring")],
    "insulation": [("craft", "insulation")],
    "welder": [("craft", "metal_construction")],
    "welding": [("craft", "metal_construction")],
    "fence": [("shop", "fence")],
    "fencing": [("shop", "fence")],
    "glass": [("craft", "glaziery")],
    "glazier": [("craft", "glaziery")],
    "handyman": [("craft", "handyman")],
    "drywall": [("craft", "plasterer")],
    "drywaller": [("craft", "plasterer")],
    "plasterer": [("craft", "plasterer")],
    "plastering": [("craft", "plasterer")],
    "real estate agent": [("office", "estate_agent")],
    "real estate developer": [("office", "estate_agent")],
    "property manager": [("office", "estate_agent")],
    "interior designer": [("office", "interior_decorator")],
    "interior design": [("office", "interior_decorator")],
    "surveyor": [("office", "surveyor")],
    "lawyer": [("office", "lawyer"), ("amenity", "lawyer")],
    "attorney": [("office", "lawyer"), ("amenity", "lawyer")],
    "accountant": [("office", "accountant")],
}


class OverpassEngineError(Exception):
    """Raised when we can't finish a Lead Finder search, with a message
    that's safe to show directly to the user."""
    pass


def _match_known_trades(keyword):
    """Looks the typed industry up against KNOWN_TRADES. Matches loosely
    in both directions ('roof' matches 'roofer', 'general contractor'
    matches 'contractor') so small variations in wording still work."""
    matches = []
    for trade, tags in KNOWN_TRADES.items():
        if trade in keyword or keyword in trade:
            matches.extend(tags)
    seen = set()
    unique = []
    for pair in matches:
        if pair not in seen:
            seen.add(pair)
            unique.append(pair)
    return unique


def geocode_location(location):
    """Turns a typed location into a center point using Nominatim.

    Returns (lat, lon, display_name). We deliberately do NOT use
    Nominatim's own bounding box here - see the module docstring for
    why - only the center point, which Nominatim gives back reliably
    for basically any place name, ZIP code, or "City, ST" string.
    """
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": location, "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException:
        raise OverpassEngineError(
            "Could not reach OpenStreetMap's location lookup (Nominatim) "
            "right now. It's free and usually reliable - try again in a "
            "moment."
        )

    results = resp.json()
    if not results:
        raise OverpassEngineError(
            f'Could not find a location matching "{location}". Try a '
            "city and state, or a ZIP code."
        )

    lat = float(results[0]["lat"])
    lon = float(results[0]["lon"])
    display_name = results[0].get("display_name", location)
    return lat, lon, display_name


def _bbox_from_center(lat, lon, radius_km=DEFAULT_RADIUS_KM):
    """Builds a fixed-size bounding box around a center point instead of
    trusting Nominatim's own bounding box, which varies wildly in size
    from city to city. Same radius every time, for every city, so the
    amount of ground a search covers is never a matter of luck.

    Returns (south, west, north, east).
    """
    lat_delta = radius_km / KM_PER_DEGREE_LAT
    # A degree of longitude covers less ground the further you are from
    # the equator, so this narrows as |lat| grows. Guard against the
    # (extremely unlikely, for US cities) case of lat = +/-90.
    lon_km_per_degree = KM_PER_DEGREE_LAT * math.cos(math.radians(lat))
    lon_km_per_degree = max(lon_km_per_degree, 1.0)
    lon_delta = radius_km / lon_km_per_degree

    south = lat - lat_delta
    north = lat + lat_delta
    west = lon - lon_delta
    east = lon + lon_delta
    return south, west, north, east


def _query_overpass(ql):
    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": ql},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException:
        raise OverpassEngineError(
            "Could not reach OpenStreetMap's Overpass API right now. It's "
            "a free, shared service and sometimes busy - try again in a "
            "minute."
        )
    return resp.json()


def _classify_phone(raw):
    if not raw:
        return "", "NOT FOUND"
    if re.match(r"^[\d\s\-\+\(\)]{7,}$", raw):
        return raw, "VERIFIED"
    return raw, "FOUND"


def _classify_website(raw):
    if not raw:
        return "", "NOT FOUND"
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw, "VERIFIED"
    return raw, "FOUND"


def _classify_email(raw):
    if raw and "@" in raw:
        return raw, "FOUND"
    return "", "NOT FOUND"


def discover_companies(industry, location, services="", limit=40,
                        radius_km=DEFAULT_RADIUS_KM):
    """
    The main entry point. Searches for real businesses matching `industry`
    near `location`. `services` is just carried through onto each result
    (the services you sell, for your own reference on the lead) - it does
    not affect the search itself. `radius_km` controls how wide an area
    around `location`'s center point gets searched (default 20km/~12mi).

    Returns a list of dicts, each shaped to match LEAD_FIELDS in
    database.py, plus an extra "quality" dict for the results page to
    display (not saved to the database).
    """
    keyword = (industry or "").strip().lower()
    if not keyword:
        raise OverpassEngineError("Enter an industry or trade to search for.")

    lat, lon, place_name = geocode_location(location)
    south, west, north, east = _bbox_from_center(lat, lon, radius_km)

    # Be polite: pause between the Nominatim call above and the Overpass
    # call below, rather than firing them back-to-back.
    time.sleep(1)

    tag_filters = _match_known_trades(keyword)
    escaped_keyword = re.sub(r'["\\]', "", keyword)

    lines = []
    for osm_key, osm_value in tag_filters:
        lines.append(f'node["{osm_key}"="{osm_value}"]({south},{west},{north},{east});')
        lines.append(f'way["{osm_key}"="{osm_value}"]({south},{west},{north},{east});')
    # Always also try a plain text match on the business name, so an
    # industry we don't have in KNOWN_TRADES still returns something.
    lines.append(f'node["name"~"{escaped_keyword}",i]({south},{west},{north},{east});')
    lines.append(f'way["name"~"{escaped_keyword}",i]({south},{west},{north},{east});')

    ql = "[out:json][timeout:25];\n(\n" + "\n".join(lines) + f"\n);\nout center {limit * 3};"

    data = _query_overpass(ql)
    elements = data.get("elements", [])

    seen = set()
    candidates = []

    for el in elements:
        tags = el.get("tags", {})

        name = tags.get("name") or tags.get("brand")
        if not name:
            # No usable name - not worth showing as a lead.
            continue
        name = name.strip()
        name_status = "VERIFIED" if tags.get("name") else "UNVERIFIED"

        phone_raw, phone_status = _classify_phone(
            tags.get("phone") or tags.get("contact:phone")
        )
        website_raw, website_status = _classify_website(
            tags.get("website") or tags.get("contact:website") or tags.get("url")
        )
        email_raw, email_status = _classify_email(
            tags.get("email") or tags.get("contact:email")
        )

        street = " ".join(
            p for p in [tags.get("addr:housenumber"), tags.get("addr:street")] if p
        )
        city = tags.get("addr:city", "")
        state = tags.get("addr:state", "")
        address_status = "VERIFIED" if (street or city) else "NOT FOUND"

        dedupe_key = (name.lower(), phone_raw, website_raw)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        matched_tag = None
        for osm_key, osm_value in tag_filters:
            if tags.get(osm_key) == osm_value:
                matched_tag = f"{osm_key}={osm_value}"
                break

        notes_bits = ["Found via OpenStreetMap (Overpass API)."]
        if matched_tag:
            notes_bits.append(f"Matched category: {matched_tag}.")
        if street or city or state:
            notes_bits.append(
                "Address: " + ", ".join(p for p in [street, city, state] if p) + "."
            )
        notes_bits.append(
            f"Data quality - Name: {name_status}, Phone: {phone_status}, "
            f"Website: {website_status}, Email: {email_status}, "
            f"Address: {address_status}."
        )

        candidates.append({
            "company_name": name,
            "phone": phone_raw,
            "website": website_raw,
            "email": email_raw,
            "city": city,
            "state": state,
            "industry": industry,
            "services_needed": services,
            "lead_source": "OpenStreetMap (Overpass API)",
            "notes": " ".join(notes_bits),
            "quality": {
                "name": name_status,
                "phone": phone_status,
                "website": website_status,
                "email": email_status,
                "address": address_status,
            },
        })

        if len(candidates) >= limit:
            break

    return candidates
