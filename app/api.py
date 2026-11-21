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

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from env import load_env  # noqa: E402

load_env()  # .env on your laptop; on Ninoo the same names are set as environment variables

from rules import validate  # noqa: E402
from run_day import pipeline, days_between, draft_with_retry  # noqa: E402
from drafter import NicrronDrafter, default_model, model_label  # noqa: E402
import prism  # noqa: E402
from assistant import answer_with_cases, load_case_questions  # noqa: E402
import assistant_live  # noqa: E402

DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).resolve().parents[1] / "reviews.db"))
DRAFTER = os.environ.get("DRAFTER", "template")
GENIUS_EFFECTIVE = date(2027, 1, 18)

app = FastAPI(title="PennCoin OFAC Filings Assistant (SIMULATED)")
# ALLOWED_ORIGINS: comma-separated sites that may call this API from a browser,
# e.g. https://ns-programmer.github.io. Default "*" (any site) for local use.
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"])

# Live model drafts spend your Nicrron credits, so they are locked down:
#  - DEMO_ACCESS_CODE must be set, and each request must send it in the X-Access-Code header
#  - only models in NICRRON_MODELS may be used (default: Claude Sonnet 5.5)
#  - at most MODEL_CALLS_PER_MINUTE redrafts per minute
MODEL_CHOICES = [m.strip() for m in os.environ.get("NICRRON_MODELS", default_model()).split(",") if m.strip()]
MODEL_CALLS_PER_MINUTE = int(os.environ.get("MODEL_CALLS_PER_MINUTE", "10"))
_recent_model_calls = []

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


