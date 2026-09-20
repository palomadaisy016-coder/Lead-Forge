"""
config.py
---------
The settings sheet for Lead Forge. Change something once here instead
of hunting through every file.

PHASE 2 ADDITIONS
------------------
OWNER_USERNAME / OWNER_PASSWORD
    Used ONE TIME ONLY - the very first time you run the app, to create
    your owner account in the database. After that first run, the
    database has its own copy (safely scrambled - see database.py), and
    changing these two lines again will NOT change your password,
    because the app never re-seeds an account once one already exists.

    These now come from environment variables instead of being typed
    directly into this file - so this file is safe to push to a public
    GitHub repo. The real values are set on your own computer and on
    PythonAnywhere separately (see below).

    If you ever want to change your password, the clean way is: stop
    the app, delete leadforge.db, update the environment variable, run
    the app again. That rebuilds the database fresh with the new
    password. (Only safe to do before you have real lead data saved.)

SECRET_KEY
    Flask needs a random secret to keep your login session secure.
    Instead of typing one by hand, the app generates a long random one
    the first time it runs and saves it to a file called secret.key
    sitting next to this file. Every run after that reuses the same
    one. Never share that file, never commit it to git, never paste
    its contents anywhere.
"""

import os
import secrets

APP_NAME = "Lead Forge"
APP_TAGLINE = "Discover \u2192 Research \u2192 Qualify \u2192 Organize \u2192 Convert"
CREATOR_NAME = "Muhammad Haseeb Nadeem"

DATABASE_PATH = "leadforge.db"

# --- Owner account (used only to seed the first account - see note above) ---
# No default value on purpose: if these aren't set, the app should fail
# loudly at startup rather than silently create an account with a blank
# or guessable password.
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "")

if not OWNER_USERNAME or not OWNER_PASSWORD:
    raise RuntimeError(
        "OWNER_USERNAME and OWNER_PASSWORD environment variables are not "
        "set. On your own computer, set them before running python app.py "
        "(see the two 'set'/'export' commands below). On PythonAnywhere, "
        "set them at the top of your WSGI configuration file instead."
    )

# --- Secret key: generated once, then reused from disk ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SECRET_KEY_PATH = os.path.join(BASE_DIR, "secret.key")


def _load_or_create_secret_key():
    if os.path.exists(_SECRET_KEY_PATH):
        with open(_SECRET_KEY_PATH, "r") as f:
            existing = f.read().strip()
            if existing:
                return existing
    new_key = secrets.token_hex(32)
    with open(_SECRET_KEY_PATH, "w") as f:
        f.write(new_key)
    return new_key


SECRET_KEY = _load_or_create_secret_key()


# --- API secret: lets your local scripts (procore_finder.py, etc.) push
# leads straight into the database without logging in through a browser.
# Generated once, then reused from disk - same pattern as SECRET_KEY
# above. View it on the Import Leads page once you're logged in.
_API_SECRET_PATH = os.path.join(BASE_DIR, "api_secret.key")


def _load_or_create_api_secret():
    if os.path.exists(_API_SECRET_PATH):
        with open(_API_SECRET_PATH, "r") as f:
            existing = f.read().strip()
            if existing:
                return existing
    new_key = secrets.token_urlsafe(24)
    with open(_API_SECRET_PATH, "w") as f:
        f.write(new_key)
    return new_key


API_SECRET = _load_or_create_api_secret()


# --- Phase 4: free-tier keys for Intent Leads ---------------------------
# Both of these have a real, ongoing free tier - no card required.
# Set them as environment variables before running the app; see
# intent_engine.py for exactly where to get each one.
#
#   GEMINI_API_KEY - https://aistudio.google.com/apikey      (scoring)
#   GROQ_API_KEY   - https://console.groq.com/keys           (scoring fallback)
#
# Leaving either blank just means that provider gets skipped in favor
# of the other, instead of crashing. Search itself (Reddit's RSS feeds)
# needs no key at all.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
