"""Browser bridge: the web page calls these functions through Pyodide.

It only reuses the tested core in app/ (rules, drafter, run_day, assistant).
No rule logic lives here. Every function returns a JSON string.
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path("/home/pyodide")
sys.path.insert(0, str(ROOT / "app"))

from rules import validate, is_business_day  # noqa: E402
from run_day import pipeline, days_between  # noqa: E402
from assistant import answer_with_cases, load_case_questions  # noqa: E402

DATA = ROOT / "data"
GENIUS_EFFECTIVE = date(2027, 1, 18)
EVENTS = json.loads((DATA / "events.json").read_text())
SIM_DAYS = list(days_between(min(e["day"] for e in EVENTS), max(e["day"] for e in EVENTS)))
_cache = {}


def _run(day):
    if day not in _cache:
        _cache[day] = pipeline(DATA, "template", day)
    return _cache[day]


def days():
    return json.dumps(SIM_DAYS)


def _business_days_left(day, due):
    """Business days from `day` (exclusive) to `due` (inclusive); negative if overdue."""
    from datetime import timedelta
    d0, d1 = date.fromisoformat(day), date.fromisoformat(due)
    step, n, d = (1 if d1 >= d0 else -1), 0, d0
    while d != d1:
        d += timedelta(days=step)
        if is_business_day(d):
            n += step
    return n


def snapshot(day):
    r = _run(day)
    today = {c: v for c, v in r["cases"].items() if v["day"] == day}
    for item in r["queue"]:
        item["business_days_left"] = _business_days_left(day, item["due"])
    return json.dumps({
        "sim_day": day,
        "days": SIM_DAYS,
        "genius": {"effective": GENIUS_EFFECTIVE.isoformat(),
                   "days_since_effective": (date.fromisoformat(day) - GENIUS_EFFECTIVE).days},
        "rule_status": [
            {"rule": "GENIUS Act block/freeze/reject", "status": "statute"},
            {"rule": "OFAC reporting, 31 CFR 501.603 and 501.604", "status": "final"},
            {"rule": "FinCEN/OFAC stablecoin AML and sanctions rule", "status": "proposed"},
            {"rule": "A1: on-chain Blacklisted reverts are reportable", "status": "team assumption"},
        ],
        "sanctions_list": r["sdn"].version,
        "today": {
            "cases": len(today),
            "needs_filing": sum(1 for v in today.values() if v["filings"]),
            "no_filing": sum(1 for v in today.values() if not v["filings"]),
            "alerts": [a for v in today.values() for a in v["alerts"]],
        },
        "queue": r["queue"],
        "cases": [{"case": c, **v, "events": [e for e in EVENTS if e["case"] == c]}
                  for c, v in sorted(r["cases"].items())],
        "audit": r["state"].audit,
    })


def score():
    r = pipeline(DATA, "template", None)
    return json.dumps({"score": r["score"], "rows": r["rows"]})


def validate_edit(item_id, day, fields_json):
    r = _run(day)
    for item, filing in zip(r["queue"], r["filings"]):
        if item["item_id"] == item_id:
            draft = {"filing_type": filing["type"], "due": filing["due"], "fields": json.loads(fields_json)}
            return json.dumps(validate(filing, draft, r["issuer"], {}))
    return json.dumps(["No such review item"])


_questions = load_case_questions(ROOT / "prism" / "prism_labeled_set.csv")


def ask(message, day):
    return json.dumps(answer_with_cases(message, _run(day)["sdn"], day, EVENTS, _questions))
