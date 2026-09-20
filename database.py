"""
database.py
------------
In charge of the database.

We're using SQLite: a database that lives in a single file on your
computer (leadforge.db) - nothing to install, nothing to pay for.

Phase 1 created:
  - app_info: basic info about the app itself.

Phase 2 added:
  - users: the login system.

Phase 3 (this update) adds the real lead database:
  - leads: one row per lead, whether it's a normal COMPANY lead or an
    INTENT lead (someone publicly asking for a service - built out
    properly in Phase 4, but the column is here now so we never have
    to reshape this table later).
  - lead_activity: a timeline per lead - every note and every status
    change, so nothing about a lead's history ever gets lost.
  - saved_searches: reserved for Phase 4's intent-alert feature.

Phase 10 adds:
  - opportunities: one row per detected buying signal (hiring,
    expansion, or new project) found on a lead's own website, tied
    back to that lead so it always shows up on their profile too.

Passwords are NEVER stored as plain text - see users table below.
"""

import sqlite3
from werkzeug.security import generate_password_hash

from config import (
    DATABASE_PATH, APP_NAME, CREATOR_NAME,
    OWNER_USERNAME, OWNER_PASSWORD,
)

# The full set of pipeline stages a lead can sit in. Kept in one place
# so every part of the app (filters, the status dropdown, the badge
# colours in CSS) agrees on the same list and order.
LEAD_STATUSES = ["new", "contacted", "qualified", "opportunity", "won", "lost"]

STATUS_LABELS = {
    "new": "New",
    "contacted": "Contacted",
    "qualified": "Qualified",
    "opportunity": "Opportunity",
    "won": "Won",
    "lost": "Lost",
}

# Every column a lead can have. Company leads use the top group,
# intent leads use both groups. Keeping this one flat table (rather
# than two separate tables) means a single "All Leads" query, a
# single search box, and a single detail page can serve both types.
LEAD_FIELDS = [
    "lead_type", "status", "score",
    "company_name", "contact_name", "email", "phone", "website",
    "city", "state", "country", "industry", "services_needed",
    "requested_service", "post_text", "post_url", "post_date",
    "profile_url", "potential_project_type", "potential_urgency",
    "intent_classification", "lead_source", "notes",
]

# The three kinds of public buying signal Phase 10 (Opportunities) looks
# for. Kept in one place, same pattern as LEAD_STATUSES above, so the
# scan engine and the Opportunities page always agree on the same list.
SIGNAL_TYPES = ["hiring", "expansion", "new_project"]

SIGNAL_LABELS = {
    "hiring": "Hiring",
    "expansion": "Expansion",
    "new_project": "New project",
}


