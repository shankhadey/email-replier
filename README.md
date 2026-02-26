# Inbox Pilot

AI-powered Gmail assistant that reads incoming emails, decides whether to reply, drafts replies in your voice, and routes them based on your autonomy setting.

Connects to Gmail, Google Calendar, and Google Drive. Hosted on Render (free tier or paid).

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
6. Add your Render URL as an authorized redirect URI: `https://<your-app>.onrender.com/auth/callback`
7. Download the JSON file, rename it to `credentials.json`, place it in the project root

### 2. Anthropic API Key

Set as an environment variable (in Render's environment settings or locally):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. First Run (OAuth Authorization)

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Visit the app URL and click **Authorize with Google**. After authorizing, `token.json` is saved and reused automatically.

---

## Configuring AI Behavior

Edit **`behavior_params.json`** to change how the AI classifies and drafts emails — no code changes needed. Changes take effect on the next processed email.

| Section | What it controls |
|---|---|
| `user_identity` | Who the AI thinks you are (name, role, company, context) |
| `voice_profile` | How replies are written (traits, examples, tone, sign-off) |
| `classification_rules` | What makes an email high priority, critical, worth replying to, etc. |
| `routing_rules` | Documentation of routing logic (actual logic lives in `autonomy_engine.py`) |
| `calendar_defaults` | Work hours, timezone, minimum slot length |
| `gdrive_defaults` | Search strategy for Drive attachments |
| `email_fetch_rules` | Gmail query and label filters |

**How it works:** `params.py` loads `behavior_params.json` on every API call. `classifier.py` and `drafter.py` build their system prompts from the loaded params — so the AI model receives the latest version of your parameters on each email processed.

---

## Operational Configuration

Settings adjustable via the UI (Settings panel) or `config.json`:

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
├── main.py              # FastAPI server + API routes
├── config.py            # Operational settings (poll interval, autonomy level, etc.)
├── params.py            # Behavioral parameters loader (reads behavior_params.json)
├── behavior_params.json # AI persona, voice, classification rules — edit to tune behavior
├── evals.json           # Test cases for classifier and drafter evaluation
├── auth.py              # Google OAuth2
├── gmail_client.py      # Gmail: read, draft, send
├── gcal_client.py       # Calendar: free/busy across all connected calendars
├── gdrive_client.py     # Drive: name-first search + attach
├── classifier.py        # Claude: classify email (prompt built from behavior_params.json)
├── drafter.py           # Claude: draft reply (prompt built from behavior_params.json)
├── processor.py         # Orchestration pipeline
├── autonomy_engine.py   # Routing logic (send / review / skip)
├── scheduler.py         # APScheduler poll loop
├── database.py          # SQLite persistence + activity log
├── requirements.txt
├── credentials.json     # ← you provide this (Google OAuth client)
└── frontend/
    ├── index.html
    ├── app.js
    └── style.css
```

---

## Running Locally

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Visit: [http://localhost:8000](http://localhost:8000)

---

## Deployment (Render)

1. Push to GitHub
2. Create a new **Web Service** on Render pointing to your repo
3. Set environment variables: `ANTHROPIC_API_KEY`
4. Upload `credentials.json` and `token.json` as part of your deployment or as Render secrets

**Note on free tier:** Render free tier uses an ephemeral filesystem — `gmail_replier.db` (SQLite) and `token.json` will be wiped on every restart. For persistence, use Render's Persistent Disk add-on or an external database.
