"""
intent_engine.py
------------------
Phase 4: finds "intent leads" - people publicly asking for a service -
using ONLY things that are free with no card, no key, and no vendor
pricing page that can change on us:

  - Reddit's own public .rss feeds (still free, no login, no key -
    this is not the Reddit Data API that closed in November 2025;
    it's the same feed a browser or RSS reader uses)
  - A small local keyword filter, so we only spend AI calls on posts
    that already look like a real match
  - Gemini Flash (free, no card, 1,500 requests/day) to make the final
    call, falling back to Groq/Llama 3.3 (also free, no card) if
    Gemini errors out or its daily quota is used up

We went through three search-API vendors (Google Custom Search, Brave)
before landing here, and all three either closed or dropped their free
tier. RSS is the one piece of this that isn't a company's pricing page
- Reddit built it as a public feature, the way a browser reads it, and
it doesn't need an account or a key at all. That's why this version
skips a "search API" step entirely.

HOW TO GET YOUR (FREE) AI KEYS
---------------------------------
1. Gemini API key: https://aistudio.google.com/apikey
   Sign in with any Google account, click "Create API key", copy it.

2. Groq API key (optional fallback): https://console.groq.com/keys
   Sign up, click "Create API Key", copy it.

Set them as environment variables before starting the app:

    # Mac/Linux
    export GEMINI_API_KEY="your-key-here"
    export GROQ_API_KEY="your-key-here"
    python app.py

    # Windows (PowerShell)
    $env:GEMINI_API_KEY="your-key-here"
    $env:GROQ_API_KEY="your-key-here"
    python app.py

IMPORTANT - if you ever paste a real key into a chat, a doc, or
anywhere else it could leak, treat it as burned: go regenerate it at
the link above rather than keep using it.

A NOTE ON BEING A GOOD CITIZEN OF REDDIT'S RSS FEEDS
--------------------------------------------------------
Reddit rate-limits anonymous RSS traffic. This code sends a real,
descriptive User-Agent (required by Reddit's own API rules) and
pauses briefly between subreddit requests so it behaves like a slow,
polite reader rather than something hammering their servers. Please
don't remove the delay or fire this in a tight loop - that's exactly
the kind of "hide automated activity" behavior this app avoids, and
it's also how you get your IP rate-limited or blocked.
"""

import time
import xml.etree.ElementTree as ET

import json
import requests

import config

ATOM_NS = "{http://www.w3.org/2005/Atom}"
REDDIT_USER_AGENT = "LeadForge/1.0 (by a small business owner; contact via app)"
SECONDS_BETWEEN_SUBREDDIT_REQUESTS = 3

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Phrases that usually signal someone is asking for a service, rather
# than just mentioning one in passing. Checked against the post title
# and summary before anything gets sent to the AI, to keep AI usage
# (and Reddit requests) low.
INTENT_PHRASES = [
    "need a {service}", "need an {service}", "looking for a {service}",
    "looking for an {service}", "recommend a {service}", "recommend an {service}",
    "any good {service}", "anyone know a {service}", "anyone know a good {service}",
    "hire a {service}", "hire an {service}", "find a {service}", "find an {service}",
    "in need of a {service}",
]


class IntentEngineError(Exception):
    """Raised when the engine can't complete a step."""


# ============================================================================
# STEP 1: FETCH  (Reddit's public .rss feeds - free, no key, no login)
# ============================================================================

def fetch_subreddit_posts(subreddit, sort="new", limit=25):
    """
    Pulls the latest posts from one subreddit's public RSS feed and
    returns a plain list of {title, url, summary} dicts. No API key,
    no OAuth - this is the same feed a browser gets from
    reddit.com/r/{subreddit}/new/.rss
    """
    url = f"https://www.reddit.com/r/{subreddit}/{sort}/.rss"
    headers = {"User-Agent": REDDIT_USER_AGENT}

    try:
        response = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as e:
        raise IntentEngineError(f"Couldn't reach r/{subreddit}: {e}")

    if response.status_code == 429:
        raise IntentEngineError(
            f"r/{subreddit} rate-limited this request. Wait a bit and "
            "try again with fewer subreddits at once."
        )
    if response.status_code != 200:
        raise IntentEngineError(
            f"r/{subreddit} returned {response.status_code} - it may not "
            "exist, or Reddit is temporarily blocking anonymous feeds."
        )

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError:
        return []

    posts = []
    for entry in root.findall(f"{ATOM_NS}entry")[:limit]:
        title_el = entry.find(f"{ATOM_NS}title")
        link_el = entry.find(f"{ATOM_NS}link")
        summary_el = entry.find(f"{ATOM_NS}content") or entry.find(f"{ATOM_NS}summary")

        posts.append({
            "title": title_el.text if title_el is not None else "",
            "url": link_el.get("href") if link_el is not None else "",
            "summary": (summary_el.text or "") if summary_el is not None else "",
        })
    return posts


# ============================================================================
# STEP 2: LOCAL KEYWORD FILTER  (free, instant, no API calls at all)
# ============================================================================

