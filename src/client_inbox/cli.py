"""`client-inbox`: sign in to Google if needed, then run the local app."""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from . import db, google_auth, llm

SETUP_HELP = """
No Google OAuth client found.

Client Inbox needs one "Desktop app" OAuth client to read your Gmail and Drive.
Follow "Google setup" in the README (about 10 minutes, one time), then save the
downloaded file as:

    {path}

and run `client-inbox` again.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Gmail threads by client, with replies drafted by your Claude.")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "logout"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if args.command == "logout":
        google_auth.token_path().unlink(missing_ok=True)
        print("Signed out of Google. Your local data in ~/.client-inbox is unchanged.")
        return 0

    if not llm.available():
        print("Warning: Claude Code was not found. Install it and run `claude` once to log in;\n"
              "until then the inbox works but nothing is summarized or drafted.", file=sys.stderr)

    if google_auth.client_secrets_path() is None:
        print(SETUP_HELP.format(path=db.home() / "credentials.json"), file=sys.stderr)
        return 1

    if google_auth.load_credentials() is None:
        print("Opening your browser to sign in to Google...")
        google_auth.login()

    from . import gmail

    address = gmail.profile()["emailAddress"].lower()
    previous = db.get_setting("me")
    if previous and previous != address:
        print(f"This data folder belongs to {previous}, but you signed in as {address}.\n"
              f"Run `client-inbox logout` and sign in as {previous}, or set CLIENT_INBOX_HOME to a "
              "different folder for the second mailbox.", file=sys.stderr)
        return 1
    db.set_setting("me", address)

    import uvicorn

    url = f"http://127.0.0.1:{args.port}"
    print(f"Signed in as {address}. Client Inbox is running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run("client_inbox.app:app", host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
