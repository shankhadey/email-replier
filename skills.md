# email-drafter

An AI skill for Claude Cowork that acts as your personal email chief of staff.
On first run it learns your writing voice and maps your professional network.
Every morning at 6am it delivers a full brief — drafted replies, calendar context,
and clear approve/skip decisions. Every hour after that it scans for anything new.

---

## Prerequisites

Connect these plugins in Claude Cowork before running:
- **Gmail** — to read and send email
- **Google Calendar** — to check your schedule
- **Google Drive** — to find and attach files when requested

---

## How to Schedule

After first-run setup is complete:
1. Type `/schedule` in this Cowork task
2. Set frequency to: **Hourly**
3. Set days to: **Weekdays**
4. Done — Claude runs your email brief every hour, 6am–6pm, automatically

---

## What Gets Created

The skill stores everything in `.claude/email-drafter/` inside your project:

```
.claude/email-drafter/
  profile.md          ← your identity, writing voice, example replies, settings
  contacts.md         ← your professional network with relationship context
  last-refresh.md     ← when each reference was last updated
  brief-YYYY-MM-DD.md ← today's brief (a new one each day)
  email-drafter.log   ← execution log
```

Open any of these files in your editor at any time. They are plain markdown —
you can read them, edit them, and they will be used on the next run.

---

## Instructions for Claude

When this skill is invoked, follow every step below in order. Do not skip steps.
Do not guess at values that the steps tell you to compute with code.

---

### STEP 0 — Detect run mode

Run this Python script exactly as written. Use the output to decide what to do next.

```python
python3 -c "
from datetime import datetime
import pytz
tz_name = open('.claude/email-drafter/profile.md').read().split('Timezone:')[1].split('\n')[0].strip() if open('.claude/email-drafter/profile.md', 'r').read().__contains__('Timezone:') else 'America/Chicago'
tz = pytz.timezone(tz_name)
hour = datetime.now(tz).hour
if hour < 6 or hour >= 18:
    print('OUTSIDE_WINDOW')
elif hour == 6:
    print('FULL_BRIEF')
else:
    print('QUICK_SCAN')
" 2>/dev/null || echo "FULL_BRIEF"
```

- **OUTSIDE_WINDOW** → log "outside schedule window" and stop. Do nothing else.
- **FULL_BRIEF** → continue to Step 1
- **QUICK_SCAN** → skip to Step 5 (quick scan)

If profile.md does not exist yet, treat this as FULL_BRIEF and continue to Step 1.

---

### STEP 1 — First-run setup

**Skip this step entirely if `.claude/email-drafter/profile.md` already exists.**

#### 1a — Ask for identity

Say this to the user:

> Let's set up your email-drafter. I need four quick things:
> 1. Your full name?
> 2. Your role or title?
> 3. Your company?
> 4. One sentence about your main work focus?

Wait for answers before continuing.

#### 1b — Ask for autonomy level

Say this to the user:

> How much should I auto-send without asking you first?
>
> **1 — Review everything** (safest — nothing sends without your approval)
> **2 — Smart auto-send** (sends high-confidence replies to known contacts automatically;
> critical emails, unknown senders, and anything with attachments always go to your review queue)
> **3 — Full auto** (sends everything; unknown senders and attachments still go to review)
>
> Most people start with 1 and move to 2 after a week. Which level? (default: 1)

Wait for their answer. If they just press enter or say nothing, use 1.

#### 1c — Build voice profile

Fetch the last 100 sent emails. If fewer than 20 are returned, expand to 90 days and try again.

Build a sample: for each email take the subject line and the first 500 characters of the body.
Use up to 50 emails. Join them with `\n\n---\n\n` between each.

Call Claude Opus with temperature=1:

**System:** *(none)*

**User:**
```
Analyse these {N} email excerpts written by the same person.
Identify 5–8 concise bullet-point traits that describe their email writing style and voice.
Focus on: tone, length preference, formality level, sign-off style, punctuation habits, common phrases.
Output ONLY the bullet points, one per line, starting with "- ". No intro, no outro.

Emails:
{sample_text}
```

Also extract 2–3 example reply bodies from the fetched emails. Choose ones that are:
- Between 40 and 280 characters long
- Do not have a paragraph break (blank line) within the first 80 characters
- Feel representative of how this person actually writes

#### 1d — Build contact network

Fetch up to 500 sent email headers. Count how many times each recipient address appears.
Keep the top 20.

Call Claude Opus with temperature=0:

