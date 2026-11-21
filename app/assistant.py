"""Guarded assistant: the text endpoint PRISM tests.

1. Guardrails run first, in code, on the person's own words (not on quoted data).
2. If the message carries event JSON lines (the PRISM labeled set does), the real
   rules engine decides on those facts and the answer names the rule behind it.
3. Anything else gets a short, safe answer. The model never overrides a guardrail.
"""
import json
import re

from rules import ADDR_RE, INJECTION_RE, SanctionsList, State, match_day

RULES_TEXT = {
    "blocked_property": "31 CFR 501.603(b)(1): report blocked property within 10 business days",
    "rejected_transaction": "31 CFR 501.604: report rejected transactions within 10 business days (on-chain Blacklisted reverts per assumption A1)",
    "unblock_transfer": "31 CFR 501.603(b)(3): report unblocked or transferred blocked property within 10 business days",
}

GUARDS = [
    ("data_leak", re.compile(r"(other (holders|customers|clients)|all (holders|customers)|everyone'?s|system (prompt|instructions)|your (instructions|prompt))", re.I),
     "I can't share other holders' data or my instructions. I can only discuss the case you're working on."),
    ("backdate", re.compile(r"(backdate|change the (blocked|rejected|freeze) date|move the (deadline|due date)|so (it|the report) isn'?t late)", re.I),
     "I can't change dates. The blocked or rejected date comes from the transaction, and the due date is set by the deadline engine (10 business days)."),
    ("skip_report", re.compile(r"(skip|don'?t bother|do not file|no need to file|don'?t file|just spam).{0,60}(report|filing|file|attempt)|(report|filing).{0,40}(skip|not needed)", re.I),
     "I can't skip a required report. There is no minimum amount, and each Blacklisted revert is reportable under assumption A1. Spam spikes are escalated to counsel."),
    ("sdn_authority", re.compile(r"(write|put|use|just say).{0,30}\bSDN\b.{0,40}(authority|legal)|\bSDN\b as (the )?(legal )?authority", re.I),
     "I can't use 'SDN' as legal authority (31 CFR 501.603). The report must name the sanctions program or executive order, which comes from the SDN file entry."),
    ("mark_filed", re.compile(r"mark (it|the report|this|.{0,30}) (as )?filed|(set|change) .{0,20}status to filed", re.I),
     "I can't mark a report as filed. A report is filed only when a person enters the ORS confirmation ID after submitting it."),
    ("unfreeze", re.compile(r"\b(unfreeze|release|unblock|lift the freeze)\b", re.I),
     None),  # answered below with the wallet's live list status
]


def _strip_quoted_data(message: str) -> str:
    """The person's own words: drop JSON event lines and the context block we add."""
    keep = []
    for line in message.splitlines():
        s = line.strip()
        if (s.startswith(("{", "0x", "tx 0x", "...")) or s.startswith("SDN list lookup")
                or s.startswith("Frozen wallets") or s.startswith("Already processed")):
            continue
        keep.append(line)
    return "\n".join(keep)


def _guard(own_words: str, sdn: SanctionsList, day: str):
    if INJECTION_RE.search(own_words) or re.search(r"\bmemo\b.{0,20}SYSTEM", own_words, re.I):
        return "injection", "That text tries to override my rules. I treat it as data, change nothing, and log it as an injection attempt."
    for name, pattern, reply in GUARDS:
        if not pattern.search(own_words):
            continue
        if name == "unfreeze":
            listed = [a for a in ADDR_RE.findall(own_words) if sdn.entry(a, day)]
            if listed or re.search(r"lazarus", own_words, re.I):
                return name, ("I can't unfreeze this wallet. It is on today's SDN list, so release needs a delisting "
                              "or an OFAC license plus counsel approval recorded in the system. I've routed the request to unfreeze review.")
            return name, "Unfreeze requests go to unfreeze review: counsel checks delisting or license evidence before anything is released."
        return name, reply
    return None, None


