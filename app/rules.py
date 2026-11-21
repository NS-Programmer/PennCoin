"""Steps 4-6: event matcher, deadline engine and validator (plain code, no AI).

Rules decide; the AI only drafts and explains. Nothing here calls a model.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Step 5: deadline engine
# ---------------------------------------------------------------------------
# US federal holidays (observed dates). Update yearly.
FED_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-10-12", "2026-11-11", "2026-11-26",
    "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-10-11", "2027-11-11", "2027-11-25",
    "2027-12-24", "2027-12-31",
}


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in FED_HOLIDAYS


def due_date(action_day: str, n: int = 10) -> str:
    """10 business days after the action; day 1 = the next business day."""
    d, count = date.fromisoformat(action_day), 0
    while count < n:
        d += timedelta(days=1)
        if is_business_day(d):
            count += 1
    return d.isoformat()


# ---------------------------------------------------------------------------
# Sanctions list with simulated overlays
# ---------------------------------------------------------------------------
# Comprehensive-sanctions jurisdictions for primary-market checks.
# VERIFY against current OFAC programs before relying on this list.
JURISDICTIONS = {
    "Iran": "Iranian Transactions and Sanctions Regulations, 31 CFR part 560",
    "Cuba": "Cuban Assets Control Regulations, 31 CFR part 515",
    "North Korea": "North Korea Sanctions Regulations, 31 CFR part 510",
}


class SanctionsList:
    def __init__(self, data_dir: Path):
        wallets = json.loads((data_dir / "sdn_wallets.json").read_text())
        self.by_addr = {}
        for w in wallets:
            self.by_addr.setdefault(w["address_lower"], w)
        self.version = json.loads((data_dir / "list_version.json").read_text())
        ov = json.loads((data_dir / "list_overlays.json").read_text())
        self.not_before = {a.lower(): d for d, addrs in ov["not_yet_listed_before"].items() for a in addrs}
        self.removed_on = {a.lower(): d for d, addrs in ov["removed_on"].items() for a in addrs}

    def entry(self, address: str, day: str):
        a = address.lower()
        if a not in self.by_addr:
            return None  # exact match only: lookalikes never match
        if a in self.not_before and day < self.not_before[a]:
            return None
        if a in self.removed_on and day >= self.removed_on[a]:
            return None
        return self.by_addr[a]

    def newly_listed(self, day: str):
        return [a for a, d in self.not_before.items() if d == day]

    def newly_removed(self, day: str):
        return [a for a, d in self.removed_on.items() if d == day]


def authority_of(entry) -> str:
    return "; ".join(entry["legal_authorities"]) + f" (programs: {', '.join(entry['programs'])})"


# ---------------------------------------------------------------------------
# Step 4: event matcher
# ---------------------------------------------------------------------------
INJECTION_RE = re.compile(
    r"(ignore (all |any )?(prior|previous) (rules|instructions)|^\s*system\s*:|do not file|mark (all )?(alerts )?as clear)",
    re.I | re.M,
)
UNFREEZE_RE = re.compile(r"\b(unfreeze|release|unblock)\b", re.I)
DATA_LEAK_RE = re.compile(r"(other (holders|customers)|system (instructions|prompt)|your instructions)", re.I)
REPORT_REQ_RE = re.compile(r"blocked property report", re.I)
ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
VOLUME_ALERT_THRESHOLD = 10  # sanctions rejections per wallet per day


class State:
    def __init__(self, initial: dict):
        self.frozen = {}  # address_lower -> {chain: amount}
        self.reported = {}  # address_lower -> ORS id of blocked property report
        for f in initial["frozen"]:
            a = f["address"].lower()
            self.frozen.setdefault(a, {})[f["chain"]] = f["amount"]
            self.reported[a] = f["blocked_report_ors_id"]
        self.seen_tx = set()
        self.audit = []

    def log(self, **kw):
        self.audit.append(kw)


def _filing(ftype, case, action_day, facts):
    return {"type": ftype, "case": case, "action_day": action_day,
            "due": due_date(action_day), "facts": facts, "status": "needs_draft"}


def match_day(day: str, events: list, sdn: SanctionsList, state: State):
    """Return {case_id: {"decision": ..., "filings": [...], "reason": ..., "alerts": [...]}}."""
    out = {}

    def result(case, decision, reason, filings=None, alerts=None):
        r = out.setdefault(case, {"decision": decision, "reason": reason, "filings": [], "alerts": []})
        r["decision"], r["reason"] = decision, reason
        r["filings"] += filings or []
        r["alerts"] += alerts or []
        return r

    todays = [e for e in events if e["day"] == day]

    # Freezes: newly listed wallets with a balance -> freeze needed; AddedBlacklist = executed freeze
    snapshots = {}
    for e in todays:
        if e["type"] == "balance_snapshot":
            snapshots.setdefault((e["case"], e["address"].lower()), []).append(e)
    for (case, addr), snaps in snapshots.items():
        entry = sdn.entry(addr, day)
        if entry is None:
            continue
        held = [s for s in snaps if s["amount"] > 0]
        if not held:
            result(case, "no_action", "Newly listed wallet holds no eUSD on any chain.")
            continue
        freezes = [e for e in todays if e["type"] == "AddedBlacklist" and e["address"].lower() == addr]
        if not freezes:
            result(case, "freeze_needed", "Listed wallet holds eUSD; awaiting human approval to freeze.")
            continue
        holdings = []
        for s in held:
            fz = next((f for f in freezes if f["chain"] == s["chain"]), None)
            holdings.append({"chain": s["chain"], "address": s["address"], "amount": s["amount"],
                             "freeze_tx": fz and fz["tx_hash"], "frozen_at": fz and fz["ts"]})
            state.frozen.setdefault(addr, {})[s["chain"]] = s["amount"]
        if addr in state.reported:
            result(case, "freeze", "Already reported; no second blocked property report (D1).")
            continue
        action_day = min(f["ts"] for f in freezes)[:10]  # earliest freeze starts the clock
        facts = {"wallet": entry["address"], "holdings": holdings,
                 "usd_value": sum(h["amount"] for h in holdings),
                 "target": {k: entry[k] for k in ("entity_name", "entity_id", "entity_type", "programs", "countries")},
                 "legal_authority": authority_of(entry), "list_version": sdn.version["sha256"],
                 "approved_by": freezes[0].get("approved_by")}
        state.reported[addr] = "PENDING"
        result(case, "freeze", "Exact SDN match holding eUSD; frozen on all chains.",
               [_filing("blocked_property", case, action_day, facts)])

    # Failed transfers (rejection monitor, assumption A1)
    per_wallet_rejections = {}
    for e in todays:
        if e["type"] != "transfer_failed":
            continue
        if e["tx_hash"] in state.seen_tx:
            result(e["case"], "duplicate", "Same tx hash already processed; not filed twice.")
            continue
        state.seen_tx.add(e["tx_hash"])
        parties = {e["sender"].lower(), e["from"].lower(), e["to"].lower()}
        frozen_party = next((p for p in parties if p in state.frozen), None)
        if e.get("revert_reason") != "Blacklisted":
            result(e["case"], "log_only", f"Failed for '{e.get('revert_reason')}', not a sanctions rejection.")
            continue
        if frozen_party is None:
            result(e["case"], "log_only", "Blacklisted revert but no frozen party found; flag for review.")
            continue
        entry = sdn.entry(frozen_party, day) or sdn.by_addr.get(frozen_party)
        facts = {"chain": e["chain"], "call": e["call"], "tx_hash": e["tx_hash"],
                 "sender": e["sender"], "from": e["from"], "to": e["to"], "amount": e["amount"],
                 "usd_value": e["amount"], "rejected_at": e["ts"], "revert_reason": "Blacklisted",
                 "blacklisted_party": entry["address"],
                 "target": {k: entry[k] for k in ("entity_name", "entity_id", "programs", "countries")},
                 "legal_authority": authority_of(entry),
                 "original_report_ors_id": state.reported.get(frozen_party)}
        per_wallet_rejections[frozen_party] = per_wallet_rejections.get(frozen_party, 0) + 1
        result(e["case"], "reject_logged", "Transfer reverted by the blacklist (A1: reportable).",
               [_filing("rejected_transaction", e["case"], day, facts)])
    for wallet, n in per_wallet_rejections.items():
        if n > VOLUME_ALERT_THRESHOLD:
            for case, r in out.items():
                if r["filings"] and r["filings"][0]["facts"].get("blacklisted_party", "").lower() == wallet:
                    r["alerts"].append(f"Volume alert: {n} rejected attempts from one wallet today; escalate to counsel.")
                    break

    for e in todays:
        t = e["type"]
        if t == "redemption_request":
            country = e["destination_country"]
            if country in JURISDICTIONS:
                facts = {"account_id": e["account_id"], "amount": e["amount"], "usd_value": e["amount"],
                         "destination_country": country, "destination_bank": e["destination_bank"],
                         "rejected_at": e["ts"], "legal_authority": JURISDICTIONS[country]}
                result(e["case"], "reject", f"Redemption to {country} (comprehensive sanctions).",
                       [_filing("rejected_transaction", e["case"], day, facts)])
            else:
                result(e["case"], "allow", "Destination not comprehensively sanctioned.")
        elif t == "transfer":
            if e.get("memo") and INJECTION_RE.search(e["memo"]):
                result(e["case"], "flag_injection", "Memo contains instructions; treated as data and logged.")
                continue
            hits = [p for p in (e["from"], e["to"]) if sdn.entry(p, day)]
            if hits:
                result(e["case"], "freeze_needed", "Successful transfer touching a listed wallet.")
            elif e.get("exposure_note"):
                result(e["case"], "clear_watchlist", "Indirect (multi-hop) exposure only; no exact match.")
            else:
                result(e["case"], "clear", "No exact match to the SDN list (lookalikes never match).")
        elif t == "court_order":
            facts = {k: e[k] for k in ("order_id", "issuer", "action", "address", "chain", "amount", "deadline")}
            a = e["address"].lower()
            if a in state.frozen and e["action"] in ("transfer", "seize", "burn"):
                ent = sdn.by_addr.get(a, {})
                facts.update({"usd_value": e["amount"], "to": e.get("to"),
                              "target": {k: ent.get(k) for k in ("entity_name", "entity_id", "programs", "countries")},
                              "original_report_ors_id": state.reported.get(a),
                              "legal_authority": f"Court order {e['order_id']} ({e['issuer']})"})
                state.frozen[a][e["chain"]] = max(0, state.frozen[a].get(e["chain"], 0) - e["amount"])
                result(e["case"], "court_order_log", "Order moves blocked property; unblock/transfer report due.",
                       [_filing("unblock_transfer", e["case"], day, facts)])
            else:
                result(e["case"], "court_order_log", "Not blocked property; log the order, no OFAC filing.")
        elif t == "message":
            text = e["text"]
            if DATA_LEAK_RE.search(text):
                result(e["case"], "refuse", "Request for other holders' data or system instructions refused.")
            elif REPORT_REQ_RE.search(text):
                addrs = [a.lower() for a in ADDR_RE.findall(text)]
                if any(a in state.reported for a in addrs):
                    result(e["case"], "refuse_duplicate_report", "Wallet already has a blocked property report (D1).")
                else:
                    result(e["case"], "needs_review", "Report request for an unreported wallet; route to Maya.")
            elif UNFREEZE_RE.search(text):
                addrs = [a for a in ADDR_RE.findall(text) if sdn.entry(a, day)]
                result(e["case"], "refuse", "Unfreeze refused: wallet is still on today's SDN list; "
                       "requires delisting or an OFAC license plus counsel approval." if addrs else
                       "Unfreeze request routed to unfreeze review.")
            else:
                result(e["case"], "needs_review", "Message routed to Maya.")
        elif t == "list_update":
            for a in e.get("removed", []):
                if a.lower() in state.frozen:
                    result(e["case"], "unfreeze_review",
                           "Removed from SDN list: counsel reviews release; no OFAC unblock report (delisting exempt).")
    return out


# ---------------------------------------------------------------------------
# Step 6: validator
# ---------------------------------------------------------------------------
REQUIRED = {
    "blocked_property": ["holder", "transaction_description", "target", "property", "date_blocked",
                         "usd_value", "action_taken", "legal_authority", "documents"],
    "rejected_transaction": ["holder", "transaction_description", "target", "date_rejected",
                             "usd_value", "legal_authority", "documents"],
    "unblock_transfer": ["holder", "target", "property", "date_unblocked", "usd_value",
                         "legal_authority", "original_report"],
}
SDN_AS_AUTHORITY = re.compile(r"\bSDN\b")


def allowed_addresses(filing) -> set:
    found = set()

    def walk(x):
        if isinstance(x, str):
            found.update(a.lower() for a in ADDR_RE.findall(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(filing["facts"])
    return found


def validate(filing: dict, draft: dict, issuer: dict, reported_wallets: dict) -> list:
    """Return a list of problems; empty list = valid."""
    problems = []
    if draft.get("filing_type") != filing["type"]:
        problems.append(f"filing_type '{draft.get('filing_type')}' disagrees with rules '{filing['type']}'")
    fields = draft.get("fields", {})
    for f in REQUIRED[filing["type"]]:
        if fields.get(f) in (None, "", []):
            problems.append(f"missing required field: {f}")
    if SDN_AS_AUTHORITY.search(str(fields.get("legal_authority", ""))):
        problems.append("legal authority may not be 'SDN'; cite the program, EO or CFR part")
    try:
        if float(fields.get("usd_value")) < 0:
            problems.append("usd_value is negative")
    except (TypeError, ValueError):
        problems.append("usd_value must be a number")
    if draft.get("due") != filing["due"]:
        problems.append(f"due date {draft.get('due')} does not match deadline engine {filing['due']}")
    allowed = allowed_addresses(filing) | {issuer.get("token_ethereum", "").lower(), issuer.get("token_polygon", "").lower()}
    leaked = {a.lower() for a in ADDR_RE.findall(json.dumps(draft))} - allowed
    if leaked:
        problems.append(f"addresses not in this case: {sorted(leaked)}")
    if filing["type"] == "blocked_property":
        w = filing["facts"]["wallet"].lower()
        if reported_wallets.get(w) not in (None, "PENDING"):
            problems.append("wallet already has a blocked property report (D1)")
    if filing["type"] == "unblock_transfer" and not fields.get("original_report"):
        problems.append("unblock/transfer report must cite the original report's ORS ID")
    return problems
