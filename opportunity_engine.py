"""
opportunity_engine.py
------------------------
Phase 10: Opportunities - public buying-signal detection.

Looks at a company already saved as a lead and checks whether its OWN
website explicitly states one of three signals that it might soon need
outside help:

  - HIRING       - actively hiring / growing the team
  - EXPANSION    - a new location, facility, or market
  - NEW_PROJECT  - a specific announced project or contract win

This reuses the exact same fetch_website_text() Phase 7's AI Research
already uses (no login, no bypassing anything - the same request a
browser makes) and the same free Groq call every other engine in this
app already uses. No new API, no new key.

THE ONE RULE THAT MATTERS MOST HERE: no unsupported claims. The AI is
told explicitly not to infer a signal from a generic "about us" page or
vague language - if it can't point to real text on the page that says
one of the three things above, this returns nothing for that lead
rather than guessing. A missed signal is fine; a made-up one isn't.
"""

import time

from database import SIGNAL_TYPES
from intent_engine import _call_groq, _parse_json_response
from research_engine import fetch_website_text, ResearchEngineError

SECONDS_BETWEEN_SITE_REQUESTS = 2


class OpportunityEngineError(Exception):
    """Raised when a scan can't be completed, with a message that's
    safe to show directly to the user."""


SIGNAL_PROMPT = """You are screening ONE company's own website text for a \
real, explicitly stated buying signal - something that suggests they might \
soon need outside architecture, engineering, estimation, or construction \
support.

Company: {company_name}
Website text (from {url}):
{text}

Look ONLY for text that actually, explicitly says one of these three things:
  - HIRING: they are actively hiring or growing their team
  - EXPANSION: they are opening a new location, expanding a facility, or \
growing into a new market
  - NEW_PROJECT: they have announced a specific new project, contract win, \
or upcoming build

Do NOT invent or infer a signal from vague language, a generic "about us" \
page, or a site that simply lists the services it offers. If nothing in the \
text explicitly supports one of the three signals above, say so plainly - a \
false "found" is worse than an honest "not found".

Respond with ONLY a JSON object, no other text, in exactly this shape:
{{
  "found": true or false,
  "signal_type": "hiring" or "expansion" or "new_project" or null,
  "headline": "one short plain sentence stating the signal, or null",
  "evidence": "the specific phrase or sentence from the text this is based on, or null",
  "why_it_matters": "1-2 sentences on why this could matter for a construction, architecture, engineering, or estimation firm reaching out now, or null",
  "related_service": "whichever of these fits best: architectural drawings, structural engineering, construction estimation, permit sets, general construction/project support - or null"
}}
"""


def _run_ai(prompt):
    """Calls Groq - the same provider every other engine in this app
    already uses for AI classification."""
    try:
        raw = _call_groq(prompt)
        return _parse_json_response(raw)
    except Exception:
        raise OpportunityEngineError(
            "Groq failed to respond. Check GROQ_API_KEY is set as an "
            "environment variable, or try again in a moment."
        )


def scan_lead_for_opportunity(lead):
    """
    Reads one lead's own website and asks AI whether it explicitly
    states a hiring, expansion, or new-project signal. Returns a dict
    ready for database.create_opportunity() if a real signal was
    found, or None if it wasn't found - never a guess either way.
    """
    website = lead["website"]
    if not website:
        raise OpportunityEngineError(
            "This lead has no website on file, so there's nothing to scan."
        )

    text = fetch_website_text(website)
    result = _run_ai(SIGNAL_PROMPT.format(
        company_name=lead["company_name"] or lead["contact_name"] or "this company",
        url=website, text=text,
    ))

    if not result.get("found") or result.get("signal_type") not in SIGNAL_TYPES:
        return None

    return {
        "lead_id": lead["id"],
        "signal_type": result["signal_type"],
        "headline": result.get("headline") or "Buying signal detected",
        "why_it_matters": result.get("why_it_matters"),
        "related_service": result.get("related_service"),
        "evidence": result.get("evidence"),
        "source_label": "Company website",
        "source_url": website,
    }


def scan_leads_for_opportunities(leads, max_ai_calls=15):
    """
    Runs scan_lead_for_opportunity() across a batch of leads (normally
    every lead with a website on file). A lead with no website, or
    whose site can't be reached, is skipped rather than aborting the
    whole batch - one blocked or slow site shouldn't waste a scan
    across everything else. A short, polite pause between site
    requests keeps this a slow reader rather than something hammering
    several companies' servers back to back, same as every other
    engine in this app.

    Returns (found, skipped) - `found` is a list of opportunity dicts
    ready to save, `skipped` is a list of short human-readable reasons.
    """
    found = []
    skipped = []
    ai_calls_made = 0
    tried_any = False

    scannable = [lead for lead in leads if lead["website"]]

    for lead in scannable:
        label = lead["company_name"] or lead["contact_name"] or f"lead #{lead['id']}"

        if ai_calls_made >= max_ai_calls:
            skipped.append(f"{label}: scan limit reached for this run")
            continue

        if tried_any:
            time.sleep(SECONDS_BETWEEN_SITE_REQUESTS)
        tried_any = True

        try:
            result = scan_lead_for_opportunity(lead)
            ai_calls_made += 1
        except (ResearchEngineError, OpportunityEngineError) as e:
            skipped.append(f"{label}: {e}")
            continue
        except Exception:
            skipped.append(f"{label}: unexpected error reading its site")
            continue

        if result is not None:
            found.append(result)

    return found, skipped
