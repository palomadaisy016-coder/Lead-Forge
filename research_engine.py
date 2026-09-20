"""
research_engine.py
--------------------
Phase 7: AI Research - Quick + Deep.

For a company lead you already have saved, this fetches the text of
that company's OWN public website - no login, no bypassing anything,
literally the same request a browser makes - and asks the same free
Gemini -> Groq setup from Phase 4 to make sense of it.

  Quick Research  - a 2-4 sentence summary of what the company does.
  Deep Research   - services, markets, likely project types, and how
                    well they might fit as a customer for Freedom
                    Masons' own services (architecture / engineering /
                    estimation / construction support), with the
                    reasoning spelled out - never just a verdict.

Reuses the exact Gemini/Groq calling code from intent_engine.py rather
than a second copy of it, so there's one place to fix if a provider's
API shape ever changes.
"""

import re

import requests

from intent_engine import _call_groq, _parse_json_response

RESEARCH_USER_AGENT = (
    "LeadForge/1.0 (personal lead-gen tool; reading public company sites)"
)


class ResearchEngineError(Exception):
    """Raised when research can't be completed, with a message that's
    safe to show directly to the user."""


def fetch_website_text(url, max_chars=6000):
    """Fetches one public page and returns its visible text, with all
    HTML/scripts/styles stripped out. No login, no JS rendering, no
    working around a block - if a site refuses a plain request, we say
    so and stop, same as every other engine in this app."""
    if not url:
        raise ResearchEngineError(
            "This lead has no website on file, so there's nothing to research."
        )

    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    try:
        resp = requests.get(
            url, headers={"User-Agent": RESEARCH_USER_AGENT}, timeout=15
        )
    except requests.RequestException as e:
        raise ResearchEngineError(f"Couldn't reach {url}: {e}")

    if resp.status_code != 200:
        raise ResearchEngineError(
            f"{url} returned status {resp.status_code} - the site may be "
            "down, blocking automated requests, or the URL on file may "
            "be wrong."
        )

    html = resp.text
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        raise ResearchEngineError(
            f"{url} loaded, but no readable text was found on the page."
        )

    return text[:max_chars]


QUICK_PROMPT = """You are helping a small business owner quickly understand a \
company from their own website text below.

Website text (from {url}):
{text}

In 2-4 plain sentences, summarize what this company actually does - their \
trade/industry, and anything specific about their focus. Do not invent \
anything not supported by the text. If the text doesn't give enough to say \
anything useful, say so plainly instead of guessing.

Respond with ONLY a JSON object, no other text, in exactly this shape:
{{
  "summary": "2-4 sentence summary"
}}
"""

DEEP_PROMPT = """You work for Freedom Masons, a construction / architecture / \
engineering / estimation firm. You are researching another company as a \
potential CUSTOMER for Freedom Masons' services, using only their own \
website text below - never invent anything the text doesn't support.

Company website text (from {url}):
{text}

Freedom Masons offers: architectural drawings, structural engineering, \
construction estimation, permit sets, and general construction/project \
support services.

Respond with ONLY a JSON object, no other text, in exactly this shape:
{{
  "services": "what this company itself does/sells, in your own words",
  "markets": "residential, commercial, industrial, etc - whatever the text supports",
  "project_types": "typical project types this company appears to run, if the text supports it",
  "freedom_masons_fit": "High" or "Medium" or "Low" or "Unclear",
  "reasoning": "2-4 sentences explaining the fit rating, tied to specific things in the text"
}}
"""


def _run_ai(prompt):
    """Calls Groq only - no Gemini key needed."""
    for caller, provider_name in ((_call_groq, "groq"),):
        try:
            raw = caller(prompt)
            parsed = _parse_json_response(raw)
            parsed["_provider"] = provider_name
            return parsed
        except Exception:
            continue
    raise ResearchEngineError(
        "Groq failed to respond. Check GROQ_API_KEY is set as an "
        "environment variable, or try again in a moment."
    )


def quick_research(url):
    """Returns {"summary": "...", "_provider": "gemini" or "groq"}."""
    text = fetch_website_text(url)
    return _run_ai(QUICK_PROMPT.format(url=url, text=text))


def deep_research(url):
    """Returns {"services", "markets", "project_types",
    "freedom_masons_fit", "reasoning", "_provider"}."""
    text = fetch_website_text(url)
    return _run_ai(DEEP_PROMPT.format(url=url, text=text))
