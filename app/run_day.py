"""Step 10: process simulated days and score against the answer key.

Usage:
  python -I app/run_day.py --data data --out out                 # all days, template drafter
  python -I app/run_day.py --day 2027-02-01 --data data --out out
  python -I app/run_day.py --drafter nicrron:<model> ...         # once Nicrron is connected
"""
import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rules import SanctionsList, State, match_day, validate  # noqa: E402
from drafter import TemplateDrafter, NicrronDrafter  # noqa: E402
from ninoo import review_item, push  # noqa: E402

from brand import ISSUER  # noqa: E402,F401  (re-exported for api.py and tests)


def days_between(start: str, end: str):
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        yield d.isoformat()
        d += timedelta(days=1)


def make_drafter(name: str):
    return TemplateDrafter() if name == "template" else NicrronDrafter(name.split(":", 1)[1])


def pipeline(data: Path, drafter_name: str = "template", through_day: str | None = None, do_push: bool = False):
    """Run the simulation from the first day through `through_day` (default: last day).

    State carries over between days, so a later day always sees earlier freezes.
    """
    events = json.loads((data / "events.json").read_text())
    initial = json.loads((data / "initial_state.json").read_text())
    issuer = dict(ISSUER, token_ethereum=initial["tokens"]["ethereum"], token_polygon=initial["tokens"]["polygon"])
    sdn = SanctionsList(data)
    state = State(initial)
    drafter = make_drafter(drafter_name)

    first, last = min(e["day"] for e in events), max(e["day"] for e in events)
    cases, queue, filings_all = {}, [], []
    for day in days_between(first, through_day or last):
        results = match_day(day, events, sdn, state)
        for case, r in results.items():
            for filing in r["filings"]:
                draft = drafter.draft(filing, issuer)
                problems = validate(filing, draft, issuer, state.reported)
                if problems:  # one retry with the validator's feedback, then a human
                    draft = drafter.draft(filing, issuer, feedback=problems)
                    problems = validate(filing, draft, issuer, state.reported)
                filing["status"] = "drafted" if not problems else "needs_human"
                queue.append(review_item(filing, draft, problems, drafter.name))
                filings_all.append(filing)
                state.log(day=day, case=case, action="draft", filing_type=filing["type"],
                          due=filing["due"], drafter=drafter.name, problems=problems)
            state.log(day=day, case=case, action="decision", decision=r["decision"], reason=r["reason"])
            cases[case] = {"day": day, "decision": r["decision"], "reason": r["reason"], "alerts": r["alerts"],
                           "filings": [{"type": f["type"], "due": f["due"], "status": f["status"]} for f in r["filings"]]}
    if do_push:
        push(queue)

    # Score against the answer key (only cases up to through_day)
    key = {k["case"]: k for k in json.loads((data / "answer_key.json").read_text())}
    case_day = {e["case"]: e["day"] for e in events}
    rows, correct = [], 0
    for c, k in key.items():
        if through_day and case_day[c] > through_day:
            continue
        got = cases.get(c, {"decision": "missing", "filings": []})
        got_types = sorted(f["type"] for f in got["filings"])
        got_due = sorted({f["due"] for f in got["filings"]})
        exp_due = [k["due"]] if k["due"] else []
        ok = (got["decision"] == k["decision"] and got_types == sorted(k["filings"]) and got_due == exp_due)
        correct += ok
        rows.append({"case": c, "label": k["label"], "expected": k["decision"], "got": got["decision"],
                     "filings_expected": len(k["filings"]), "filings_got": len(got_types),
                     "due_expected": k["due"], "due_got": got_due[0] if got_due else None, "pass": ok})
    files = [r for r in rows if r["label"] == "file"]
    nofiles = [r for r in rows if r["label"] == "no_file"]
    score = {
        "drafter": drafter.name, "list_version": sdn.version, "cases": len(rows), "correct": correct,
        "missed_filings": sum(r["filings_got"] < r["filings_expected"] for r in files),
        "unneeded_filings": sum(r["filings_got"] > 0 for r in nofiles),
        "drafts": len(queue), "drafts_needing_human": sum(q["validator"] != "passed" for q in queue),
    }
    return {"cases": cases, "queue": queue, "filings": filings_all, "issuer": issuer, "state": state, "score": score, "rows": rows,
            "events": events, "sdn": sdn, "drafter": drafter}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="out")
    ap.add_argument("--day", help="run from the first day THROUGH this day")
    ap.add_argument("--drafter", default="template")
    args = ap.parse_args()

    out = Path(args.out)
    r = pipeline(Path(args.data), args.drafter, args.day, do_push=True)
    tag = r["drafter"].name.replace(":", "_").replace("/", "__")  # model names contain "/"
    (out / tag).mkdir(parents=True, exist_ok=True)
    for name, obj in [("cases", r["cases"]), ("ninoo_queue", r["queue"]), ("audit_log", r["state"].audit),
                      ("score", r["score"]), ("score_rows", r["rows"])]:
        (out / tag / f"{name}.json").write_text(json.dumps(obj, indent=2))
    print(json.dumps(r["score"], indent=2))
    for row in r["rows"]:
        print(("PASS " if row["pass"] else "FAIL ") + json.dumps(row))


if __name__ == "__main__":
    main()