**User:**
```
Classify these email contacts based on their names, email addresses, and how frequently
they appear. For each contact determine:
- relationship_type: recruiter | colleague | manager | vendor | personal | unknown
- formality_level: formal | semi-formal | casual

Return ONLY a markdown list, one block per contact, exactly in this format:

## {Display Name} · {email@address.com}
Relationship: {relationship_type} · {formality_level}

Contacts to classify:
{list each as "Name <email> — N emails sent"}
```

Now fetch email subjects involving those 20 contacts (search all mail, not just inbox).
For each contact gather up to 20 subject lines from emails sent to or received from them.

Call Claude Haiku with temperature=0:

**User:**
```
For each person below, read their email subjects and extract 3–5 short topic keywords
or phrases that describe what you typically discuss with them.

Return exactly one line per person in this format:
email@address.com: topic one, topic two, topic three

People and their email subjects:
{for each contact: "email@address.com: subject1 | subject2 | subject3 ..."}
```

Now compute priority scores. Run this Python exactly — do not estimate the scores:

```python
python3 -c "
import sys
weights = {
    'personal': 0.90,
    'manager': 0.85,
    'colleague': 0.70,
    'recruiter': 0.50,
    'vendor': 0.40,
    'unknown': 0.20,
}
# contacts is a list of dicts: email, name, relationship_type, count
# replace the line below with actual data at runtime
contacts = CONTACTS_DATA_HERE
max_count = max(c['count'] for c in contacts)
for c in contacts:
    rel_weight = weights.get(c['relationship_type'], 0.20)
    interaction_score = c['count'] / max_count
    priority = round(0.45 * interaction_score + 0.55 * rel_weight, 2)
    print(f\"{c['email']}|{priority}\")
"
```

#### 1e — Write reference files

Write `.claude/email-drafter/profile.md`:

```markdown
# My Email Voice
Last updated: YYYY-MM-DD — refreshes every 30 days

## About Me
Name: {name}
Role: {role}
Company: {company}
Focus: {focus}

## How I Write
{bullet traits from step 1c, one per line}

## Example Replies I've Sent

> {example 1}

> {example 2}

> {example 3}

## Settings
Autonomy Level: {1|2|3} — {label}
Timezone: {timezone they are in, detected or asked}
Work Hours: 8am – 6pm
Confidence Threshold: 70%
```

Write `.claude/email-drafter/contacts.md`:

```markdown
# My Contact Network
Last updated: YYYY-MM-DD — refreshes every 7 days

---

## {Display Name} · {email}
Relationship: {relationship_type} · {formality_level}
Topics: {topic1}, {topic2}, {topic3}
Priority: {High|Medium|Low} · {N} interactions

---

{repeat for each contact}
```

Write `.claude/email-drafter/last-refresh.md`:

```markdown
# Refresh Log
Voice profile last updated: YYYY-MM-DD
Contacts last updated: YYYY-MM-DD
```

Create `.claude/email-drafter/email-drafter.log` if it does not exist.

Tell the user setup is complete and show the scheduling instructions from the top of this file.

---

### STEP 2 — Check for stale references

**Run on every FULL_BRIEF run. Skip on QUICK_SCAN.**

Run this Python exactly:

```python
python3 -c "
from datetime import datetime, timezone
import re
lines = open('.claude/email-drafter/last-refresh.md').read()
def days_since(label):
    m = re.search(label + r'.*?(\d{4}-\d{2}-\d{2})', lines)
    if not m: return 999
    d = datetime.strptime(m.group(1), '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d).days
print('voice_stale' if days_since('Voice profile') >= 30 else 'voice_ok')
print('contacts_stale' if days_since('Contacts') >= 7 else 'contacts_ok')
"
```

- **voice_stale** → silently re-run Step 1c and 1e to update profile.md. Do not interrupt the user.
- **contacts_stale** → silently re-run Step 1d and 1e to update contacts.md. Do not interrupt the user.

---

### STEP 3 — Fetch context for the brief

Read the full contents of profile.md and contacts.md.

Parse from profile.md:
- name, role, company, focus
- voice traits (the bullet list under "How I Write")
- example replies (the blockquotes under "Example Replies")
- autonomy level (the number before the dash)
- timezone
- work hours start and end
- confidence threshold (default 70% = 0.70)

Parse from contacts.md:
- For each contact block: email address, relationship_type, formality_level, topics, priority level, interaction count

Fetch today's calendar: all events for today, noting start/end times and titles.
Compute free blocks within work hours (e.g. 8am–6pm) where no event exists for at least 30 minutes.

Fetch unread emails:
- Query: `is:unread in:inbox -category:promotions -category:social -category:updates`
- From the last 24 hours
- Up to 50 emails
- Fetch full body for each

---

### STEP 4 — Process each email

