"""
app.py
------
The main file that starts Lead Forge. Run it with:

    python app.py

WHAT THIS FILE DOES
--------------------
Phase 1: starts the server, sets up the database, shows the dashboard
and a "not built yet" page for every other section.

Phase 2: adds accounts - nobody sees the app without signing in first.

Phase 3 adds the real lead database:
  - "All Leads" now reads and writes real rows instead of showing a
    placeholder. You can add a lead by hand, filter by type/status,
    search, open a lead's full profile, add notes, move it through
    the pipeline, edit it, or delete it.
  - The Dashboard's numbers are now real counts from the database,
    and "Hot leads" shows your actual highest-scoring leads.
  - Every lead has a lead_type of "company" or "intent" so Phase 4
    (Intent Leads) can build straight on top of this table.

Phase 4 adds Intent Leads (Reddit RSS + AI scoring).

Phase 5 (this update) adds the real "Lead Finder" page:
  - Search real, existing companies by industry, location and the
    services you sell, using the free OpenStreetMap Overpass API -
    no account, no key, no card.
  - Results show a data-quality label per field (VERIFIED / FOUND /
    UNVERIFIED / NOT FOUND) so you always know what's real versus
    missing - nothing is ever invented.
  - Nothing is saved until you tick which results to keep and click
    "Save selected", exactly like Intent Leads.

Phase 9 (this update) adds the real "Pipeline" page:
  - A drag-and-drop board with one column per pipeline stage. Drag a
    card to a new column on desktop, or use its "Move to" menu on
    touch/mobile - both call the same /leads/<id>/move endpoint, which
    just calls the existing update_lead_status().
  - No new data: it's the same leads table, grouped by status.

Phase 10 (this update) adds the real "Opportunities" page:
  - Rereads every lead's own website (the exact same fetch Phase 7's AI
    Research already uses) looking for an explicitly stated hiring,
    expansion, or new-project signal.
  - Nothing is invented: if the AI can't point to real text on the page
    supporting a signal, that lead is simply skipped, not guessed at.
  - Each finding is saved once (re-scanning won't duplicate an already-
    open one) and also logged onto that lead's own activity timeline.
"""

import csv
import functools
import io
import os

from flask import Flask, render_template, request, redirect, url_for, session

from werkzeug.security import check_password_hash

import config
import locations
from dedupe import find_match
from database import (
    init_db, get_app_info, get_user_by_username, touch_last_login,
    LEAD_STATUSES, STATUS_LABELS, SIGNAL_LABELS,
    create_lead, update_lead, update_lead_status, delete_lead,
    get_lead, get_leads, get_lead_stats, get_hot_leads,
    add_activity, get_activity,
    create_opportunity, get_opportunities, dismiss_opportunity,
    opportunity_already_open,
)
from intent_engine import discover_intent_leads, IntentEngineError
from overpass_engine import discover_companies, OverpassEngineError
from research_engine import quick_research, deep_research, ResearchEngineError
from opportunity_engine import scan_leads_for_opportunities, OpportunityEngineError

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

init_db()

SIDEBAR_ITEMS = [
    {"label": "Dashboard", "slug": "dashboard"},
    {"label": "Lead Finder", "slug": "lead-finder"},
    {"label": "AI Research", "slug": "ai-research"},
    {"label": "Hot Leads", "slug": "hot-leads"},
    {"label": "Intent Leads", "slug": "intent-leads"},
    {"label": "All Leads", "slug": "all-leads"},
    {"label": "Pipeline", "slug": "pipeline"},
    {"label": "Lead Map", "slug": "lead-map"},
    {"label": "Opportunities", "slug": "opportunities"},
    {"label": "Exports", "slug": "exports"},
    {"label": "AI Assistant", "slug": "ai-assistant"},
    {"label": "Analytics", "slug": "analytics"},
    {"label": "Sources", "slug": "sources"},
    {"label": "Settings", "slug": "settings"},
]

# Sections that now have a real page, rather than the "coming soon" catch-all.
BUILT_SLUGS = {
    "dashboard", "all-leads", "intent-leads", "lead-finder",
    "pipeline", "opportunities",
}

