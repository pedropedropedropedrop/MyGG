"""Authorize Google Calendar and Gmail for the default or a named account.

Use python setup_calendar.py for your personal account. For a second account,
set GOOGLE_EXTRA_ACCOUNTS=work in .env and run python setup_calendar.py work.
"""

import sys
from pathlib import Path
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv(Path(__file__).with_name(".env"))
from tools import GOOGLE_ACCOUNTS, GOOGLE_SCOPES
from utils import write_secret_file

CREDS_PATH = Path(__file__).parent / "credentials.json"


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

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), GOOGLE_SCOPES)
    creds = flow.run_local_server(port=8090, open_browser=True)
    write_secret_file(acct["token_path"], creds.to_json())
    print(f"\nAuthorization successful! Token saved to {acct['token_path']}")


if __name__ == "__main__":
    main()
