# Client Inbox

A local app that watches your Gmail inbox, groups threads by the client they came from,
summarizes each new email, and writes a reply in your own voice using the thread, its
attachments, your Google Drive, files you upload, and your past sent mail.

Drafts stay in the app. Copy one, or press **Create Gmail draft** to put it in the Gmail
thread for you to review. **The app cannot send email**: there is no send code, and a test
fails if any is added.

Everything runs on your machine with your own Claude. Your mail is stored only in
`~/.client-inbox/` and is sent only to Google (to read it) and to Claude (to summarize and draft).

## Start

```bash
uv run client-inbox
```

Your browser opens `http://127.0.0.1:8765`. The first run asks you to sign in to Google.

Needs: [uv](https://docs.astral.sh/uv/), [Claude Code](https://claude.com/claude-code) installed
and logged in (run `claude` once), and the one-time Google setup below.

## Google setup (one time, about 10 minutes)

The app needs one Google "Desktop app" OAuth client. Whoever shares the app does this once;
everyone they share it with can reuse the same file.

1. Open https://console.cloud.google.com and create a project.
2. **APIs & Services > Library**: enable **Gmail API** and **Google Drive API**.
3. **APIs & Services > OAuth consent screen**: user type **External**, fill in the app name and
   your email, and add these scopes: `gmail.readonly`, `gmail.compose`, `drive.readonly`.
4. On the same screen, set publishing status to **In production**. Left on "Testing", Google
   signs every user out after 7 days. No verification is needed for up to 100 users; they see
   an "unverified app" warning and click **Advanced > Go to app**.
5. **APIs & Services > Credentials > Create credentials > OAuth client ID**, type **Desktop app**.
6. Download the JSON and save it as `~/.client-inbox/credentials.json`.

To share the app with the client built in, save that same file as
`src/client_inbox/oauth_client.json` before you hand the folder over. A desktop client's
"secret" is not confidential (Google documents this), but only commit it to a private repo.

Company Google Workspace accounts may block unverified apps; personal Gmail accounts work.

## How it works

1. **Poll**: every 60 seconds the app asks Gmail what changed.
2. **File**: the thread goes under a client. The sender's domain is the client (`acme.com` is
   "Acme"); free-mail senders (gmail.com and so on) are one client per address.
3. **Summarize**: Claude writes a short summary, decides whether you need to reply, and picks
   search keywords. Newsletters and notifications stop here.
4. **Gather**: attachment text, matching Drive files, matching uploaded files for that client,
   and your last few sent emails to that client.
5. **Draft**: Claude writes the reply using your voice profile, and lists which sources it used.

First run: the last 30 days of inbox threads are filed by client without calling Claude. An
older thread is summarized when you open it, and drafted when you press **Draft a reply**.
Only mail that arrives after install is drafted automatically.

Your voice profile is built once from your most recent 200 sent emails. **Relearn my voice**
rebuilds it.

## Organizing clients

- **Rename**: client bar > Rename.
- **Merge into…**: fold one client into another (its domains, threads and files all move).
- **Move to client…**: file one thread by hand; tick the box to send that sender's future mail
  there too.
- **Files tab**: upload PDF, Word, Excel or text files per client. Claude searches them when drafting.

## Safety

- Email is untrusted text. Claude runs with every tool switched off (`--tools ""`), no MCP
  servers or connectors, no user settings and an empty working directory, so an email cannot
  make it do anything except write text.
- The web app listens on `127.0.0.1` only and rejects requests from other websites.
- Google permissions: read mail, create drafts, read Drive. Nothing else.
- Do not point this at a mailbox holding regulated data (for example PHI) unless your
  organization has approved both this tool and the Claude account it uses.

## Commands

| Command | What it does |
|---|---|
| `uv run client-inbox` | Run the app |
| `uv run client-inbox --port 9000 --no-browser` | Other port, do not open the browser |
| `uv run client-inbox logout` | Forget the Google sign-in (local data is kept) |
| `uv run pytest` | Run the tests |

Settings by environment variable: `CLIENT_INBOX_HOME` (data folder, default `~/.client-inbox`),
`CLIENT_INBOX_FAST_MODEL` (default `haiku`), `CLIENT_INBOX_SMART_MODEL` (default `sonnet`).

## Files

| File | Purpose |
|---|---|
| `cli.py` | Sign-in and startup |
| `app.py` | Local web server and API |
| `pipeline.py` | File, summarize, gather sources, draft; poller and worker |
| `gmail.py` | Read threads and attachments, watch for changes, create drafts |
| `drive.py` | Drive search and text export |
| `clients.py` | Sender-domain rules, rename, merge, move |
| `voice.py` | Voice profile and past replies |
| `extract.py` | PDF, Word, Excel, HTML to text |
| `llm.py` | The locked-down `claude -p` call |
| `static/` | The three-pane screen |

## Decisions

- **Direct Google sign-in, not Claude's Gmail/Drive connectors.** Connectors were unreliable in
  headless `claude -p` runs and attachment download was unconfirmed (checked 2026-11-12).
- **Claude Code CLI as the engine** so each person uses their own Claude with no API key to manage.
- **Polling, not Gmail push.** Push needs a public endpoint; a local app has none.
- **No embeddings.** Uploaded files use SQLite full-text search; Drive uses Drive's own search.
- **Mail that arrives while the app is off** is picked up on the next start (Gmail keeps about
  a week of change history; beyond that the app looks back 3 days).

## Run history

- 2026-11-12: first build. Tests pass with Gmail, Drive and Claude faked; the real `claude -p`
  call was checked by hand. Not yet run against a real mailbox (needs the Google setup above).