def get_connection():
    """
    Opens a connection to the SQLite database file.
    Every function that needs to talk to the database calls this first.
    """
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """
    Creates every table Lead Forge needs if it doesn't already exist,
    and seeds the very first (owner) account the first time the app
    ever runs. Safe to call every time the app starts - it will NOT
    erase existing data or reset an existing password.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_name TEXT NOT NULL,
            creator_name TEXT NOT NULL,
            version TEXT NOT NULL,
            installed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("SELECT COUNT(*) FROM app_info")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO app_info (app_name, creator_name, version) VALUES (?, ?, ?)",
            (APP_NAME, CREATOR_NAME, "0.3.0 - Phase 3 Lead Database")
        )
    else:
        cursor.execute(
            "UPDATE app_info SET version = ? WHERE id = (SELECT id FROM app_info ORDER BY id DESC LIMIT 1)",
            ("0.3.0 - Phase 3 Lead Database",)
        )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'owner',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_login_at TEXT
        )
    """)

    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            """INSERT INTO users (username, password_hash, display_name, role)
               VALUES (?, ?, ?, 'owner')""",
            (OWNER_USERNAME, generate_password_hash(OWNER_PASSWORD), CREATOR_NAME)
        )

    # --- Phase 3: the leads table ---------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            lead_type TEXT NOT NULL DEFAULT 'company',   -- 'company' or 'intent'
            status TEXT NOT NULL DEFAULT 'new',
            score INTEGER,                                -- 0-100, either type

            -- shared identity / contact fields
            company_name TEXT,
            contact_name TEXT,
            email TEXT,
            phone TEXT,
            website TEXT,
            city TEXT,
            state TEXT,
            country TEXT,
            industry TEXT,
            services_needed TEXT,

            -- intent-lead-specific fields (Phase 4 fills these in
            -- automatically; usable by hand from Phase 3 onward)
            requested_service TEXT,
            post_text TEXT,
            post_url TEXT,
            post_date TEXT,
            profile_url TEXT,
            potential_project_type TEXT,
            potential_urgency TEXT,             -- low / medium / high
            intent_classification TEXT,         -- High / Medium / Low / Not a lead

            lead_source TEXT,                   -- Manual, Reddit, Referral, etc.
            notes TEXT,

            date_discovered TEXT DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_type ON leads(lead_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score)")

    # --- Phase 3: activity timeline per lead -----------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lead_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
            activity_type TEXT NOT NULL,     -- 'created', 'note', 'status_change'
            content TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_activity_lead ON lead_activity(lead_id)")

    # --- Reserved for Phase 4 (Intent Leads saved-search alerts) --------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS saved_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            service TEXT,
            location TEXT,
            intent_level TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- Phase 10: opportunities - one row per detected buying signal ---
    # (hiring / expansion / new project) found on a lead's own website.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,

            signal_type TEXT NOT NULL,       -- 'hiring', 'expansion', 'new_project'
            headline TEXT NOT NULL,          -- one-line statement of what was found
            why_it_matters TEXT,
            related_service TEXT,
            evidence TEXT,                   -- the actual page text the signal rests on

            source_label TEXT NOT NULL,      -- e.g. 'Company website'
            source_url TEXT,

            dismissed INTEGER NOT NULL DEFAULT 0,
            detected_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_lead ON opportunities(lead_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_dismissed ON opportunities(dismissed)")

    conn.commit()
    conn.close()


def get_app_info():
    """Reads the single app_info row back out of the database."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM app_info ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return row


def get_user_by_username(username):
    """
    Looks up one account by username. Returns None if no such account
    exists. The login page uses this, then checks the password hash.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    return row


def touch_last_login(user_id):
    """Records the time of a successful login, for the account list later."""
    conn = get_connection()
    conn.execute(
        "UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()


# ============================================================================
# LEADS - Phase 3
# ============================================================================

def _clean(data):
    """Keeps only real lead columns and turns '' into NULL, so empty form
    fields don't get stored as literal empty strings."""
    out = {}
    for key in LEAD_FIELDS:
        if key in data:
            value = data[key]
            if isinstance(value, str):
                value = value.strip() or None
            out[key] = value
    return out


def create_lead(data):
    """Inserts one new lead (company or intent) and returns its new id."""
    clean = _clean(data)
    clean.setdefault("lead_type", "company")
    clean.setdefault("status", "new")

    columns = list(clean.keys())
    placeholders = ", ".join("?" for _ in columns)
    values = [clean[c] for c in columns]

    conn = get_connection()
    cursor = conn.execute(
        f"INSERT INTO leads ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    lead_id = cursor.lastrowid
    conn.execute(
        "INSERT INTO lead_activity (lead_id, activity_type, content) VALUES (?, 'created', ?)",
        (lead_id, f"Lead created ({clean.get('lead_type', 'company')})"),
    )
    conn.commit()
    conn.close()
    return lead_id


def update_lead(lead_id, data):
    """Updates an existing lead's editable fields."""
    clean = _clean(data)
    if not clean:
        return
    columns = list(clean.keys())
    assignments = ", ".join(f"{c} = ?" for c in columns)
    values = [clean[c] for c in columns] + [lead_id]

    conn = get_connection()
    conn.execute(
        f"UPDATE leads SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()


def update_lead_status(lead_id, new_status):
    """Moves a lead to a new pipeline stage and logs it on the timeline."""
    if new_status not in LEAD_STATUSES:
        return
    conn = get_connection()
    conn.execute(
        "UPDATE leads SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_status, lead_id),
    )
    conn.execute(
        "INSERT INTO lead_activity (lead_id, activity_type, content) VALUES (?, 'status_change', ?)",
        (lead_id, f"Status changed to {STATUS_LABELS.get(new_status, new_status)}"),
    )
    conn.commit()
    conn.close()


def delete_lead(lead_id):
    """Removes a lead and its activity history permanently."""
    conn = get_connection()
    conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()


def get_lead(lead_id):
    """Fetches one lead by id, or None if it doesn't exist."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    return row


def get_leads(lead_type=None, status=None, search=None, order_by="created_at DESC"):
    """
    Returns the leads table, optionally filtered by type, status, and a
    free-text search across the fields users actually search by.
    """
    query = "SELECT * FROM leads WHERE 1=1"
    params = []

    if lead_type and lead_type != "all":
        query += " AND lead_type = ?"
        params.append(lead_type)

    if status and status != "all":
        query += " AND status = ?"
        params.append(status)

    if search:
        query += """ AND (
            company_name LIKE ? OR contact_name LIKE ? OR email LIKE ?
            OR city LIKE ? OR state LIKE ? OR industry LIKE ?
            OR requested_service LIKE ? OR post_text LIKE ?
        )"""
        term = f"%{search}%"
        params.extend([term] * 8)

    query += f" ORDER BY {order_by}"

    conn = get_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def get_lead_stats():
    """
    One dashboard-ready summary of the leads table. Every number here is
    real - computed straight from whatever leads actually exist.
    """
    conn = get_connection()

    def scalar(query, params=()):
        result = conn.execute(query, params).fetchone()[0]
        return result or 0

    stats = {
        "total_leads": scalar("SELECT COUNT(*) FROM leads"),
        "new_leads": scalar("SELECT COUNT(*) FROM leads WHERE status = 'new'"),
        "hot_leads": scalar("SELECT COUNT(*) FROM leads WHERE score >= 90"),
        "qualified_leads": scalar("SELECT COUNT(*) FROM leads WHERE status = 'qualified'"),
        "contacted_leads": scalar("SELECT COUNT(*) FROM leads WHERE status = 'contacted'"),
        "opportunities": scalar("SELECT COUNT(*) FROM leads WHERE status = 'opportunity'"),
        "emails_found": scalar("SELECT COUNT(*) FROM leads WHERE email IS NOT NULL AND email != ''"),
        "phones_found": scalar("SELECT COUNT(*) FROM leads WHERE phone IS NOT NULL AND phone != ''"),
        "companies_researched": scalar(
            "SELECT COUNT(DISTINCT company_name) FROM leads WHERE lead_type = 'company' AND company_name IS NOT NULL"
        ),
    }
    avg_row = conn.execute("SELECT AVG(score) FROM leads WHERE score IS NOT NULL").fetchone()[0]
    stats["average_score"] = round(avg_row) if avg_row else 0

    conn.close()
    return stats


def get_hot_leads(limit=5):
    """The highest-scoring leads, for the Dashboard's 'Hot leads' panel."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM leads WHERE score IS NOT NULL ORDER BY score DESC, created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


# ============================================================================
# LEAD ACTIVITY - Phase 3
# ============================================================================

def add_activity(lead_id, activity_type, content):
    """Adds one entry to a lead's timeline (a note or a status change)."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO lead_activity (lead_id, activity_type, content) VALUES (?, ?, ?)",
        (lead_id, activity_type, content),
    )
    conn.commit()
    conn.close()


def get_activity(lead_id):
    """A lead's full timeline, most recent first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM lead_activity WHERE lead_id = ? ORDER BY created_at DESC",
        (lead_id,),
    ).fetchall()
    conn.close()
    return rows


# ============================================================================
# OPPORTUNITIES - Phase 10
# ============================================================================

def opportunity_already_open(lead_id, signal_type):
    """
    True if this lead already has a live (non-dismissed) opportunity of
    this same signal type, so re-running a scan doesn't create a second
    row every time the same hiring page gets read again.
    """
    conn = get_connection()
    row = conn.execute(
        """SELECT 1 FROM opportunities
           WHERE lead_id = ? AND signal_type = ? AND dismissed = 0
           LIMIT 1""",
        (lead_id, signal_type),
    ).fetchone()
    conn.close()
    return row is not None


def create_opportunity(data):
    """Saves one detected buying signal, tied to the lead it came from."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO opportunities
               (lead_id, signal_type, headline, why_it_matters,
                related_service, evidence, source_label, source_url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data["lead_id"], data["signal_type"], data["headline"],
            data.get("why_it_matters"), data.get("related_service"),
            data.get("evidence"), data.get("source_label"),
            data.get("source_url"),
        ),
    )
    conn.commit()
    conn.close()


def get_opportunities(include_dismissed=False):
    """
    Every detected opportunity, newest first, joined with its lead's
    basic identity so the Opportunities page can show a company name
    without a second query per row.
    """
    query = """
        SELECT opportunities.*, leads.company_name, leads.contact_name,
               leads.lead_type
        FROM opportunities
        JOIN leads ON leads.id = opportunities.lead_id
    """
    if not include_dismissed:
        query += " WHERE opportunities.dismissed = 0"
    query += " ORDER BY opportunities.detected_at DESC"

    conn = get_connection()
    rows = conn.execute(query).fetchall()
    conn.close()
    return rows


def dismiss_opportunity(opportunity_id):
    """Hides one opportunity from the main list without deleting its
    history - a fresh scan can still surface a new one later if the
    same signal genuinely shows up again."""
    conn = get_connection()
    conn.execute(
        "UPDATE opportunities SET dismissed = 1 WHERE id = ?",
        (opportunity_id,),
    )
    conn.commit()
    conn.close()
