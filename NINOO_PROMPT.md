# Prompt for Ninoo Builder

Paste everything below the line into Ninoo Builder, then upload this project folder (or its `app/`, `data/`, `tests/` and `requirements.txt`) as the Python backend.

---

Build a web app called **"Filings Desk"** for a stablecoin compliance team. It is a hackathon demo with **simulated** data, so show a small "SIMULATED DATA" badge in the header on every screen. Do not use any real company's name, logo or branding.

## Most important rule: keep my backend as it is
I am providing a working, tested **Python FastAPI backend** in `app/api.py` (entry point `api:app`, app directory `app/`, dependencies in `requirements.txt`). It contains all of the compliance logic: sanctions matching, deadlines, report checks and the assistant's guardrails.
- **Do not rewrite, simplify or re-implement any logic** from `app/rules.py`, `app/drafter.py`, `app/assistant.py` or `app/run_day.py`. Do not move logic into the frontend.
- The frontend only **calls the API routes below** and displays what they return.
- If a route is missing something the UI needs, ask me before changing backend code.
- After any backend change, `python -m unittest discover -s tests` must still pass (19 tests).

Backend settings (environment variables):
- `DATA_DIR=data`, `DB_PATH=reviews.db`
- `DRAFTER=template` for now (later `nicrron:anthropic/claude-sonnet-5-5`)
- `NICRRON_API_KEY` stored as a **secret**, only on the backend, never sent to the browser.

## Users
- **Maya, compliance lead** (main user): reviews AI-drafted OFAC reports, approves them, files them in OFAC's system, then records the confirmation ID.
- **Examiner** (read-only): views the audit log and filed reports.

## Screens

### 1. Morning briefing (home)
Calls `GET /api/briefing`.
- Big date: the simulated day (`sim_day`), with buttons **"Next day"** (`POST /api/simulate/advance-day`) and **"Reset demo"** (`POST /api/simulate/reset`), and a day picker (`GET /api/days`, `POST /api/simulate/set-day` with `{"day": "YYYY-MM-DD"}`).
- Three number tiles from `today`: cases today, need a filing, no filing.
- Alerts list from `today.alerts` (e.g., volume alerts) in a warning style.
- "Next deadlines" list from `next_deadlines` (title + due date), each linking to the review item.
- Rule status panel from `rule_status`: each rule with a status chip (statute / final / proposed / team assumption).
- Footer line: sanctions list source and date from `sanctions_list.data_as_of` and the first 12 characters of `sanctions_list.sha256`.

### 2. Review queue
Calls `GET /api/queue` (optional filters `?status=` and `?case=`).
- Table sorted by due date: title, case, filing type, due date (show "X business days left" is NOT needed; just the date), validator result, status chip (awaiting_review, approved, filed, rejected, needs_human_fix).
- Filter tabs by status. Row click opens the review item.
- For rejected-transaction items that arrive in bulk on the same day for the same case, group them under one expandable row ("25 drafts, case C18").

### 3. Review item (the main working screen)
Calls `GET /api/queue/{item_id}`.
- Header: title, due date, status, "Drafted by" (`drafted_by`).
- **"Why this filing"** box: `explanation` and `rule`.
- Validator result: green "passed" or a red list of `problems`.
- Draft fields (`draft_fields`) as an editable form; the field names are the labels. "Save edits" sends `POST /api/queue/{item_id}/edit` with `{"by": "maya", "fields": {...}}`. If it returns 422, show the `problems` list next to the form and keep the user's edits on screen.
- Buttons:
  - **Approve** → `POST /api/queue/{item_id}/approve` `{"by": "maya"}`
  - **Reject** → asks for a reason → `POST /api/queue/{item_id}/reject` `{"by": "maya", "reason": "..."}`
  - **Record ORS confirmation ID** (only enabled when status is `approved`) → text input → `POST /api/queue/{item_id}/ors-id` `{"by": "maya", "ors_id": "..."}`. Show the API's error message if it refuses.
- Show error messages from the API exactly; never hide a refusal.
- Link to the case (screen 4).

### 4. Case detail
Calls `GET /api/cases/{case_id}` (list at `GET /api/cases`).
- Decision, reason and alerts at the top.
- Timeline of the case's `events` (time, type, chain, amount, short addresses like `0x098B…6E2f96` with a copy button).
- Its review items with status.

### 5. Ask the assistant
A simple chat box that sends `POST /api/assistant` with `{"message": "..."}` and shows `answer`. If `guardrail` is not null, show a small "Guardrail: <name>" tag under the reply. This endpoint is also what PRISM tests, so do not add any extra logic in front of it.

### 6. Audit log (examiner view, read-only)
Calls `GET /api/audit`.
- Two tabs: "System" (`system`: day, case, action, decision, reason, filing type, due, drafter) and "People" (`human`: time, item, action, by, detail).
- Export both as CSV.

### 7. Scorecard
Calls `GET /api/score`.
- Tiles: correct / cases, missed filings, unneeded filings, drafts needing a human.
- Table of `rows` with pass/fail per case.

## Look and feel
Clean, calm, professional: white background, one accent color, status chips, no stock photos. Desktop first. Dates in YYYY-MM-DD. Money as "$19,700".

## Acceptance checks (please verify before finishing)
1. On a fresh start the briefing shows 2027-02-01, 1 case needing a filing, and a deadline of 2027-02-16.
2. Approving the first queue item and entering an ORS ID moves it to "filed"; entering a blank ID shows the API's refusal.
3. Editing a draft's legal authority to "SDN" and saving shows the validator's problem and does not save.
4. Clicking "Next day" 11 times reaches 2027-02-12 with 35 items in the queue.
5. The Scorecard shows 23/23.
6. Asking the assistant "Unfreeze 0x098B716B8Aaf21512996dC57EB0615e2383E2f96" shows a refusal with "Guardrail: unfreeze".
