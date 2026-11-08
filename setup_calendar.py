"""Authorize Google Calendar and Gmail for the default or a named account.

Use python setup_calendar.py for your personal account. For a second account,
set GOOGLE_EXTRA_ACCOUNTS=work in .env and run python setup_calendar.py work.
"""

import logging
import sys
from pathlib import Path
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv(Path(__file__).with_name(".env"))
from tools import GOOGLE_ACCOUNTS, GOOGLE_SCOPES, TODO_BACKEND
from utils import write_secret_file

CREDS_PATH = Path(__file__).parent / "credentials.json"


def _verify_google_identity(creds, expected):
    from google.auth.transport.requests import AuthorizedSession
    with AuthorizedSession(creds) as session:
        resp = session.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", timeout=10)
        resp.raise_for_status()
        actual = resp.json().get("emailAddress", "")
    if not actual or (expected and actual.casefold() != expected.casefold()):
        raise ValueError("Google returned a different account than configured. Existing authorization was preserved.")
    return actual


def main():
    account = sys.argv[1] if len(sys.argv) > 1 else "personal"

    if account not in GOOGLE_ACCOUNTS:
        print(f"Unknown account '{account}'. Available: {', '.join(GOOGLE_ACCOUNTS)}")
        print("Add the account name to GOOGLE_EXTRA_ACCOUNTS in .env first.")
        raise SystemExit(1)
    acct = GOOGLE_ACCOUNTS[account]
    print(f"Setting up: {acct['label']}")
    print(f"Token will be saved to: {acct['token_path']}")

    if not CREDS_PATH.exists():
        print(
            "\nMissing credentials.json!\n\n"
            "1. Go to https://console.cloud.google.com/apis/credentials\n"
            "2. Create a project (or select existing)\n"
            "3. Enable 'Google Calendar API' and 'Gmail API' in APIs & Services > Library\n"
            "4. Create OAuth 2.0 Client ID (type: Desktop app)\n"
            "5. Download the JSON and save it as credentials.json in this folder\n"
            "6. Run this script again"
        )
        raise SystemExit(1)
    print(f"\nA browser window will open. Sign in with the {acct['label']} account.\n")

    scopes = list(GOOGLE_SCOPES)
    if account == "personal" and TODO_BACKEND == "google":
        scopes.append("https://www.googleapis.com/auth/tasks")
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), scopes)
    # The loopback URL contains an authorization code.
    logging.getLogger("google_auth_oauthlib.flow").setLevel(logging.WARNING)
    expected = acct["email"]
    creds = flow.run_local_server(port=8090, open_browser=True, timeout_seconds=300, prompt="select_account",
                                  **({"login_hint": expected} if expected else {}))
    actual = _verify_google_identity(creds, expected)
    print(f"Authorized Google account: {actual}")
    write_secret_file(acct["token_path"], creds.to_json())
    print(f"\nAuthorization successful! Token saved to {acct['token_path']}")


if __name__ == "__main__":
    main()