def looks_like_intent(post, service):
    """
    Cheap local check: does this post's title or summary contain a
    phrase that usually signals someone wants to hire for `service`?
    This runs before any AI call, so a subreddit full of unrelated
    chatter doesn't cost you anything.
    """
    text = f"{post['title']} {post['summary']}".lower()
    service_lower = service.lower()
    return any(
        phrase.format(service=service_lower) in text
        for phrase in INTENT_PHRASES
    )


# ============================================================================
# STEP 3: SCORE  (Gemini Flash, falling back to Groq/Llama - both free)
# ============================================================================

CLASSIFY_PROMPT = """You are helping a small business owner find people who \
are publicly asking for the service they sell: "{service}".

Here is one Reddit post:
Title: {title}
Text: {summary}
URL: {url}

Decide if this is genuinely someone asking for that service (a real \
"intent lead"), or something else (an unrelated post, someone offering \
the service rather than needing it, idle chat, etc).

Respond with ONLY a JSON object, no other text, in exactly this shape:
{{
  "is_lead": true or false,
  "score": a number from 0 to 100 (how strong the buying intent is; \
use 0 if is_lead is false),
  "classification": "High Intent" or "Medium Intent" or "Low Intent" \
or "Not a lead",
  "urgency": "high", "medium", "low", or null,
  "reason": "one short sentence explaining the decision"
}}
"""


def _call_gemini(prompt):
    if not config.GEMINI_API_KEY:
        raise IntentEngineError("No Gemini API key set.")
    url = f"{GEMINI_URL}?key={config.GEMINI_API_KEY}"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    response = requests.post(url, json=body, timeout=20)
    response.raise_for_status()
    data = response.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_groq(prompt):
    if not config.GROQ_API_KEY:
        raise IntentEngineError("No Groq API key set.")
    headers = {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "openai/gpt-oss-120b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    response = requests.post(GROQ_URL, headers=headers, json=body, timeout=20)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def _parse_json_response(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text)


def classify_post(post, service):
    """
    Scores one post with AI. Tries Gemini Flash first; if that fails
    for ANY reason (no key, quota hit, network error), falls back to
    Groq automatically. Returns a dict, or None if both failed.
    """
    prompt = CLASSIFY_PROMPT.format(
        service=service, title=post["title"], summary=post["summary"][:600],
        url=post["url"],
    )

    for caller, provider_name in ((_call_groq, "groq"),):
        try:
            raw = caller(prompt)
            parsed = _parse_json_response(raw)
            parsed["_provider"] = provider_name
            return parsed
        except Exception:
            continue

    return None


# ============================================================================
# STEP 4: PUT IT TOGETHER
# ============================================================================

def discover_intent_leads(service, subreddits, max_ai_calls=20):
    """
    Runs one full Phase 4 cycle across a list of subreddits:
      1. Pulls each subreddit's RSS feed (free, no key)
      2. Filters locally for posts that look like real intent (free)
      3. Sends only those to AI for a final score
      4. Returns the ones the AI confirms are real intent leads

    `subreddits` is a list of subreddit names, no "r/" prefix.
    Nothing is saved to the database here - this returns plain dicts
    for a human to review before anything is added as a lead.

    If one subreddit is rate-limited, missing, or otherwise fails, it's
    skipped rather than aborting the whole search - a block on one
    subreddit shouldn't waste a search across four working ones. Which
    subreddits were skipped, and why, is returned alongside the results
    so the person searching still knows what happened.
    """
    if not subreddits:
        raise IntentEngineError("Add at least one subreddit to search.")

    ai_calls_made = 0
    candidates = []
    skipped = []
    tried_any = False

    for i, subreddit in enumerate(subreddits):
        subreddit = subreddit.strip().lstrip("r/").strip()
        if not subreddit:
            continue

        if tried_any:
            time.sleep(SECONDS_BETWEEN_SUBREDDIT_REQUESTS)
        tried_any = True

        try:
            posts = fetch_subreddit_posts(subreddit)
        except IntentEngineError as e:
            skipped.append(f"r/{subreddit}: {e}")
            continue

        matches = [p for p in posts if looks_like_intent(p, service)]

        for post in matches:
            if ai_calls_made >= max_ai_calls:
                break
            verdict = classify_post(post, service)
            ai_calls_made += 1
            if verdict is None or not verdict.get("is_lead"):
                continue

            candidates.append({
                "score": verdict.get("score"),
                "requested_service": service,
                "post_text": (post["summary"][:500] or post["title"]),
                "post_url": post["url"],
                "intent_classification": verdict.get("classification"),
                "potential_urgency": verdict.get("urgency"),
                "lead_source": f"Reddit RSS + AI ({verdict.get('_provider', 'ai')})",
                "notes": verdict.get("reason", ""),
            })

    if skipped and not candidates and len(skipped) == len(
        [s for s in subreddits if s.strip()]
    ):
        # Every single subreddit failed - that's worth raising as a real
        # error rather than a quiet empty result.
        raise IntentEngineError(
            "None of the subreddits could be reached: " + "; ".join(skipped)
        )

    return candidates, skipped