For every email fetched, run this full pipeline. Track the results.

#### 4a — Look up the sender

Search contacts.md for the sender's email address.
If found: extract relationship_type, formality_level, topics, priority.
If not found: relationship_type = "unknown", formality_level = "formal", topics = none.

#### 4b — Classify the email

Call Claude Sonnet with temperature=0.

**System prompt** (fill in values from profile.md):
```
You are an email classifier for {name}, {role} at {company} ({focus}).

Classify the email below using these definitions:

sender_priority:
- high: executives, active recruiters, close collaborators, important business contacts
- medium: known colleagues, vendors, professional acquaintances
- low: mailing lists, newsletters, peripheral contacts
- unknown: first-time or completely unrecognized senders

is_critical = true when any of these apply:
- Hard deadline within the next 24–48 hours
- Financial or payment matter
- Legal or compliance matter
- Job offer or contract decision required
- Explicitly urgent decision needed

needs_calendar = true when the email asks for availability or proposes a meeting time.
calendar_days_requested: map "tomorrow" → 2, "this week" → 7, "next two weeks" → 14,
"this month" → 30. Default to 7 if unspecified.

needs_gdrive = true when the email asks for a document, report, resume, or specific file.
gdrive_query: use the exact filename if one is stated in the email. Otherwise use the most
distinctive multi-word phrase that describes the file. Never use generic single words like
"document", "file", "form", or "report" alone.

needs_reply = false (skip entirely) when: pure newsletter, automated system notification,
order confirmation, receipt, or promotional email with no question or action required.
{if contact found: "\nKnown contact context: {relationship_type}, {formality_level} formality, {interaction_count} prior interactions."}
```

**User prompt:**
```
From: {sender name and email}
Subject: {subject}
Has attachments: {true|false}

Body:
{first 2000 characters of body}

Return ONLY valid JSON with no markdown formatting:
{
  "needs_reply": true or false,
  "sender_priority": "high" or "medium" or "low" or "unknown",
  "confidence": a number from 0.0 to 1.0,
  "is_critical": true or false,
  "needs_calendar": true or false,
  "calendar_days_requested": a number or null,
  "needs_gdrive": true or false,
  "gdrive_query": "search phrase" or null,
  "reasoning": "one sentence explaining your classification"
}
```

#### 4c — Make the routing decision

Run this Python exactly. Pass in the actual values. Do not guess at the output.

```python
python3 << 'EOF'
import json, sys

classification_json = """REPLACE_WITH_ACTUAL_JSON"""
relationship_type = "REPLACE_WITH_ACTUAL_VALUE"
has_attachment_to_send = False   # set to True if a Drive file will be attached
autonomy_level = 1               # from profile.md
confidence_threshold = 0.70      # from profile.md

c = json.loads(classification_json)
rel = relationship_type

if not c['needs_reply']:
    print('SKIP|No reply needed')
elif rel == 'unknown' or c['sender_priority'] == 'unknown':
    print('REVIEW|Unknown sender — always review')
elif has_attachment_to_send:
    print('REVIEW|Has attachment — always review')
elif rel in ('colleague', 'manager', 'recruiter', 'vendor'):
    print(f'REVIEW|Known {rel} — always review')
elif autonomy_level == 1:
    print('REVIEW|Autonomy level 1 — review everything')
elif autonomy_level == 2:
    if c['confidence'] < confidence_threshold:
        pct = int(c['confidence'] * 100)
        print(f'REVIEW|Confidence {pct}% below threshold')
    elif c['is_critical']:
        print('REVIEW|Critical email — always review')
    else:
        print('SEND|High confidence, not critical, known contact')
else:
    print('SEND|Autonomy level 3')
EOF
```

The output is either `SKIP|reason`, `REVIEW|reason`, or `SEND|reason`.

#### 4d — Fetch calendar availability (if needs_calendar = true)

Fetch free slots from Google Calendar for the next `calendar_days_requested` days
(minimum 1, maximum 60). Only include slots within work hours from profile.md.
Only include slots of at least 30 minutes. Format as:

```
4/28 (Mon): 2:00–5:00pm
4/29 (Tue): 10:00–11:30am, 3:00–5:00pm
```

#### 4e — Find Drive attachment (if needs_gdrive = true)

Search Google Drive using the gdrive_query from the classification.

1. First search: `name contains '{query}' and trashed=false` — fetch up to 10 results
2. For each result, score it: count how many words from the query appear in the filename
   (lowercase, ignore punctuation and file extension). Score = matching words ÷ total query words.
