"""Backend API for the Ninoo app (FastAPI). Wraps the tested core; contains no rule logic.

Run locally:  uvicorn api:app --app-dir app --reload
Env vars:     DATA_DIR (default data), DB_PATH (default reviews.db),
              DRAFTER (default template, or nicrron:<model>), NICRRON_API_KEY (secret)
"""
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rules import validate  # noqa: E402
from run_day import pipeline, days_between  # noqa: E402
from assistant import answer_with_cases, load_case_questions  # noqa: E402

DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).resolve().parents[1] / "reviews.db"))
DRAFTER = os.environ.get("DRAFTER", "template")
GENIUS_EFFECTIVE = date(2027, 1, 18)

app = FastAPI(title="PennCoin OFAC Filings Assistant (SIMULATED)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

EVENTS = json.loads((DATA / "events.json").read_text())
SIM_DAYS = list(days_between(min(e["day"] for e in EVENTS), max(e["day"] for e in EVENTS)))
CASE_QUESTIONS = load_case_questions(DATA.parent / "prism" / "prism_labeled_set.csv")
_cache = {}


# ---------------------------------------------------------------- storage
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS reviews (item_id TEXT PRIMARY KEY, status TEXT,
                   edited_fields TEXT, ors_id TEXT, by TEXT, note TEXT, updated_at TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT)")
    con.execute("""CREATE TABLE IF NOT EXISTS review_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts TEXT, item_id TEXT, action TEXT, by TEXT, detail TEXT)""")
    return con


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sim_day():
    with db() as con:
        row = con.execute("SELECT v FROM settings WHERE k='sim_day'").fetchone()
    return row["v"] if row else SIM_DAYS[0]


def run(day=None):
    day = day or sim_day()
    if (DRAFTER, day) not in _cache:
        _cache[(DRAFTER, day)] = pipeline(DATA, DRAFTER, day)
    return _cache[(DRAFTER, day)]


def merged_queue(day=None):
    r = run(day)
    with db() as con:
        reviews = {row["item_id"]: dict(row) for row in con.execute("SELECT * FROM reviews")}
    items = []
    for item, filing in zip(r["queue"], r["filings"]):
        it = dict(item)
        rv = reviews.get(it["item_id"])
        if rv:
            it["status"] = rv["status"]
            it["ors_confirmation_id"] = rv["ors_id"]
            it["reviewed_by"] = rv["by"]
            if rv["edited_fields"]:
                it["draft_fields"] = json.loads(rv["edited_fields"])
        items.append(it)
    return items


def find(item_id):
    r = run()
    for item, filing in zip(r["queue"], r["filings"]):
        if item["item_id"] == item_id:
            return item, filing, r
    raise HTTPException(404, "No such review item")


def save(item_id, action, by, detail=None, **fields):
    with db() as con:
        cur = con.execute("SELECT * FROM reviews WHERE item_id=?", (item_id,)).fetchone()
        row = dict(cur) if cur else {"item_id": item_id, "status": "awaiting_review",
                                     "edited_fields": None, "ors_id": None, "by": None, "note": None}
        row.update(fields, by=by, updated_at=now())
        con.execute("""INSERT OR REPLACE INTO reviews (item_id, status, edited_fields, ors_id, by, note, updated_at)
                       VALUES (:item_id, :status, :edited_fields, :ors_id, :by, :note, :updated_at)""", row)
        con.execute("INSERT INTO review_log (ts, item_id, action, by, detail) VALUES (?,?,?,?,?)",
                    (now(), item_id, action, by, json.dumps(detail) if detail else None))


def current_status(item_id):
    with db() as con:
        row = con.execute("SELECT status FROM reviews WHERE item_id=?", (item_id,)).fetchone()
    return row["status"] if row else "awaiting_review"


# ---------------------------------------------------------------- models
class By(BaseModel):
    by: str


class Reject(BaseModel):
    by: str
    reason: str


class Edit(BaseModel):
    by: str
    fields: dict


class OrsId(BaseModel):
    by: str
    ors_id: str


class Day(BaseModel):
    day: str


class Ask(BaseModel):
    message: str


# ---------------------------------------------------------------- routes
@app.get("/api/health")
def health():
    return {"ok": True, "drafter": DRAFTER, "simulated": True}


@app.get("/api/briefing")
def briefing():
    day = sim_day()
    r = run(day)
    today = {c: v for c, v in r["cases"].items() if v["day"] == day}
    items = merged_queue(day)
    open_items = [i for i in items if i["status"] not in ("filed", "rejected")]
    d = date.fromisoformat(day)
    return {
        "sim_day": day,
        "simulated": True,
        "genius": {"effective": GENIUS_EFFECTIVE.isoformat(),
                   "days_since_effective": (d - GENIUS_EFFECTIVE).days},
        "rule_status": [
            {"rule": "GENIUS Act block/freeze/reject", "status": "statute, in force Jan 18, 2027"},
            {"rule": "FinCEN/OFAC PPSI AML and sanctions rule", "status": "proposed"},
            {"rule": "OFAC reporting 31 CFR 501.603/501.604", "status": "final, in force"},
            {"rule": "A1: on-chain Blacklisted reverts reportable", "status": "team assumption"},
        ],
        "sanctions_list": r["sdn"].version,
        "today": {
            "cases": len(today),
            "needs_filing": sum(1 for v in today.values() if v["filings"]),
            "no_filing": sum(1 for v in today.values() if not v["filings"]),
            "alerts": [a for v in today.values() for a in v["alerts"]],
        },
        "queue": {"open": len(open_items),
                  "needs_human_fix": sum(i["validator"] != "passed" for i in open_items)},
        "next_deadlines": sorted(({"item_id": i["item_id"], "title": i["title"], "due": i["due"]}
                                  for i in open_items), key=lambda x: x["due"])[:5],
    }


@app.get("/api/days")
def days():
    return {"days": SIM_DAYS, "current": sim_day()}


@app.post("/api/simulate/set-day")
def set_day(body: Day):
    if body.day not in SIM_DAYS:
        raise HTTPException(400, f"day must be one of {SIM_DAYS}")
    with db() as con:
        con.execute("INSERT OR REPLACE INTO settings (k, v) VALUES ('sim_day', ?)", (body.day,))
    return {"sim_day": body.day}


@app.post("/api/simulate/advance-day")
def advance_day():
    i = SIM_DAYS.index(sim_day())
    return set_day(Day(day=SIM_DAYS[min(i + 1, len(SIM_DAYS) - 1)]))


@app.post("/api/simulate/reset")
def reset():
    with db() as con:
        con.execute("DELETE FROM reviews")
        con.execute("DELETE FROM review_log")
        con.execute("DELETE FROM settings")
    _cache.clear()
    return {"sim_day": SIM_DAYS[0]}


@app.get("/api/queue")
def queue(status: str | None = None, case: str | None = None):
    items = merged_queue()
    if status:
        items = [i for i in items if i["status"] == status]
    if case:
        items = [i for i in items if i["case"] == case]
    return sorted(items, key=lambda i: (i["due"], i["item_id"]))


@app.get("/api/queue/{item_id}")
def queue_item(item_id: str):
    find(item_id)
    return next(i for i in merged_queue() if i["item_id"] == item_id)


@app.post("/api/queue/{item_id}/edit")
def edit(item_id: str, body: Edit):
    item, filing, r = find(item_id)
    if current_status(item_id) in ("filed",):
        raise HTTPException(409, "Filed reports cannot be edited")
    draft = {"filing_type": filing["type"], "due": filing["due"], "fields": body.fields}
    problems = validate(filing, draft, r["issuer"], {})
    if problems:
        save(item_id, "edit_rejected", body.by, problems, status="needs_human_fix")
        raise HTTPException(422, {"problems": problems})
    save(item_id, "edit", body.by, None, status="awaiting_review", edited_fields=json.dumps(body.fields))
    return queue_item(item_id)


@app.post("/api/queue/{item_id}/approve")
def approve(item_id: str, body: By):
    item, _, _ = find(item_id)
    status = current_status(item_id)
    if status == "filed":
        raise HTTPException(409, "Already filed")
    with db() as con:
        row = con.execute("SELECT edited_fields FROM reviews WHERE item_id=?", (item_id,)).fetchone()
    fixed_by_edit = bool(row and row["edited_fields"])  # edits only save after passing the validator
    if item["validator"] != "passed" and not fixed_by_edit:
        raise HTTPException(422, "Fix the validator problems (edit the draft) before approving")
    if status == "needs_human_fix":
        raise HTTPException(422, "The last edit failed validation; edit again before approving")
    save(item_id, "approve", body.by, None, status="approved")
    return queue_item(item_id)


@app.post("/api/queue/{item_id}/reject")
def reject(item_id: str, body: Reject):
    find(item_id)
    if current_status(item_id) == "filed":
        raise HTTPException(409, "Already filed")
    save(item_id, "reject", body.by, {"reason": body.reason}, status="rejected", note=body.reason)
    return queue_item(item_id)


@app.post("/api/queue/{item_id}/ors-id")
def ors_id(item_id: str, body: OrsId):
    find(item_id)
    if not body.ors_id.strip():
        raise HTTPException(422, "A report is filed only when a person enters the ORS confirmation ID")
    if current_status(item_id) != "approved":
        raise HTTPException(409, "Approve the draft before recording the ORS confirmation ID")
    save(item_id, "ors_id", body.by, {"ors_id": body.ors_id.strip()}, status="filed", ors_id=body.ors_id.strip())
    return queue_item(item_id)


@app.get("/api/cases")
def cases():
    r = run()
    return [{"case": c, **v} for c, v in sorted(r["cases"].items())]


@app.get("/api/cases/{case_id}")
def case(case_id: str):
    r = run()
    if case_id not in r["cases"]:
        raise HTTPException(404, "No such case (or not reached yet in the simulation)")
    return {"case": case_id, **r["cases"][case_id],
            "events": [e for e in EVENTS if e["case"] == case_id],
            "review_items": [i for i in merged_queue() if i["case"] == case_id]}


@app.get("/api/audit")
def audit():
    r = run()
    with db() as con:
        reviews = [dict(x) for x in con.execute("SELECT * FROM review_log ORDER BY id")]
    return {"system": r["state"].audit, "human": reviews}


@app.get("/api/score")
def score():
    r = pipeline(DATA, DRAFTER, None)
    return {"score": r["score"], "rows": r["rows"]}


@app.post("/api/assistant")
def assistant(body: Ask):
    """Text endpoint for PRISM: guarded answers, every call names its rule or signal."""
    day = sim_day()
    return answer_with_cases(body.message, run(day)["sdn"], day, EVENTS, CASE_QUESTIONS)
