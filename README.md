# Gmail Replier — Inbox Pilot

AI-powered Gmail assistant that reads incoming emails, decides whether to reply, drafts replies in your voice, and routes them based on your autonomy setting.

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
5. Create **OAuth 2.0 Client ID** → Desktop App
6. Download the JSON file, rename it to `credentials.json`, and place it in this project root

### 2. Anthropic API Key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Add to your shell profile (`~/.zshrc` or `~/.bashrc`) to persist.

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. First Run (OAuth Authorization)

```bash
python main.py
```

A browser window will open asking you to authorize Google access. After authorizing, `token.json` is saved and re-used automatically.

### 5. Open the UI

Visit: [http://localhost:8000](http://localhost:8000)

---

## Configuration

All settings are adjustable via the Settings panel in the UI or by editing `config.json`:

| Key | Default | Description |
|---|---|---|
| `poll_interval_minutes` | 30 | How often to check Gmail |
| `poll_start_hour` | 7 | Start polling at 7am local time |
| `poll_end_hour` | 20 | Stop polling at 8pm local time |
| `autonomy_level` | 1 | 1=review all, 2=smart, 3=full auto |
| `low_confidence_threshold` | 0.70 | Below this confidence, always review |

---

## Autonomy Levels

| Level | Label | Behavior |
|---|---|---|
| 1 | Review All | Every email queued for your review |
| 2 | Smart | Auto-sends high confidence, known, non-critical emails |
| 3 | Full Auto | Sends everything; only Drive attachments and unknown senders go to review |

**Hard rules (override all levels):**
- Emails with Drive file attachments → always review
- Unknown senders → always review

---

## Project Structure

```
gmail-replier/
├── main.py              # FastAPI server + API routes
├── config.py            # Settings management
├── auth.py              # Google OAuth2
├── gmail_client.py      # Gmail: read, draft, send
├── gcal_client.py       # Calendar: free/busy slots
├── gdrive_client.py     # Drive: search + attach
├── classifier.py        # Claude: classify email
├── drafter.py           # Claude: draft reply
├── processor.py         # Orchestration pipeline
├── autonomy_engine.py   # Routing logic
├── scheduler.py         # APScheduler poll loop
├── database.py          # SQLite persistence
├── requirements.txt
├── credentials.json     # ← you provide this
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── style.css
└── README.md
```

---

## Running

```bash
# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Start the server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
