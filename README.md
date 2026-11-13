# Client Inbox

A local app that watches your Gmail inbox, groups threads by the client they came from,
summarizes each new email, and writes a reply in your own voice using the thread, its
attachments, your Google Drive, files you upload, and your past sent mail.

Drafts stay in the app. Copy one, or press **Create Gmail draft** to put it in the Gmail
thread for you to review. **The app cannot send email**: there is no send code, and a test
fails if any is added.

Everything runs on your machine with your own Claude. Your mail is stored only in
`~/.client-inbox/` and is sent only to Google (to read it) and to Claude (to summarize and draft).

## Try it first, with no account

```bash
uv run client-inbox demo
```

Opens `http://127.0.0.1:8766` on a made-up inbox (a small design studio, nine emails) with no
Google sign-in. Gmail, Drive and web pages are sample data; Claude is real, so the summaries,
OCR and drafts are written live (about 90 seconds for everything). It includes a scanned
invoice, a whiteboard photo, a web link, a Google Doc link, a newsletter and a phishing email.
**Deliver a sample email** makes five more arrive one at a time; the last two are California HR
questions (one routine, one high risk), best tried after loading the HR starter pack (that step
does go online). The demo keeps its data in
`~/.client-inbox-demo` and starts fresh on every run.

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
4. **Read the files**: every attachment is read and summarized. PDFs with a text layer, Word,
   Excel and text files are read directly; pictures and scanned PDFs are read by Claude (OCR).
   For mail that needs a reply, up to four links in the email are opened too: web pages, PDFs
   and pictures behind a link, and Google Docs/Sheets/Slides links (read through your Drive access).
5. **Gather**: those files and links, matching Drive files, matching uploaded files for that
   client, and your last few sent emails to that client.
6. **Draft**: Claude writes the reply using your voice profile, and lists which sources it used.

Each email has a **Files** tab listing its attachments and links with a summary, how each was
read, the extracted text, and a link to open the original. **Read files and links** reads
anything not yet read (older mail, or links on mail that needed no reply).

First run: the last 30 days of inbox threads are filed by client without calling Claude. An
older thread is summarized when you open it, and drafted when you press **Draft a reply**.
Only mail that arrives after install is drafted automatically.

Your voice profile is built once from your most recent 200 sent emails. **Relearn my voice**
rebuilds it.

## Reference library (HR starter pack)

**Reference library** at the top of the client list holds documents Claude consults for every
client, not just one. **Load HR starter pack (California + federal)** downloads 32 free,
official documents straight from the agencies' own sites (about 20 seconds):

- California Labor Commissioner FAQs: overtime, exemptions, meal and rest periods, paydays and
  final wages, vacation, minimum wage, deductions, paid sick leave, equal pay, contractors,
  lactation, personnel files, retaliation, plus the DLSE enforcement manual
