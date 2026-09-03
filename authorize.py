#!/usr/bin/env python3
"""One-time OAuth: exchange a Google client secret for a stored refresh token.

Run once. The refresh token it writes to .env is what every later run uses, so
the browser consent never happens again unless the token is revoked.

Usage:
    ./.venv/bin/python authorize.py client_secret_*.json

Prints an authorisation URL rather than launching a browser itself, because this
usually runs inside an agent session where the browser and the terminal are not
the same context. Open the URL, approve, and the loopback server catches the
redirect.
"""

import json
import pathlib
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

# gmail.modify is every read and write operation EXCEPT permanent deletion —
# reading, searching, labelling, archiving, drafting and sending all included.
# It cannot bypass the trash, and it grants nothing over account settings
# (filters, forwarding, send-as), which live in gmail.settings.* and are not
# requested. So the worst an agent can do to this mailbox is recoverable.
#
# contacts (not contacts.readonly) because renaming and merging entries is a
# wanted operation. There is no narrower Google scope — no read-plus-edit-names
# variant — so this also carries delete. Unlike Gmail there is no trash to
# recover from: People API deletes are immediate and permanent. Keep destructive
# contact operations out of the tool surface; read paths use it read-only.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/contacts",
]

HERE = pathlib.Path(__file__).parent
ENV = HERE / ".env"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    secret_path = pathlib.Path(sys.argv[1]).expanduser()
    if not secret_path.exists():
        print(f"no such file: {secret_path}")
        return 2

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=False,
                                  authorization_prompt_message="\nApprove here:\n\n{url}\n")

    if not creds.refresh_token:
        print("no refresh token returned — revoke the app's access and re-run")
        return 1

    client = json.loads(secret_path.read_text())
    client = client.get("installed") or client.get("web") or {}

    ENV.write_text(
        f"GOOGLE_CLIENT_ID={client['client_id']}\n"
        f"GOOGLE_CLIENT_SECRET={client['client_secret']}\n"
        f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}\n"
    )
    ENV.chmod(0o600)
    print(f"\nwrote {ENV} (refresh token stored, mode 600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
