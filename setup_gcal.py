"""One-time Google Calendar authorisation for Ghost.

Run this once from the repo root:

    python setup_gcal.py

It opens a browser, asks for read-only access to your calendars, and writes
`gcal_token.json` next to this file. After that, Ghost refreshes the token by
itself and this never needs running again - unless the authorisation is revoked
or the OAuth consent screen is left in "Testing" mode, which expires refresh
tokens after 7 days. Set it to "In production".

Before running, you need `credentials.json` in this folder - see CONSOLE_STEPS.

Both `credentials.json` and `gcal_token.json` are gitignored - they are secrets.
"""
import sys

from ghost.skills.calendar_feed import (
    CALENDARS, CREDENTIALS_PATH, SCOPES, TOKEN_PATH,
    _calendar_list, _matches, _save_token,
)

CONSOLE_STEPS = """\
One-time setup in Google Cloud Console (use the existing Gemini project):
  1. APIs & Services -> Library -> enable "Google Calendar API"
  2. APIs & Services -> OAuth consent screen -> publishing status "In production"
     (NOT "Testing" - Testing expires refresh tokens after 7 days, and you would
     be re-authorising every week for the same reason the old links kept dying)
  3. Credentials -> Create credentials -> OAuth client ID -> application type
     "Desktop app"
  4. Download the JSON and save it as credentials.json in this folder
Then run this script again."""


def main():
    if not CREDENTIALS_PATH.exists():
        print(f"No OAuth client file at {CREDENTIALS_PATH}\n")
        print(CONSOLE_STEPS)
        return 1

    if TOKEN_PATH.exists():
        print(f"{TOKEN_PATH.name} already exists - re-authorising will replace it.")
        if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
            return 0

    from google_auth_oauthlib.flow import InstalledAppFlow

    print("Opening a browser for Google sign-in...")
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    # prompt="consent" forces Google to issue a refresh token even if this
    # account has already approved the app before; without it a re-run can come
    # back with an access token only, and Ghost goes dead again in an hour.
    creds = flow.run_local_server(port=0, prompt="consent")
    _save_token(creds)
    print(f"Saved {TOKEN_PATH}")

    # Verifying the name lookup here is the point of the whole exercise: if a
    # calendar is named differently to what Ghost expects, it is far better to
    # find out now than during a briefing.
    print("\nCalendars visible to this account:")
    cals = _calendar_list(force=True)
    for c in sorted(cals, key=lambda c: c["name"].lower()):
        kinds = [k for k, name in CALENDARS.items() if _matches(name, c["name"])]
        tag = f"  <- {', '.join(kinds)}" if kinds else ""
        print(f"  {c['name']}{tag}")

    unmatched = [k for k, name in CALENDARS.items()
                 if not any(_matches(name, c["name"]) for c in cals)]
    if unmatched:
        print("\nNot found: " + ", ".join(f"{k} (expected \"{CALENDARS[k]}\")"
                                          for k in unmatched))
        print("If one of the calendars above is the right one under a different "
              "name, set GCAL_CLASSES_NAME / GCAL_ASSIGNMENTS_NAME / "
              "GCAL_FINALS_NAME in .env to its exact name.")
    else:
        print("\nAll three calendars resolved. Ghost is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
