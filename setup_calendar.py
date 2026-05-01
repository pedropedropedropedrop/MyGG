"""Google OAuth authorization for Calendar + Gmail.

Usage:
  python setup_calendar.py             # Setup the default 'personal' account
  python setup_calendar.py <name>      # Setup an additional named account

To enable a second account (e.g. 'work'):
  1. Add the entry to the ACCOUNTS dict below.
  2. Add the matching entry to GOOGLE_ACCOUNTS in tools.py.
  3. Run `python setup_calendar.py work` to authorize and write the token file.

Both ACCOUNTS (here) and GOOGLE_ACCOUNTS (in tools.py) need the same key.
"""

import os
import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

ACCOUNTS = {
    "personal": {
        "token_path": Path(__file__).parent / "token.json",
        "label": os.getenv("GOOGLE_ACCOUNT_LABEL", "Personal Google account"),
    },
    # To add a second account, mirror this entry and the one in tools.py:
    # "work": {
    #     "token_path": Path(__file__).parent / "token_work.json",
    #     "label": os.getenv("GOOGLE_ACCOUNT_WORK_LABEL", "Work account"),
    # },
}

CREDS_PATH = Path(__file__).parent / "credentials.json"


def main():
    account = sys.argv[1] if len(sys.argv) > 1 else "personal"

    if account not in ACCOUNTS:
        print(f"Unknown account '{account}'. Available: {', '.join(ACCOUNTS.keys())}")
        print("To add a new account, edit ACCOUNTS in setup_calendar.py and GOOGLE_ACCOUNTS in tools.py.")
        return

    acct = ACCOUNTS[account]
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
        return

    print(f"\nA browser window will open. Sign in with the {acct['label']} account.\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
    creds = flow.run_local_server(port=8090, open_browser=True)
    acct["token_path"].write_text(creds.to_json())
    print(f"\nAuthorization successful! Token saved to {acct['token_path']}")


if __name__ == "__main__":
    main()
