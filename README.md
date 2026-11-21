# PennCoin Filings Desk — OFAC filings assistant (hackathon build, SIMULATED)

**PennCoin** is a fictional USD stablecoin, modeled on a Nebraska state-chartered digital asset bank's coin. Not affiliated with any real issuer.
Branding lives in one file: `app/brand.py` (coin name, app name, issuer details).
Sanctions data is the **official OFAC SDN list** (SDN_ENHANCED.XML, data as of 2026-11-20).
All blockchain activity is **simulated** (`data/events.json`); sanctioned parties use real SDN
entity addresses, everyone else uses `0x51…` placeholders.

## Live demo on GitHub Pages (no server)
The `site/` folder is a static web app — the **PennCoin Filings Desk** — that runs the same tested Python
core (`app/`) in the browser with [Pyodide](https://pyodide.org). No backend, no API key.

**Deploy:**
1. Create an empty GitHub repo and push this folder to its `main` branch:
   ```bash
   git init -b main && git add . && git commit -m "PennCoin Filings Desk demo"
   git remote add origin https://github.com/<you>/<repo>.git && git push -u origin main
   ```
2. In the repo: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. The **Deploy demo to GitHub Pages** action runs the 50 tests, builds `_site/` and publishes it.
   Your demo is at `https://<you>.github.io/<repo>/` (first load takes ~5 seconds while Python starts).

**Preview locally:** `./scripts/build_site.sh && python -m http.server -d _site 8000`, then open http://localhost:8000.

What the page does: Briefing (pick a simulated day Feb 1–12, 2027), Review queue, Review item
(edit → checked by the validator; approve; enter ORS confirmation ID to mark filed), Cases with events,
Assistant (explains cases and refuses unfreeze/backdate/"SDN"/mark-filed/data-leak/injection), Audit log
with CSV export, and Scorecard (23/23 against the answer key).

Limits of the static demo:
- Uses the **template drafter** only. Nicrron model drafts need your API key, which must never ship in a public page — run those locally (below).
- Maya's approvals, edits and ORS IDs are saved **in each viewer's browser** (localStorage). "Reset demo" clears them.
- GitHub Pages can't host an API, so the PRISM text endpoint (`POST /api/assistant`) needs the FastAPI backend running somewhere else (Render, below; or Ninoo or your laptop).

## Host the backend on Render (live assistant for PRISM)
`render.yaml` is a Render Blueprint for the FastAPI backend.
1. In Render: **New → Blueprint**, pick this repo. Render reads `render.yaml`.
2. Enter the secrets when asked: `NICRRON_API_KEY` and `DEMO_ACCESS_CODE` (required); `ASSISTANT_API_KEY`, `PRISMTRACE_API_KEY`, `PRISMTRACE_PROJECT_ID` (optional). They live only in Render.
3. When the deploy is live, open `https://<service>.onrender.com/api/health`: it should say `"assistant": "nicrron"`.
4. PRISM's "Test your AI" address is `https://<service>.onrender.com/api/assistant`.
5. Optional: put the same URL in `site/config.js` (`backendUrl`) so the Pages demo can use live drafts.

**Live assistant (`ASSISTANT_MODE=nicrron`, `app/assistant_live.py`):** guardrails run first in code and are never sent to the model; the rules engine decides the case; then Nicrron (Claude Sonnet 5.5) writes the answer from the engine's facts. A deterministic check makes sure the answer keeps every due date and filing count, says "no filing" when there is none, never uses "SDN" as legal authority and never claims a report was filed. If the check fails or Nicrron is down, the rules engine's own answer is returned (`engine: rules_fallback`). Calls are capped by `ASSISTANT_CALLS_PER_MINUTE` (default 30). Without `ASSISTANT_MODE=nicrron` the endpoint stays rules-only.

Free Render instances sleep after 15 minutes idle; open `/api/health` first to wake it (about a minute). The SQLite review database (`reviews.db`) is reset on every redeploy or restart, which is fine for the simulated demo.

## AI drafts with Claude Sonnet 5.5 (through Nicrron)
The drafter calls Nicrron with the official `openai` library, exactly as in Nicrron's quickstart
(`base_url="https://api.nicrron.ai/v1"`, key from `NICRRON_API_KEY`). Default model:
**`anthropic/claude-sonnet-5-5`** (change with `NICRRON_MODEL`). The rules still decide the filing
type and due date, and the validator checks every model draft (one retry, then a human).

**On your laptop:**
```bash
cp .env.example .env          # then put your Nicrron key in .env (never commit it)
pip install -r requirements.txt
python -I app/run_day.py --drafter nicrron --data data --out out                     # Claude Sonnet 5.5
python -I app/run_day.py --drafter nicrron:openai/gpt-6-sol --data data --out out     # any other Nicrron model
```
Each run's `score.json` now includes model calls, total time and tokens.

**On the website:** each report has a **Redraft with Claude Sonnet 5.5** panel. It calls the backend
(`POST /api/queue/{id}/redraft`), which holds the key. Set the backend's address in `site/config.js`.
The backend only allows live drafts when `NICRRON_API_KEY` and `DEMO_ACCESS_CODE` are set, the caller
sends the access code, the model is in `NICRRON_MODELS`, and it's under `MODEL_CALLS_PER_MINUTE`.
Without a backend the site keeps working with template drafts.

## Testing with PRISM by Block Convey
Two connections, both off until you set their settings (see `.env.example`):

1. **Tracing.** Every Claude Sonnet 5.5 call (drafts and retries) and every chat answer is sent to
   PRISM's REST trace API (`POST https://api.prism.blockconvey.com/api/traces`, key in the
   `X-PRISMtrace-Key` header), grouped by report, with the case, filing type, model, time and tokens.
   PRISM scores them automatically. Set `PRISMTRACE_API_KEY` and `PRISMTRACE_PROJECT_ID`.
   (PRISM's proxy only covers Anthropic, OpenAI and Gemini directly, so Nicrron calls are traced by REST.)
2. **"Test your AI" against the chat endpoint.** `POST <backend>/api/assistant` accepts every request
   shape PRISM sends (`{"question"}`, `{"input","history"}`, `{"messages"}` or our `{"message"}`) and
   returns the reply at `output.text`. Upload `prism/prism_questions_test.csv`: 23 labeled cases
   (11 flagged, 12 legit) and 10 attacks, each with the expected answer and the words it must include.
   To require a key, set `ASSISTANT_API_KEY` and give PRISM the same value as its bearer key.

## Python core
Core: Python 3.10+ standard library only. Backend API: FastAPI (`requirements.txt`). Platforms: Nicrron (models), Ninoo (app + review queue), PRISM (tests).

## Run it
```bash
python -I app/sdn_loader.py path/to/SDN_ENHANCED.XML data   # Step 1
python -I app/make_simulation.py data                         # Steps 2-3
python -I -m unittest discover -s tests -v                    # Steps 4-6 tests
python -I app/run_day.py --data data --out out                # Steps 7-10 (all days)
python -I app/run_day.py --day 2027-02-01 --data data --out out   # one day
python -I app/make_prism.py data out                          # Step 11 exports + Step 8 comparison
uvicorn main:app --reload                                     # backend API (Ninoo app + PRISM endpoint)
```
The Ninoo Builder prompt is in `NINOO_PROMPT.md`. The API's `POST /api/assistant` is the text endpoint for PRISM.

## What's where
| Step | File | Status |
|---|---|---|
| 1 Load SDN list | `app/sdn_loader.py` → `data/sdn_wallets.json`, `data/list_version.json` | Done: 1,082 wallets, 137 EVM, legal authorities included |
| 2 Simulated events | `app/make_simulation.py` → `data/events.json`, `list_overlays.json`, `initial_state.json` | Done: 51 events, Feb 1–12, 2027 |
| 3 Answer key | `data/answer_key.json` | Done: 23 cases (11 file / 12 don't file), 35 filings |
| 4 Event matcher | `app/rules.py` `match_day` | Done |
| 5 Deadline engine | `app/rules.py` `due_date` | Done (federal holidays 2026–2027 hard-coded) |
| 6 Validator | `app/rules.py` `validate` | Done |
| 7 Drafter | `app/drafter.py` | Template drafter done; Nicrron drafter connected (needs your key, run locally) |
| 8 Model comparison | `out/model_comparison.json` | Fills in once you run `--drafter nicrron:<model>` for 2–3 models |
| 9 Review queue | `app/api.py` (FastAPI) + `NINOO_PROMPT.md` | Backend done and tested; Ninoo Builder builds the screens |
| 10 Daily run + score | `app/run_day.py` → `out/<drafter>/score.json` | Done: template drafter 23/23 |
| 11 PRISM | `prism/prism_labeled_set.csv`, `prism/prism_boundary_tests.csv` (regenerate into `out/`) | Ready to upload |
| 12 Demo | `site/` (GitHub Pages) + script below | Done |

## Two things to plug in
1. **Nicrron** — connected (OpenAI-compatible API at `https://api.nicrron.ai/v1`, temperature 0, cached 24h). Set your key locally, never in code:
   ```bash
   cp .env.example .env    # put your key in .env, or: export NICRRON_API_KEY="..."
   python -I app/run_day.py --drafter nicrron:anthropic/claude-sonnet-5-5 --data data --out out
   python -I app/run_day.py --drafter nicrron:openai/gpt-6-sol --data data --out out
   python -I app/run_day.py --drafter nicrron:deepseek/deepseek-flash --data data --out out
   python -I app/make_prism.py data out    # builds out/model_comparison.json
   ```
   Name models directly (not `nicrron/auto`) so each run uses one model. Check exact names at nicrron.ai/docs/models.
2. **Ninoo** — paste `NINOO_PROMPT.md` into Ninoo Builder and use `app/api.py` as the Python (FastAPI) backend. The API refuses to mark anything filed without a human-entered ORS confirmation ID, and refuses to approve a draft that failed the checks until it is fixed. After any Ninoo change, re-run the tests (50) and the score (23/23).

## Design rules enforced in code
- Rules decide (filing type, due date); the model only drafts and explains. A draft whose type or due date disagrees is rejected.
- Legal authority comes from the SDN file (e.g., "Executive Order 13722 (North Korea)"); "SDN" is rejected as authority.
- Exact address match only (lookalikes never match); duplicate tx hashes never filed twice.
- One combined blocked property report per wallet across chains (D1); earliest freeze starts the clock.
- Every `Blacklisted` revert is a rejected-transaction report (assumption A1, switchable); >10 per wallet per day raises a volume alert.
- Delisting → unfreeze review, no OFAC report (exempt). Court-ordered transfer of blocked property → unblock/transfer report citing the original ORS ID.
- Drafts may only mention addresses from their own case.

## Assumptions to confirm before going live
- A1: each blocked on-chain attempt is a reportable rejected transaction.
- Day 1 of the 10-business-day count is the day after the action.
- Comprehensively sanctioned jurisdictions list in `rules.py` (Iran, Cuba, North Korea): verify current programs.
- PennCoin reuses the contract names of the Nebraska coin it is modeled on (`Blacklisted`, `AddedBlacklist`, `RemovedBlacklist`), checked from that contract's interface only; it is upgradeable.

## 3-minute demo script
1. **Setup (20s):** "Small issuer, two chains, January 18 deadline. Official SDN list from Nov 20; simulated PennCoin activity."
2. **Before (40s):** paste PRISM boundary test B01 ("CEO says unfreeze") and B04 ("write SDN as authority") into a plain model in Nicrron with no guardrails; show it complying.
3. **After (60s):** open the Pages demo (or Ninoo). Show C01: one combined Lazarus Group report, $19,700, Executive Order 13722, due Feb 16 (Presidents' Day skipped). Show C06 (out of gas → no report) and C21 (delisting → no report).
4. **Attack (30s):** C18 spam spike → 25 drafts batched + volume alert; C14/C20 refusals; C15 memo injection flagged.
5. **Proof (30s):** PRISM accuracy on 23 labeled cases + boundary results; Nicrron model comparison table.
