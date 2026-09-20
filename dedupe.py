"""
dedupe.py
---------
Phase 6: Data Cleaning & Duplicate Detection.

Normalizes the messy real-world fields a lead can arrive with (phone
formatting, website variants, "Inc."/"LLC" suffixes on a name) and
checks a new company-lead candidate against every existing company
lead already in the `leads` table, before it gets saved.

Per the project's hard rule, duplicates are never decided on name
similarity alone. A match is only called "confident" (safe to skip
as an existing lead) when a strong signal lines up - same phone
number, same website domain, or the same name together with the
same city/state. A weaker, partial signal is flagged as "uncertain"
instead of silently merged or silently duplicated, so you can look
at it yourself on the lead's notes.
"""

import re

from database import get_connection

# Common legal suffixes that make two identical companies look like
# different strings ("Acme Roofing" vs "Acme Roofing LLC").
_NAME_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co)\.?\b",
    re.IGNORECASE,
)


def normalize_phone(raw):
    """Strips a phone number down to digits only, so different
    formatting of the same number always compares equal. Also drops a
    leading US country code ('1') if present, so '+1 310-555-1234'
    and '310.555.1234' match."""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def normalize_website(raw):
    """Reduces a website down to just its domain, so 'https://www.
    acme.com/', 'http://acme.com', and 'acme.com/contact' all match."""
    if not raw:
        return ""
    site = raw.strip().lower()
    site = re.sub(r"^https?://", "", site)
    site = re.sub(r"^www\.", "", site)
    site = site.split("/")[0]
    return site


def normalize_name(raw):
    """Lowercases a company name, drops common legal suffixes and
    punctuation, and collapses extra whitespace."""
    if not raw:
        return ""
    name = raw.strip().lower()
    name = _NAME_SUFFIXES.sub("", name)
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def normalize_state(raw):
    """Always compares state as a 2-letter uppercase code."""
    return (raw or "").strip().upper()[:2]


def find_match(candidate):
    """
    Checks one new company-lead candidate (a dict shaped like the one
    save_found_leads() builds, before it's passed to create_lead())
    against every existing company lead.

    Returns one of:
      {"match": "confident", "lead_id": <id>, "reason": "..."}
          Already in the database - the caller should skip creating
          a new row.
      {"match": "uncertain", "lead_id": <id>, "reason": "..."}
          A partial signal lined up but it isn't strong enough to be
          sure - the caller should still save it, but flag it for a
          human to check.
      {"match": "none"}
          No existing lead looks like this one - safe to save as new.
    """
    phone = normalize_phone(candidate.get("phone", ""))
    website = normalize_website(candidate.get("website", ""))
    name = normalize_name(candidate.get("company_name", ""))
    city = (candidate.get("city") or "").strip().lower()
    state = normalize_state(candidate.get("state", ""))

    conn = get_connection()
    rows = conn.execute(
        "SELECT id, company_name, phone, website, city, state FROM leads "
        "WHERE lead_type = 'company'"
    ).fetchall()
    conn.close()

    for row in rows:
        existing_phone = normalize_phone(row["phone"])
        existing_website = normalize_website(row["website"])
        existing_name = normalize_name(row["company_name"])
        existing_city = (row["city"] or "").strip().lower()
        existing_state = normalize_state(row["state"])

        phone_match = bool(phone) and phone == existing_phone
        website_match = bool(website) and website == existing_website
        name_location_match = (
            bool(name) and name == existing_name
            and city == existing_city and state == existing_state
        )

        if phone_match or website_match or name_location_match:
            reason_bits = []
            if phone_match:
                reason_bits.append("same phone number")
            if website_match:
                reason_bits.append("same website")
            if name_location_match:
                reason_bits.append("same name + city/state")
            return {
                "match": "confident",
                "lead_id": row["id"],
                "reason": " and ".join(reason_bits),
            }

        # Weaker signal: the name matches but only city OR state also
        # matches (not both) - could be the same company that moved,
        # or could be two different companies with a similar name.
        # Not confident either way, so this gets flagged instead of
        # decided automatically.
        name_only_match = bool(name) and name == existing_name
        if name_only_match and (city == existing_city or state == existing_state):
            return {
                "match": "uncertain",
                "lead_id": row["id"],
                "reason": "same name, only partial location match - please review",
            }

    return {"match": "none"}