PAGE_NOTES = {
    "ai-research": "Quick and deep research on any company, from public "
                   "sources only. Built later.",
    "hot-leads": "A dedicated, filterable view of your highest-scoring "
                 "leads. For now, see the Hot Leads panel on the Dashboard, "
                 "or filter All Leads by score.",
    "lead-map": "See where your leads cluster by state and city.",
    "exports": "Export any list to CSV, Excel or JSON.",
    "ai-assistant": "Ask questions about your leads and get help shaping "
                    "search criteria.",
    "analytics": "Discovery, source, industry and score trends over any "
                 "date range.",
    "sources": "Turn data sources on or off and see what each one found.",
    "settings": "Scoring rules, AI keys, exports and user management. "
                "Appearance is already live: open it from the sliders icon "
                "in the top bar.",
}


def initials(name):
    """Turns 'Muhammad Haseeb Nadeem' into 'MN' for the avatar circle."""
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "U"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


@app.template_global()
def asset_version(static_path):
    """
    Returns a CSS/JS file's own last-modified time so base.html can tack
    it onto the file's URL as ?v=169... . Every time style.css or app.js
    is actually edited, this number changes, so the browser sees a brand
    new URL and is forced to download the new file instead of quietly
    reusing whatever it cached last time - no more hard-refreshing by
    hand after every update.
    """
    full_path = os.path.join(app.static_folder, static_path)
    try:
        return int(os.path.getmtime(full_path))
    except OSError:
        return 0


@app.context_processor
def inject_current_user():
    """Makes the signed-in user's name/initials available in every template
    automatically, without every route having to pass it in by hand."""
    name = session.get("display_name")
    return {
        "current_user_name": name,
        "current_user_initials": initials(name) if name else None,
    }


def login_required(view):
    """
    Wrap any page route with @login_required and it will bounce a
    signed-out visitor to the login screen instead of showing the page.
    After they sign in, they land back on the page they originally asked
    for (see the "next" handling in the login route below).
    """
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def render_page(template_name, active_slug, **kwargs):
    return render_template(
        template_name,
        sidebar_items=SIDEBAR_ITEMS,
        active_slug=active_slug,
        app_name=config.APP_NAME,
        app_tagline=config.APP_TAGLINE,
        creator_name=config.CREATOR_NAME,
        **kwargs
    )