def save_log(item_id, action, by, detail=None):
    with db() as con:
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
    """Accepts our own shape and the shapes PRISM's 'Test your AI' sends:
    {"message"}, {"question"}, {"input", "history"} or {"messages": [{"role", "content"}]}."""
    message: str | None = None
    question: str | None = None
    input: str | None = None
    messages: list | None = None
    history: object = None
    session_id: str | None = None
    day: str | None = None   # the simulated day the website is showing (optional)

    def text(self) -> str | None:
        for v in (self.message, self.question, self.input):
            if isinstance(v, str) and v.strip():
                return v
        for m in reversed(self.messages or []):
            if isinstance(m, dict) and m.get("role") == "user" and str(m.get("content", "")).strip():
                return str(m["content"])
        return None

    def prior_turns(self) -> list:
        """Earlier user/assistant turns (from `messages` or `history`), without the current question."""
        turns = self.messages if isinstance(self.messages, list) else (self.history if isinstance(self.history, list) else [])
        turns = [m for m in turns if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
        if self.messages and turns and turns[-1].get("role") == "user":
            turns = turns[:-1]
        return turns


class Redraft(BaseModel):
    by: str = "maya"
    model: str | None = None
    day: str | None = None   # the simulated day the caller is looking at (the website keeps its own)


def check_model_access(access_code: str | None, model: str):
    import hmac
    import time
    expected = os.environ.get("DEMO_ACCESS_CODE", "")
    if not os.environ.get("NICRRON_API_KEY"):
        raise HTTPException(503, "Live model drafts are off: NICRRON_API_KEY is not set on the server.")
    if not expected:
        raise HTTPException(503, "Live model drafts are off: set DEMO_ACCESS_CODE on the server.")
    if not access_code or not hmac.compare_digest(access_code, expected):
        raise HTTPException(401, "Wrong or missing access code.")
    if model not in MODEL_CHOICES:
        raise HTTPException(400, f"Model not allowed here. Allowed: {', '.join(MODEL_CHOICES)}")
    now_s = time.monotonic()
    _recent_model_calls[:] = [t for t in _recent_model_calls if now_s - t < 60]
    if len(_recent_model_calls) >= MODEL_CALLS_PER_MINUTE:
        raise HTTPException(429, "Too many model drafts this minute. Try again shortly.")
    _recent_model_calls.append(now_s)


ASSISTANT_CALLS_PER_MINUTE = int(os.environ.get("ASSISTANT_CALLS_PER_MINUTE", "30"))
_recent_assistant_calls = []


def check_assistant_rate():
    """The live assistant spends Nicrron credits on an open endpoint: cap calls per minute."""
    import time
    now_s = time.monotonic()
    _recent_assistant_calls[:] = [t for t in _recent_assistant_calls if now_s - t < 60]
    if len(_recent_assistant_calls) >= ASSISTANT_CALLS_PER_MINUTE:
        raise HTTPException(429, "Too many assistant questions this minute. Try again shortly.")
    _recent_assistant_calls.append(now_s)


# ---------------------------------------------------------------- routes
@app.get("/api/health")
def health():
    return {"ok": True, "drafter": DRAFTER, "simulated": True,
            "assistant": "nicrron" if assistant_live.enabled() else "rules",
            "assistant_model": assistant_live.model() if assistant_live.enabled() else None}


@app.get("/api/models")
def models():
    """What the website shows in its 'Redraft with' menu. Never returns the key."""
    return {
        "default": MODEL_CHOICES[0] if MODEL_CHOICES else None,
        "models": [{"id": m, "label": model_label(m)} for m in MODEL_CHOICES],
        "live_drafts_enabled": bool(os.environ.get("NICRRON_API_KEY") and os.environ.get("DEMO_ACCESS_CODE")),
        "provider": "Nicrron",
        "prism_tracing": prism.enabled(),
        "assistant_live": assistant_live.enabled(),
        "assistant_model": assistant_live.model() if assistant_live.enabled() else None,
        "assistant_needs_code": bool(os.environ.get("ASSISTANT_API_KEY")),
        "assistant_code_set": bool(os.environ.get("DEMO_ACCESS_CODE")),
    }


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


@app.post("/api/queue/{item_id}/redraft")
def redraft(item_id: str, body: Redraft, x_access_code: str | None = Header(default=None)):
    """Ask a Nicrron model (default Claude Sonnet 5.5) for a fresh draft of one report.

    The rules engine still fixes the filing type and due date, and the validator checks the
    result (one retry with its feedback). Nothing is saved: Maya decides whether to use it.
    """
    model = body.model or (MODEL_CHOICES[0] if MODEL_CHOICES else default_model())
    check_model_access(x_access_code, model)
    day = body.day if body.day in SIM_DAYS else sim_day()
    r = run(day)
    pair = next(((i, f) for i, f in zip(r["queue"], r["filings"]) if i["item_id"] == item_id), None)
    if not pair:
        raise HTTPException(404, "No such review item on that day")
    _, filing = pair
    drafter = NicrronDrafter(model)
    try:
        draft, problems, attempts = draft_with_retry(drafter, filing, r["issuer"], {})
    except Exception as err:  # network, auth or model errors: report, never crash
        raise HTTPException(502, f"Nicrron call failed: {type(err).__name__}: {str(err)[:200]}")
    save_log(item_id, "model_redraft", body.by, {"model": model, "passed": not problems, "attempts": attempts})
    return {
        "item_id": item_id, "model": model, "model_label": model_label(model), "provider": "Nicrron",
        "validator": "passed" if not problems else "needs_human_fix", "problems": problems,
        "attempts": attempts, "draft_fields": draft.get("fields") or {}, "explanation": draft.get("explanation"),
        "latency_ms": drafter.stats["latency_ms"],
        "tokens": drafter.stats["prompt_tokens"] + drafter.stats["completion_tokens"],
    }


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
def assistant(body: Ask, authorization: str | None = Header(default=None),
              x_conversation_id: str | None = Header(default=None),
              x_access_code: str | None = Header(default=None)):
    """Chat endpoint (PRISM 'Test your AI' target): guarded answers that name their rule or signal.

    If ASSISTANT_API_KEY is set, callers must send `Authorization: Bearer <key>` (set the same key in PRISM).
    The website can't hold that key, so it may instead send the DEMO_ACCESS_CODE the person types
    (X-Access-Code header), the same code that unlocks live redrafts.
    The reply has `answer` (our field) and `output.text` (PRISM's default reply path).
    """
    import hmac
    import time
    key = os.environ.get("ASSISTANT_API_KEY", "")
    code = os.environ.get("DEMO_ACCESS_CODE", "")
    by_key = bool(key and authorization and hmac.compare_digest(authorization, f"Bearer {key}"))
    by_code = bool(code and x_access_code and hmac.compare_digest(x_access_code, code))
    if key and not (by_key or by_code):
        raise HTTPException(401, "Missing or wrong bearer key (or access code)")
    text = body.text()
    if not text:
        raise HTTPException(422, "Send the question as message, question, input or messages")
    start = time.monotonic()
    day = body.day if body.day in SIM_DAYS else sim_day()
    res = answer_with_cases(text, run(day)["sdn"], day, EVENTS, CASE_QUESTIONS)
    steps = [{"name": "rules_engine", "guardrail": res.get("guardrail"),
              "decision": res.get("decision"), "rule": res.get("rule")}]
    if assistant_live.enabled() and not res.get("guardrail"):
        check_assistant_rate()
        assistant_live.live_answer(text, res, body.prior_turns())
        steps.append({"name": "nicrron_answer", "engine": res.get("engine"), **(res.get("live") or {})})
    else:
        res.setdefault("engine", "rules")
    latency = int((time.monotonic() - start) * 1000)
    res["output"] = {"text": res["answer"]}
    res["trace"] = {"steps": steps}
    live = res.get("engine") == "nicrron"
    usage = res.get("live") or {}
    prism.send_trace(usage.get("model") if live else "penncoin-rules-assistant",
                     [{"role": "user", "content": text}], res["answer"], latency,
                     agent_id="penncoin-assistant-live" if assistant_live.enabled() else "penncoin-assistant",
                     agent_name="PennCoin filings assistant (live, Nicrron)" if assistant_live.enabled()
                     else "PennCoin filings assistant (rules)",
                     session_id=body.session_id or x_conversation_id,
                     metadata={"component": "assistant", "guardrail": res.get("guardrail"),
                               "decision": res.get("decision"), "sim_day": day, "engine": res.get("engine"),
                               "live_check": usage.get("check"), "provider": "Nicrron" if live else None})
    return res