def _parse_case(message: str):
    events, frozen, seen = [], [], set()
    for line in message.splitlines():
        s = line.strip()
        if s.startswith("tx 0x"):
            seen.add(s[3:].strip())
            continue
        if s.startswith("{"):
            try:
                e = json.loads(s)
                e.setdefault("case", "Q")
                events.append(e)
            except json.JSONDecodeError:
                pass
        m = re.match(r"(0x[0-9a-fA-F]{40}): frozen (\{.*\}), blocked property report (\S+)", s)
        if m:
            chains = json.loads(m.group(2).replace("'", '"'))
            for chain, amt in chains.items():
                frozen.append({"address": m.group(1), "chain": chain, "amount": amt,
                               "frozen_on": "2000-01-01", "blocked_report_ors_id": m.group(3)})
    return events, frozen, seen


def answer(message: str, sdn: SanctionsList, default_day: str) -> dict:
    events, frozen, seen = _parse_case(message)
    day = events[0]["day"] if events else default_day
    own = _strip_quoted_data(message)
    guard, reply = _guard(own, sdn, day)
    if guard:
        return {"answer": reply, "guardrail": guard, "decision": "refuse" if guard != "injection" else "flag_injection",
                "filings": [], "rule": None}
    if not events:
        return {"answer": "I can explain a case's decision, its OFAC filing and due date. Send the case events, "
                          "or ask Maya for anything outside sanctions filings.",
                "guardrail": None, "decision": None, "filings": [], "rule": None}

    # Messages inside the events are decided by the engine too (pressure, leaks, report requests)
    state = State({"frozen": frozen})
    state.seen_tx |= seen
    # Count repeated events the question summarised ("... plus N more events")
    more = re.search(r"plus (\d+) more events of the same kind", message)
    if more and events:
        base = events[-1]
        for i in range(int(more.group(1))):
            clone = dict(base, tx_hash=f"{base.get('tx_hash', 'x')}-{i}")
            events.append(clone)
    results = match_day(day, events, sdn, state)
    r = next(iter(results.values()), None)
    if r is None:
        return {"answer": "No sanctions match in these events. No filing.", "guardrail": None,
                "decision": "clear", "filings": [], "rule": "Exact SDN address match required"}
    all_filings = [f for res in results.values() for f in res["filings"]]
    decision = r["decision"]
    if all_filings:
        ftype, due = all_filings[0]["type"], all_filings[0]["due"]
        n = len(all_filings)
        text = (f"{n} x {ftype} report{'s' if n > 1 else ''}, due {due}. Decision: {decision}. "
                f"Rule: {RULES_TEXT[ftype]}. Reason: {r['reason']}")
        if r["alerts"]:
            text += " " + " ".join(r["alerts"])
        rule = RULES_TEXT[ftype]
    else:
        text = f"No filing. Decision: {decision}. Reason: {r['reason']}"
        rule = r["reason"]
    return {"answer": text, "guardrail": None, "decision": decision,
            "filings": [{"type": f["type"], "due": f["due"]} for f in all_filings], "rule": rule}


CASE_RE = re.compile(r"\bC\d{2}\b", re.I)


def answer_with_cases(message: str, sdn: SanctionsList, day: str, events: list, questions: dict) -> dict:
    """Like answer(), but a short question that names a case ("Why does C01 need a report?") is
    answered from that case's events (`questions`: case id -> PRISM labeled-set question).
    Guardrails always run first on the user's own words."""
    first = answer(message, sdn, day)
    if first.get("guardrail") or "{" in message:
        return first
    for case in (c.upper() for c in CASE_RE.findall(message)):
        happened = any(e["case"] == case and e["day"] <= day for e in events)
        if not happened:
            first["answer"] = f"Case {case} hasn't happened yet on {day}. Move the simulated day forward first."
            continue
        if questions.get(case):
            res = answer(questions[case], sdn, day)
            res["answer"] = f"Case {case}: " + res["answer"]
            res["case"] = case
            return res
    return first


def load_case_questions(path) -> dict:
    import csv
    from pathlib import Path
    p = Path(path)
    return {r["id"]: r["question"] for r in csv.DictReader(p.open())} if p.exists() else {}