# ============================================================================
# AUTH
# ============================================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_user_by_username(username)

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["display_name"] = user["display_name"]
            touch_last_login(user["id"])
            return redirect(request.args.get("next") or url_for("dashboard"))

        error = "That username or password isn't right. Try again."

    return render_template(
        "login.html",
        app_name=config.APP_NAME,
        app_tagline=config.APP_TAGLINE,
        creator_name=config.CREATOR_NAME,
        error=error,
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ============================================================================
# DASHBOARD
# ============================================================================

@app.route("/")
@app.route("/dashboard")
@login_required
def dashboard():
    app_info = get_app_info()
    stats = get_lead_stats()
    hot_leads = get_hot_leads(limit=5)
    return render_page(
        "dashboard.html", "dashboard",
        stats=stats, app_info=app_info, hot_leads=hot_leads,
    )


# ============================================================================
# LEADS - Phase 3
# ============================================================================

@app.route("/all-leads")
@login_required
def all_leads():
    filter_type = request.args.get("type", "all")
    filter_status = request.args.get("status", "all")
    search_q = request.args.get("q", "").strip()

    leads = get_leads(
        lead_type=filter_type if filter_type != "all" else None,
        status=filter_status if filter_status != "all" else None,
        search=search_q or None,
    )

    return render_page(
        "all_leads.html", "all-leads",
        leads=leads,
        filter_type=filter_type,
        filter_status=filter_status,
        search_q=search_q,
        statuses=LEAD_STATUSES,
        status_labels=STATUS_LABELS,
    )


@app.route("/leads/new", methods=["GET", "POST"])
@login_required
def new_lead():
    if request.method == "POST":
        form = request.form.to_dict()
        score = form.get("score", "").strip()
        form["score"] = int(score) if score.isdigit() else None
        lead_id = create_lead(form)
        return redirect(url_for("lead_detail", lead_id=lead_id))

    lead_type = request.args.get("type", "company")
    return render_page(
        "lead_form.html", "all-leads",
        lead=None, lead_type=lead_type,
        statuses=LEAD_STATUSES, status_labels=STATUS_LABELS,
    )


@app.route("/leads/<int:lead_id>")
@login_required
def lead_detail(lead_id):
    lead = get_lead(lead_id)
    if lead is None:
        return redirect(url_for("all_leads"))
    activity = get_activity(lead_id)
    return render_page(
        "lead_detail.html", "all-leads",
        lead=lead, activity=activity,
        statuses=LEAD_STATUSES, status_labels=STATUS_LABELS,
        research_error=request.args.get("research_error"),
    )


# ============================================================================
# AI RESEARCH (per-lead) - Phase 7
# ============================================================================
# Reads the lead's own website (no login, no bypassing anything - the
# same request a browser makes) and asks Gemini/Groq to summarize it.
# The result is logged onto the lead's own activity timeline, so it
# lives right alongside notes and status changes rather than needing
# a new place to store it.

def _run_research(lead_id, research_fn, label):
    lead = get_lead(lead_id)
    if lead is None:
        return redirect(url_for("all_leads"))

    try:
        result = research_fn(lead["website"])
    except ResearchEngineError as e:
        return redirect(url_for("lead_detail", lead_id=lead_id, research_error=str(e)))
    except Exception:
        return redirect(url_for(
            "lead_detail", lead_id=lead_id,
            research_error=("Something went wrong running research. Check "
                             "GEMINI_API_KEY / GROQ_API_KEY and try again."),
        ))

    provider = result.get("_provider", "ai")

    if "summary" in result:
        content = f"{label} ({provider}): {result['summary']}"
    else:
        content = (
            f"{label} ({provider}):\n"
            f"Services: {result.get('services', '')}\n"
            f"Markets: {result.get('markets', '')}\n"
            f"Likely project types: {result.get('project_types', '')}\n"
            f"Fit for us: {result.get('freedom_masons_fit', '')} - "
            f"{result.get('reasoning', '')}"
        )

    add_activity(lead_id, "research", content)
    return redirect(url_for("lead_detail", lead_id=lead_id))


@app.route("/leads/<int:lead_id>/research/quick", methods=["POST"])
@login_required
def research_lead_quick(lead_id):
    return _run_research(lead_id, quick_research, "Quick research")


@app.route("/leads/<int:lead_id>/research/deep", methods=["POST"])
@login_required
def research_lead_deep(lead_id):
    return _run_research(lead_id, deep_research, "Deep research")


@app.route("/leads/<int:lead_id>/edit", methods=["GET", "POST"])
@login_required
def edit_lead(lead_id):
    lead = get_lead(lead_id)
    if lead is None:
        return redirect(url_for("all_leads"))

    if request.method == "POST":
        form = request.form.to_dict()
        score = form.get("score", "").strip()
        form["score"] = int(score) if score.isdigit() else None
        update_lead(lead_id, form)
        add_activity(lead_id, "note", "Lead details updated")
        return redirect(url_for("lead_detail", lead_id=lead_id))

    return render_page(
        "lead_form.html", "all-leads",
        lead=lead, lead_type=lead["lead_type"],
        statuses=LEAD_STATUSES, status_labels=STATUS_LABELS,
    )


@app.route("/leads/<int:lead_id>/delete", methods=["POST"])
@login_required
def delete_lead_route(lead_id):
    delete_lead(lead_id)
    return redirect(url_for("all_leads"))


@app.route("/leads/<int:lead_id>/status", methods=["POST"])
@login_required
def change_lead_status(lead_id):
    new_status = request.form.get("status", "")
    update_lead_status(lead_id, new_status)
    return redirect(url_for("lead_detail", lead_id=lead_id))


@app.route("/leads/<int:lead_id>/note", methods=["POST"])
@login_required
def add_lead_note(lead_id):
    note = request.form.get("note", "").strip()
    if note:
        add_activity(lead_id, "note", note)
    return redirect(url_for("lead_detail", lead_id=lead_id))


# ============================================================================
# PIPELINE - Phase 9
# ============================================================================
# A drag-and-drop board over the same leads/status data the "All Leads"
# table and a lead's own profile page already read and write. No new
# columns, no new table - this is a different view of get_leads() and
# a thin wrapper around the existing update_lead_status().

@app.route("/pipeline")
@login_required
def pipeline():
    board = {status: [] for status in LEAD_STATUSES}
    for lead in get_leads(order_by="updated_at DESC"):
        board.setdefault(lead["status"], []).append(lead)

    return render_page(
        "pipeline.html", "pipeline",
        board=board, statuses=LEAD_STATUSES, status_labels=STATUS_LABELS,
    )


@app.route("/leads/<int:lead_id>/move", methods=["POST"])
@login_required
def move_lead(lead_id):
    """
    JSON endpoint the Pipeline board calls when a card is dropped into a
    new column (or moved from the mobile "Move to" menu). Separate from
    change_lead_status() above, which is the lead-detail page's normal
    form post and redirects back to that page - the board needs a call
    that updates the database and returns without leaving the board.
    """
    if get_lead(lead_id) is None:
        return {"ok": False, "error": "Lead not found."}, 404

    payload = request.get_json(silent=True) or {}
    new_status = payload.get("status") or request.form.get("status", "")

    if new_status not in LEAD_STATUSES:
        return {"ok": False, "error": "Not a valid status."}, 400

    update_lead_status(lead_id, new_status)
    return {"ok": True, "lead_id": lead_id, "status": new_status}


# ============================================================================
# OPPORTUNITIES - Phase 10
# ============================================================================
# Rereads every lead's own website - the exact same fetch_website_text()
# Phase 7's AI Research already uses - looking for an explicitly stated
# hiring, expansion, or new-project signal. Nothing is invented: if the
# AI can't point to real text on the page supporting a signal, that lead
# is simply skipped rather than guessed at. Scanning is a manual "Scan
# for opportunities" button, same one-click-then-review shape as the
# rest of the app's discovery pages, except results here save straight
# through - they're findings about leads you already have, not new
# leads that need a human pick before they're kept.

@app.route("/opportunities")
@login_required
def opportunities():
    return render_page(
        "opportunities.html", "opportunities",
        opportunities=get_opportunities(),
        signal_labels=SIGNAL_LABELS,
        found=request.args.get("found", type=int),
        skipped=request.args.get("skipped", type=int),
        scan_error=request.args.get("scan_error"),
    )


@app.route("/opportunities/scan", methods=["POST"])
@login_required
def scan_opportunities():
    scannable_leads = [lead for lead in get_leads() if lead["website"]]

    if not scannable_leads:
        return redirect(url_for(
            "opportunities",
            scan_error="None of your leads have a website on file yet - "
                       "add one from a lead's Edit page first.",
        ))

    try:
        candidates, skipped = scan_leads_for_opportunities(scannable_leads)
    except OpportunityEngineError as e:
        return redirect(url_for("opportunities", scan_error=str(e)))
    except Exception:
        return redirect(url_for(
            "opportunities",
            scan_error=("Something went wrong scanning your leads. Check "
                        "GROQ_API_KEY and try again."),
        ))

    saved = 0
    for candidate in candidates:
        if opportunity_already_open(candidate["lead_id"], candidate["signal_type"]):
            continue
        create_opportunity(candidate)
        add_activity(
            candidate["lead_id"], "opportunity",
            f"Opportunity detected: {candidate['headline']}",
        )
        saved += 1

    return redirect(url_for("opportunities", found=saved, skipped=len(skipped)))


@app.route("/opportunities/<int:opportunity_id>/dismiss", methods=["POST"])
@login_required
def dismiss_opportunity_route(opportunity_id):
    dismiss_opportunity(opportunity_id)
    return redirect(url_for("opportunities"))


# ============================================================================
# LEAD FINDER - Phase 5
# ============================================================================
# Searches OpenStreetMap's free Overpass API for real, existing companies
# matching an industry near a location. Nothing is written to the database
# until the user ticks which results to keep and clicks "Save selected as
# leads" - same pattern as Intent Leads below.

@app.route("/lead-finder", methods=["GET", "POST"])
@login_required
def lead_finder():
    candidates = []
    error = None
    searched = False
    industry = request.form.get("industry", "").strip()
    location = request.form.get("location", "").strip()
    services = request.form.get("services", "").strip()

    if request.method == "POST":
        searched = True
        if not industry:
            error = "Enter an industry or trade to search for."
        elif not location:
            error = "Enter a city, state, or ZIP code to search in."
        else:
            try:
                candidates = discover_companies(industry, location, services)
            except OverpassEngineError as e:
                error = str(e)
            except Exception:
                error = ("Something went wrong reaching OpenStreetMap. "
                          "Double-check the location and try again.")

    return render_page(
        "lead_finder.html", "lead-finder",
        candidates=candidates, industry=industry, location=location,
        services=services, error=error, searched=searched,
        state_cities=locations.STATE_CITIES,
    )


@app.route("/lead-finder/save", methods=["POST"])
@login_required
def save_found_leads():
    selected = request.form.getlist("save")
    saved = 0
    skipped_duplicates = 0
    flagged_for_review = 0

    for i in selected:
        lead_data = {
            "lead_type": "company",
            "status": "new",
            "company_name": request.form.get(f"company_name_{i}", ""),
            "phone": request.form.get(f"phone_{i}", ""),
            "website": request.form.get(f"website_{i}", ""),
            "email": request.form.get(f"email_{i}", ""),
            "city": request.form.get(f"city_{i}", ""),
            "state": request.form.get(f"state_{i}", ""),
            "industry": request.form.get(f"industry_{i}", ""),
            "services_needed": request.form.get(f"services_{i}", ""),
            "lead_source": request.form.get(f"source_{i}", "OpenStreetMap (Overpass API)"),
            "notes": request.form.get(f"notes_{i}", ""),
        }

        # Phase 6: check this candidate against every existing company
        # lead (by phone / website / name+location together - never
        # name alone) before it becomes a new row.
        result = find_match(lead_data)

        if result["match"] == "confident":
            # Already have this company on file - don't create a
            # second row for it.
            skipped_duplicates += 1
            continue

        if result["match"] == "uncertain":
            # Save it, but flag it in its own notes so it's obvious on
            # the lead's detail page that a human should double check
            # it against lead #<id> rather than assume either way.
            flag = (
                f"POSSIBLE DUPLICATE of lead #{result['lead_id']} "
                f"({result['reason']}). "
            )
            lead_data["notes"] = flag + (lead_data["notes"] or "")
            flagged_for_review += 1

        create_lead(lead_data)
        saved += 1

    return redirect(url_for(
        "all_leads",
        saved=saved,
        skipped_duplicates=skipped_duplicates,
        flagged_for_review=flagged_for_review,
    ))


# ============================================================================
# IMPORT LEADS - free directory sources (Procore Network, The Blue Book)
# ============================================================================
# These two sites can't be reached directly from PythonAnywhere's free
# plan (see procore_finder.py for why), so instead of searching them
# live, you run a small script on your own computer that saves a CSV
# file, then upload that CSV here. Every row goes through the same
# duplicate check as every other lead source before it's saved.

IMPORT_ALLOWED_COLUMNS = {
    "lead_type", "status", "company_name", "contact_name", "email",
    "phone", "website", "city", "state", "country", "industry",
    "services_needed", "lead_source", "notes", "profile_url",
}


@app.route("/leads/import", methods=["GET", "POST"])
@login_required
def import_leads():
    error = None
    saved = 0
    skipped_duplicates = 0
    flagged_for_review = 0
    total_rows = 0

    if request.method == "POST":
        upload = request.files.get("csv_file")
        if not upload or not upload.filename:
            error = "Choose a CSV file first."
        elif not upload.filename.lower().endswith(".csv"):
            error = "That doesn't look like a CSV file."
        else:
            try:
                text = upload.stream.read().decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(text))
                rows = list(reader)
            except Exception:
                rows = None
                error = "Couldn't read that file. Make sure it's the CSV new_leads.csv created by procore_finder.py."

            if rows is not None:
                total_rows = len(rows)
                for row in rows:
                    lead_data = {
                        k: v for k, v in row.items()
                        if k in IMPORT_ALLOWED_COLUMNS and v
                    }
                    lead_data.setdefault("lead_type", "company")
                    lead_data.setdefault("status", "new")
                    if not lead_data.get("company_name"):
                        continue

                    result = find_match(lead_data)
                    if result["match"] == "confident":
                        skipped_duplicates += 1
                        continue
                    if result["match"] == "uncertain":
                        flag = (
                            f"POSSIBLE DUPLICATE of lead #{result['lead_id']} "
                            f"({result['reason']}). "
                        )
                        lead_data["notes"] = flag + (lead_data.get("notes") or "")
                        flagged_for_review += 1

                    create_lead(lead_data)
                    saved += 1

    return render_page(
        "import_leads.html", "lead-finder",
        error=error, saved=saved, total_rows=total_rows,
        skipped_duplicates=skipped_duplicates,
        flagged_for_review=flagged_for_review,
    )