- California Civil Rights Department (CFRA and pregnancy leave, FEHA, harassment training),
  EDD (Paid Family Leave, Employer's Guide DE 44), Cal/OSHA workplace violence prevention
- Federal: FMLA, FLSA exemption and hours-worked regulations (eCFR), EEOC guidance on ADA
  accommodation, PWFA, harassment and retaliation, IRS Publications 15 and 15-A

How it is used: when an email arrives, Claude sees the library's titles and picks the documents
worth consulting; the best passages from those go into the draft. Under the draft, every source
is listed as **Used** or **Checked**, and library sources link to the agency page they came from.
If the library does not cover a point, the draft says `[verify: ...]` instead of stating a rule
from memory. Web addresses are not pasted into the reply itself.

Good to know:
- The US Department of Labor and OSHA sites refuse automated downloads, so their fact sheets are
  not in the pack. Download them in a browser and add them with **Choose Files** in the library.
- You can add your own documents (paid handbooks you are licensed to use, your templates) the
  same way. They stay on your computer.
- Employment rules change. Each document shows when it was downloaded; **Refresh HR starter
  pack** fetches the current versions.
- Loading the pack also tells Claude the owner does HR consulting with mostly California
  employees. Drafts are a starting point for a professional to check, not legal advice.

## Client profiles

Each client has a **Profile** tab: employees, states, industry, pay schedule, payroll system,
free-form notes, and the people who have written in. Claude fills in what an email states
outright ("we have 38 employees, all in California") and marks it "from email"; anything the
owner types wins and is never overwritten. The profile goes into every draft for that client,
so a reply can say which rules apply instead of "if you have 50 or more employees". A one-line
digest shows under the client's name.

## High-risk flags

When an email that needs a reply involves a harassment, discrimination or retaliation
complaint, an employee in a protected situation (pregnant, on or asking for leave, disabled,
injured, recently complained) facing termination or discipline, a wage claim or agency charge,
an attorney letter or lawsuit threat, union activity, or a threat of violence, it gets a red
**High risk** badge with the reasons. The draft for that email is a holding reply: pause the
action, list the facts and documents needed, and note that employment counsel may be advised.
**Not high risk: write a normal reply** clears the flag and rewrites the draft. The flag is
Claude's judgment and can miss things; it is a prompt to slow down, not a guarantee.

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
- Links are opened cautiously: only for mail that needs a reply, at most four per email, with a
  plain GET and no cookies. Links that look like actions or trackers are never opened
  (unsubscribe, confirm, verify, reset, sign in, accept, tracking redirects), nor are social
  media or bare home pages, nor anything pointing at this computer or your local network.
  Opening a link does tell that website someone looked at it.
- Pictures and scanned PDFs are sent to Claude inside the message; Claude still has no tools.
- The web app listens on `127.0.0.1` only and rejects requests from other websites.
- Google permissions: read mail, create drafts, read Drive. Nothing else.
- Do not point this at a mailbox holding regulated data (for example PHI) unless your
  organization has approved both this tool and the Claude account it uses.

## Commands

| Command | What it does |
|---|---|
| `uv run client-inbox demo` | Sample inbox, no Google account needed |
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
| `files.py` | Attachments and links per email: pick, read, OCR, summarize |
| `web.py` | Opens a link safely (public sites only, size and time limits) |
| `extract.py` | PDF, Word, Excel, HTML to text; link extraction; image resizing |
| `library.py` | Shared reference library and the HR starter pack list |
| `profile.py` | Per-client facts: learned from mail, edited by the owner |
| `demo.py` | The sample inbox behind `client-inbox demo` |
| `llm.py` | The locked-down `claude -p` call |
| `static/` | The three-pane screen |

## Decisions

- **Direct Google sign-in, not Claude's Gmail/Drive connectors.** Connectors were unreliable in
  headless `claude -p` runs and attachment download was unconfirmed (checked 2026-11-12).
- **Claude Code CLI as the engine** so each person uses their own Claude with no API key to manage.
- **Polling, not Gmail push.** Push needs a public endpoint; a local app has none.
- **No embeddings.** Uploaded files use SQLite full-text search; Drive uses Drive's own search.
- **OCR by Claude, not a local OCR engine.** Nothing extra to install, and it also describes
  pictures that have no text. Scanned PDFs are capped at their first 20 pages.
- **Links are opened only on mail that needs a reply**, so newsletters and notifications never
  cause a visit to anyone's site.
- **Mail that arrives while the app is off** is picked up on the next start (Gmail keeps about
  a week of change history; beyond that the app looks back 3 days).

## Run history

- 2026-11-12: first build. Tests pass with Gmail, Drive and Claude faked; the real `claude -p`
  call was checked by hand. Not yet run against a real mailbox (needs the Google setup above).
- 2026-11-12: added attachment and link reading with OCR, the per-email Files tab and demo mode.
  40 tests pass. Demo run end to end with live Claude: 9 sample emails in about 90 seconds; the
  scanned invoice and whiteboard photo were transcribed correctly and used in the drafts. Link
  opening against real websites is covered by tests of the address checks only, not yet tried live.
- 2026-11-12: added the shared reference library and HR starter pack. 47 tests pass. Live check:
  32 of 32 documents downloaded in 20 seconds (about 3.2 million characters); a California
  termination question (final pay, vacation payout, withholding for unreturned property) was
  answered from the Labor Commissioner pages with linked sources in 3 of 3 runs.
- 2026-11-12: added client profiles and high-risk flags. 53 tests pass. Live demo check: the
  profile picked up headcount, state and pay schedule from one email; a pregnancy-plus-complaint
  termination email was flagged with three reasons and got a holding reply; no other sample
  email was flagged.
