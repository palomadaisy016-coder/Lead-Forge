"""
find_duplicates.py
-------------------
A one-time CLEANUP REPORT for duplicates that are already sitting in
your leads table - from before Phase 6's dedupe check was actually
running on new saves. It does NOT delete or change anything. It just
reads your leads and prints out which ones look like the same real
company, using the exact same phone/website/name+location signals
dedupe.py already uses for new saves.

You decide what to do with each group - usually that means opening
one of the two leads in the app and clicking Delete on the one you
don't want to keep.

HOW TO RUN
----------
From your project folder (same place you run "python app.py"):

    python find_duplicates.py

It will print something like:

    Group 1 - same phone number
      Lead #2  Arlington Luxury Home Builders   Miami         HVAC contractor
      Lead #19 Arlington Luxury Home Builders   Miami         DRYWALL CONTRACTORS

    Group 2 - same name + city/state
      Lead #5  ...

That's it - nothing in your database is touched by running this.
"""

from database import get_connection
from dedupe import normalize_phone, normalize_website, normalize_name, normalize_state


def find_all_duplicate_groups():
    """Reads every company lead once, then groups them by whichever
    signal(s) match - same phone, same website, or same name+location.
    Returns a list of (reason, [row, row, ...]) groups, each with 2+
    leads that look like the same real company."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, company_name, phone, website, city, state, industry, "
        "created_at FROM leads WHERE lead_type = 'company' ORDER BY id"
    ).fetchall()
    conn.close()

    enriched = []
    for row in rows:
        enriched.append({
            "row": row,
            "phone": normalize_phone(row["phone"]),
            "website": normalize_website(row["website"]),
            "name": normalize_name(row["company_name"]),
            "city": (row["city"] or "").strip().lower(),
            "state": normalize_state(row["state"]),
        })

    groups = []
    already_grouped = set()

    for i, a in enumerate(enriched):
        if a["row"]["id"] in already_grouped:
            continue
        matches = []
        reasons = set()

        for b in enriched[i + 1:]:
            if b["row"]["id"] in already_grouped:
                continue

            phone_match = bool(a["phone"]) and a["phone"] == b["phone"]
            website_match = bool(a["website"]) and a["website"] == b["website"]
            name_location_match = (
                bool(a["name"]) and a["name"] == b["name"]
                and a["city"] == b["city"] and a["state"] == b["state"]
            )
            name_only_match = (
                bool(a["name"]) and a["name"] == b["name"]
                and not name_location_match
                and (a["city"] == b["city"] or a["state"] == b["state"])
            )

            if phone_match or website_match or name_location_match:
                matches.append(b["row"])
                if phone_match:
                    reasons.add("same phone number")
                if website_match:
                    reasons.add("same website")
                if name_location_match:
                    reasons.add("same name + city/state")
            elif name_only_match:
                matches.append(b["row"])
                reasons.add("same name, only partial location match - please review")

        if matches:
            group_rows = [a["row"]] + matches
            for r in group_rows:
                already_grouped.add(r["id"])
            groups.append((" / ".join(sorted(reasons)), group_rows))

    return groups


def main():
    groups = find_all_duplicate_groups()

    if not groups:
        print("No likely duplicates found in your leads table. Nothing to review.")
        return

    print(f"Found {len(groups)} group(s) of leads that look like the same company.\n")
    print("Nothing has been changed - this is a report only. Open the app,")
    print("check each group below, and delete whichever row you don't want")
    print("to keep (usually the older or less complete one).\n")
    print("=" * 70)

    for n, (reason, rows) in enumerate(groups, start=1):
        print(f"\nGroup {n} - {reason}")
        for r in rows:
            location = ", ".join(p for p in [r["city"], r["state"]] if p) or "no location on file"
            print(
                f"  Lead #{r['id']:<5} {r['company_name']:<40} "
                f"{location:<25} {r['industry'] or ''}  (saved {r['created_at']})"
            )

    print("\n" + "=" * 70)
    print(f"\nTotal groups needing a look: {len(groups)}")


if __name__ == "__main__":
    main()
