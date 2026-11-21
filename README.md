# OFAC Filings Assistant — hackathon build (SIMULATED eUSD)

Concept demo modeled on a small state-chartered stablecoin issuer. Not affiliated with Telcoin.
Sanctions data is the **official OFAC SDN list** (SDN_ENHANCED.XML, data as of 2026-11-20).
All blockchain activity is **simulated** (`data/events.json`); sanctioned parties use real SDN
entity addresses, everyone else uses `0x51…` placeholders.

## Live demo on GitHub Pages (no server)
The `site/` folder is a static web app — the **Filings Desk** — that runs the same tested Python
core (`app/`) in the browser with [Pyodide](https://pyodide.org). No backend, no API key.

**Deploy:**
1. Create an empty GitHub repo and push this folder to its `main` branch:
   ```bash
   git init -b main && git add . && git commit -m "Filings Desk demo"
   git remote add origin https://github.com/<you>/<repo>.git && git push -u origin main
   ```
2. In the repo: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. The **Deploy demo to GitHub Pages** action runs the 19 tests, builds `_site/` and publishes it.
   Your demo is at `https://<you>.github.io/<repo>/` (first load takes ~5 seconds while Python starts).

**Preview locally:** `./scripts/build_site.sh && python -m http.server -d _site 8000`, then open http://localhost:8000.

What the page does: Briefing (pick a simulated day Feb 1–12, 2027), Review queue, Review item
(edit → checked by the validator; approve; enter ORS confirmation ID to mark filed), Cases with events,
Assistant (explains cases and refuses unfreeze/backdate/"SDN"/mark-filed/data-leak/injection), Audit log
with CSV export, and Scorecard (23/23 against the answer key).

Limits of the static demo:
- Uses the **template drafter** only. Nicrron model drafts need your API key, which must never ship in a public page — run those locally (below).
- Maya's approvals, edits and ORS IDs are saved **in each viewer's browser** (localStorage). "Reset demo" clears them.
- GitHub Pages can't host an API, so the PRISM text endpoint (`POST /api/assistant`) needs the FastAPI backend running somewhere else (e.g. Ninoo or your laptop).

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
uvicorn api:app --app-dir app --reload                        # backend API for the Ninoo app
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
   export NICRRON_API_KEY="sk-af-..."
   python -I app/run_day.py --drafter nicrron:anthropic/claude-sonnet-5-5 --data data --out out
   python -I app/run_day.py --drafter nicrron:openai/gpt-6-sol --data data --out out
   python -I app/run_day.py --drafter nicrron:deepseek/deepseek-flash --data data --out out
   python -I app/make_prism.py data out    # builds out/model_comparison.json
   ```
   Name models directly (not `nicrron/auto`) so each run uses one model. Check exact names at nicrron.ai/docs/models.
2. **Ninoo** — paste `NINOO_PROMPT.md` into Ninoo Builder and use `app/api.py` as the Python (FastAPI) backend. The API refuses to mark anything filed without a human-entered ORS confirmation ID, and refuses to approve a draft that failed the checks until it is fixed. After any Ninoo change, re-run the tests (19) and the score (23/23).

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
- eUSD contract names (`Blacklisted`, `AddedBlacklist`, `RemovedBlacklist`) checked from the interface only; the contract is upgradeable.

## 3-minute demo script
1. **Setup (20s):** "Small issuer, two chains, January 18 deadline. Official SDN list from Nov 20; simulated eUSD activity."
2. **Before (40s):** paste PRISM boundary test B01 ("CEO says unfreeze") and B04 ("write SDN as authority") into a plain model in Nicrron with no guardrails; show it complying.
3. **After (60s):** open the Pages demo (or Ninoo). Show C01: one combined Lazarus Group report, $19,700, Executive Order 13722, due Feb 16 (Presidents' Day skipped). Show C06 (out of gas → no report) and C21 (delisting → no report).
4. **Attack (30s):** C18 spam spike → 25 drafts batched + volume alert; C14/C20 refusals; C15 memo injection flagged.
5. **Proof (30s):** PRISM accuracy on 23 labeled cases + boundary results; Nicrron model comparison table.
