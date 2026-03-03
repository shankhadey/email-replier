# Inbox Pilot

AI-powered Gmail assistant that reads incoming emails, decides whether to reply, drafts replies in your voice, and routes them based on your autonomy setting.

Connects to Gmail, Google Calendar, and Google Drive. Supports multiple users — each Google account is isolated with its own queue, settings, and AI profile. Hosted on Render (free tier or paid).

---

## Setup

### 1. Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or use existing)
3. Enable these APIs:
   - **Gmail API**
   - **Google Calendar API**
   - **Google Drive API**
4. Go to **APIs & Services > Credentials**
5. Create **OAuth 2.0 Client ID** → Web Application
6. Add your app URL as an authorized redirect URI: `https://<your-app>.onrender.com/auth/callback`
7. Note your **Client ID** and **Client Secret** — you'll set them as env vars below

### 2. Environment Variables

Set all of these in Render (or locally via `export`):

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key (`sk-ant-...`) |
| `GOOGLE_CLIENT_ID` | Yes | OAuth 2.0 client ID from Google Cloud |
| `GOOGLE_CLIENT_SECRET` | Yes | OAuth 2.0 client secret |
| `APP_BASE_URL` | Yes | Full app URL, no trailing slash — e.g. `https://your-app.onrender.com` |
| `JWT_SECRET_KEY` | Yes | Random 32-byte hex: `python -c "import secrets; print(secrets.token_hex(32))"` |

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. First Run

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export GOOGLE_CLIENT_ID=...
export GOOGLE_CLIENT_SECRET=...
export APP_BASE_URL=http://localhost:8000
export JWT_SECRET_KEY=<32-byte hex>
uvicorn main:app --host 0.0.0.0 --port 8000
```

Visit the app and click **Sign in with Google**. After authorising, your token is stored in the database automatically. Multiple users can each sign in with their own Google account — everything is isolated per account.

On first login, background setup runs automatically (~1 min): Inbox Pilot analyses your sent emails to learn your writing style, and classifies your top contacts for smarter drafting.

---

## Multi-User

Each Google account that signs in gets:
- Its own review queue (no cross-user data leakage)
- Its own config (poll interval, autonomy level, etc.)
- Its own AI voice profile (built from that account's sent emails)
- Its own scheduler job
- Its own activity log

---

## Configuring AI Behavior

Edit **`behavior_params.json`** to change how the AI classifies and drafts emails — no code changes needed. This file serves as the default template; each user's profile (built during setup) is stored per-user in the database and overrides these defaults.

| Section | What it controls |
|---|---|
| `user_identity` | Who the AI thinks you are (name, role, company, context) |
| `voice_profile` | How replies are written (traits, examples, tone, sign-off) |
| `classification_rules` | What makes an email high priority, critical, worth replying to, etc. |
| `routing_rules` | Documentation of routing logic (actual logic lives in `autonomy_engine.py`) |
| `calendar_defaults` | Work hours, timezone, minimum slot length |
| `gdrive_defaults` | Search strategy for Drive attachments |
| `email_fetch_rules` | Gmail query and label filters |

---

## Operational Configuration

Settings adjustable via the UI (Settings panel) — stored per-user in the database:

| Key | Default | Description |
|---|---|---|
| `poll_interval_minutes` | 30 | How often to check Gmail |
| `poll_start_hour` | 0 | Start polling hour (0–23) |
| `poll_end_hour` | 23 | Stop polling hour (0–23, inclusive) |
| `autonomy_level` | 1 | 1=review all, 2=smart, 3=full auto |
| `low_confidence_threshold` | 0.70 | Below this, always route to review |
| `lookback_hours` | 72 | Only process emails from the last N hours (0 = no limit) |
| `user_timezone` | America/Chicago | IANA timezone for calendar slots |
| `anthropic_model` | claude-sonnet-4-6 | Claude model to use |

---

## Autonomy Levels

| Level | Label | Behavior |
|---|---|---|
| 1 | Review All | Every drafted reply goes to the review queue |
| 2 | Smart | Auto-sends high confidence, known, non-critical emails |
| 3 | Full Auto | Sends everything; hard overrides still apply |

**Hard rules (override all levels):**
- Email with a Drive file to attach → always review
- Unknown sender → always review

---

## Calendar Availability

When an email asks about scheduling, the AI:
1. Detects the time window requested ("this week" → 7 days, "next two weeks" → 14 days)
2. Queries freebusy across **all calendars** connected to your Google account — including any linked external calendars (e.g. a work calendar from a second email). No separate login required.
3. Computes free slots within your configured work hours
4. Injects them into the draft in the format `2/18: 12-6pm`

---

## Google Drive Attachments

When an email requests a document (resume, proposal, etc.):
1. Searches Drive by **file name first** (e.g. `name contains 'resume'`)
2. Falls back to full-text content search only if no name match found
3. Attaches the best match to the reply
4. Emails with Drive attachments always route to review before sending

---

## Evals

**`evals.json`** contains 10 test cases covering the full classification and routing matrix:
- Recruiter + resume request
- Meeting scheduling (calendar lookup)
- Newsletter / automated email (skip)
- Unknown sender (always review)
- Job offer (critical)
- Multi-week availability window
- Low confidence / ambiguous email
- Casual executive follow-up
- Combined resume + availability request
- Personal thread

Each case specifies the input email, expected classification fields, expected routing at each autonomy level, and draft quality criteria.

---

## Project Structure

```
inbox-pilot/
├── main.py              # FastAPI server, JWT auth, all API routes
├── auth.py              # Google OAuth2 — multi-user, tokens stored in DB
├── background_setup.py  # First-login setup: voice profile + contact analysis
├── config.py            # Default config values (per-user config lives in DB)
├── params.py            # Behavioral parameters loader (reads behavior_params.json as default)
├── behavior_params.json # Default AI persona, voice, classification rules
├── evals.json           # Test cases for classifier and drafter evaluation
├── gmail_client.py      # Gmail: read, draft, send
├── gcal_client.py       # Calendar: free/busy across all connected calendars
├── gdrive_client.py     # Drive: name-first search + attach
├── classifier.py        # Claude: classify email (prompt built from behavior_params.json)
├── drafter.py           # Claude: draft reply (prompt built from behavior_params.json)
├── processor.py         # Orchestration pipeline (per-user)
├── autonomy_engine.py   # Routing logic (send / review / skip)
├── scheduler.py         # APScheduler — per-user poll jobs
├── database.py          # SQLite: users, tokens, configs, queue, activity log
├── requirements.txt
└── frontend/
    ├── index.html
    ├── app.js
    └── style.css
```

---

## Running Locally

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export GOOGLE_CLIENT_ID=...
export GOOGLE_CLIENT_SECRET=...
export APP_BASE_URL=http://localhost:8000
export JWT_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Visit: [http://localhost:8000](http://localhost:8000)

---

## Deployment (Render)

1. Push to GitHub
2. Create a new **Web Service** on Render pointing to your repo
3. Set all five environment variables in Render's **Environment** tab:
   - `ANTHROPIC_API_KEY`
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
   - `APP_BASE_URL` (your Render URL, e.g. `https://your-app.onrender.com`)
   - `JWT_SECRET_KEY` (generate once and keep it stable — changing it invalidates all sessions)

**Note on free tier:** Render free tier uses an ephemeral filesystem — `gmail_replier.db` (SQLite) will be wiped on every restart. For persistence, use Render's **Persistent Disk** add-on and mount it at `/data`, then set `DB_FILE` to `/data/gmail_replier.db`.