# ============================================================================
# INTENT LEADS - Phase 4
# ============================================================================
# Searches the public web (Brave, free tier) for people asking for a
# service, scores each result with AI (Gemini Flash, falling back to
# Groq/Llama - also free), and shows the results for a human to review.
# Nothing is written to the database until the user ticks which ones
# to keep and clicks "Save selected as leads".

@app.route("/intent-leads", methods=["GET", "POST"])
@login_required
def intent_leads():
    candidates = []
    skipped = []
    error = None
    searched = False
    service = request.form.get("service", "").strip()
    subreddits_raw = request.form.get("subreddits", "").strip()

    if request.method == "POST":
        searched = True
        subreddit_list = [s.strip() for s in subreddits_raw.split(",") if s.strip()]
        if not service:
            error = "Enter a service to search for."
        elif not subreddit_list:
            error = "Add at least one subreddit (e.g. Construction, HomeImprovement)."
        else:
            try:
                candidates, skipped = discover_intent_leads(service, subreddit_list)
            except IntentEngineError as e:
                error = str(e)
            except Exception:
                error = ("Something went wrong reaching Reddit or the AI "
                          "service. Double-check your API keys and try again.")

    return render_page(
        "intent_leads.html", "intent-leads",
        candidates=candidates, skipped=skipped, service=service, subreddits=subreddits_raw,
        error=error, searched=searched,
    )