3. If any result scores above 0, use the highest-scoring one.
4. If no result scores above 0, try a second search: `fullText contains '{query}' and trashed=false`
5. Apply the same scoring to fullText results. Only use a result if its filename scores above 0.
   If no fullText result scores above 0, attach nothing. A file that merely mentions the search
   term in its body but shares no words with the query in its filename is the wrong file.

#### 4f — Draft the reply (if not SKIP)

Call Claude Sonnet with temperature=1.

**System prompt:**
```
You are drafting an email reply on behalf of {name}.

{name}'s writing voice:
{voice traits from profile.md, one per line}

When sharing availability: {availability_format from profile.md}
When attaching a document: mention it briefly and naturally, no elaboration needed.

Example replies {name} has actually sent:
{example replies from profile.md, each as a blockquote}

Rules:
1. Write the reply body only — no subject line
2. Match tone to the relationship: {formality_level} for this sender
3. Be concise — if one sentence works, use one sentence
4. If calendar slots are provided, include them exactly as formatted — do not reformat
5. If an attachment is being sent, mention it briefly and naturally
6. Never use filler openers like "I hope this email finds you well"
7. Never add unnecessary sign-off lines — just {name} or nothing
8. If a specific meeting time is proposed, only agree if it falls in the provided free slots.
   If the proposed time is not available, say so and offer one or two slots that are.
```

**User prompt:**
```
Draft a reply to this email.
{if calendar_slots: "Free slots to share:\n{calendar_slots}\n"}
{if attachment_filename: "Attaching from Drive: {attachment_filename}\n"}
{if contact topics: "This person and I usually discuss: {topics}\n"}

From: {sender}
Subject: {subject}
Body:
{body, up to 2000 characters}
```

---

### STEP 5 — Quick scan (QUICK_SCAN mode only)

Read the last line of `.claude/email-drafter/email-drafter.log` to find the timestamp of the last run.
Fetch unread emails received since that timestamp only. Process each through steps 4a–4f.

Output a short update — do not write a full brief file:

```
## Email Update — {current time} {timezone}
{N} new email(s) since {last run time}

{if any critical: "🔴 CRITICAL: {subject} from {sender} — draft ready, needs your approval"}
{if any review: "🟡 {N} new in your review queue"}
{if any skip: "✅ {N} skipped ({reason summary})"}
{if nothing new: "Nothing new that needs your attention."}
```

---

### STEP 6 — Write the full morning brief (FULL_BRIEF mode only)

Write the brief to `.claude/email-drafter/brief-{YYYY-MM-DD}.md` and display it.

```markdown
# Morning Brief — {Day}, {Month Date, Year}
*Generated {time} {timezone} · {N} emails processed*

---

## Your Day
**Meetings:** {list meetings with times and titles, or "Nothing scheduled"}
**Free blocks:** {list free blocks of 30+ min within work hours}

---

## 🔴 Critical — needs you now ({N})

{for each critical email:}
**{subject}**
From: {sender name} ({sender email})
> {one sentence summary of what they want}

**Draft:**
{the drafted reply}

**Reason:** {routing reason}
→ **APPROVE** · **EDIT** · **SKIP**

---

## 🟡 Review Queue — drafted, approve before sending ({N})

{for each review email:}
**{subject}**
From: {sender name} ({relationship} · {priority} priority)
{if topics: "Topics: {topics}"}
> {one sentence summary}

**Draft:**
{the drafted reply}
{if attachment: "**Attaching:** {filename}"}

**Reason:** {routing reason}
→ **APPROVE** · **EDIT** · **SKIP**

---

## ✅ Auto-sent ({N})

{for each sent email: "- {sender} · {subject} → Sent ({reason})"}

---

## ⏭ Skipped ({N})

{for each skipped email: "- {sender} · {subject} → {reason}"}
```

---

### STEP 7 — Log the run

Append one line to `.claude/email-drafter/email-drafter.log`:

```
{ISO timestamp} | {FULL_BRIEF|QUICK_SCAN} | {N} processed | {N} critical | {N} review | {N} sent | {N} skipped
```

---

## Editing Your Profile

Open `.claude/email-drafter/profile.md` in any text editor to:
- Change your autonomy level (edit the number on the Autonomy Level line)
- Update your timezone
- Edit or add voice traits
- Replace the example replies
- Adjust your confidence threshold

Changes take effect on the next run. No reinstall needed.

## Editing Your Contacts

Open `.claude/email-drafter/contacts.md` to:
- Change a relationship type (e.g. promote someone from colleague to manager)
- Edit topics for a person
- Add a new contact block manually

To force a full contacts rebuild before the 7-day refresh, delete `last-refresh.md`
and run `/email-drafter` again.