@app.route("/intent-leads/save", methods=["POST"])
@login_required
def save_intent_leads():
    selected = request.form.getlist("save")
    saved = 0

    for i in selected:
        score = request.form.get(f"score_{i}", "").strip()
        lead_data = {
            "lead_type": "intent",
            "status": "new",
            "score": int(score) if score.isdigit() else None,
            "requested_service": request.form.get(f"service_{i}", ""),
            "post_text": request.form.get(f"post_text_{i}", ""),
            "post_url": request.form.get(f"post_url_{i}", ""),
            "intent_classification": request.form.get(f"classification_{i}", ""),
            "potential_urgency": request.form.get(f"urgency_{i}", ""),
            "lead_source": request.form.get(f"source_{i}", ""),
            "notes": request.form.get(f"notes_{i}", ""),
        }
        create_lead(lead_data)
        saved += 1

    return redirect(url_for("all_leads"))


# ============================================================================
# EVERYTHING ELSE - still "coming soon"
# ============================================================================

@app.route("/<slug>")
@login_required
def coming_soon(slug):
    matching_item = next((item for item in SIDEBAR_ITEMS if item["slug"] == slug), None)

    if matching_item is None:
        page_title = "Page not found"
        page_icon = "target"
        page_note = "That address doesn't exist in Lead Forge."
    else:
        page_title = matching_item["label"]
        page_icon = matching_item["slug"]
        page_note = PAGE_NOTES.get(slug, "This section arrives in a later phase.")

    return render_page(
        "coming_soon.html",
        slug,
        page_title=page_title,
        page_icon=page_icon,
        page_note=page_note,
    )


if __name__ == "__main__":
    app.run(debug=True)
