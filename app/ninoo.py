"""Step 9: Ninoo review queue hand-off.

`review_item` builds the payload Maya sees in Ninoo. `push` is where the Ninoo
SDK/API call goes (see Ninoo docs). A filing only becomes "filed" through
`record_ors_id`, which needs a confirmation ID typed by a human.
"""


def review_item(filing: dict, draft: dict, problems: list, drafter: str) -> dict:
    return {
        "item_id": f"{filing['case']}-{filing['type']}-{filing['facts'].get('tx_hash', filing['facts'].get('wallet', filing['facts'].get('order_id', filing['facts'].get('account_id'))))}"[:120],
        "title": f"{filing['type'].replace('_', ' ').title()} for {filing['case']}, due {filing['due']}",
        "case": filing["case"],
        "filing_type": filing["type"],
        "due": filing["due"],
        "explanation": draft.get("explanation") if draft else None,
        "rule": draft.get("rule_status") if draft else None,
        "draft_fields": draft.get("fields") if draft else None,
        "validator": "passed" if not problems else "needs human fix",
        "problems": problems,
        "drafted_by": drafter,
        "actions": ["approve", "edit", "reject"],
        "status": "awaiting_review",
        "ors_confirmation_id": None,
        "simulated": True,
    }


def push(items: list):
    # TODO: send items to Ninoo with its SDK/API (see Ninoo docs).
    return len(items)


def record_ors_id(item: dict, ors_id: str, entered_by: str) -> dict:
    if not ors_id or not ors_id.strip():
        raise ValueError("A filing is only 'filed' when a human enters the ORS confirmation ID.")
    if item["status"] != "approved":
        raise ValueError("Approve the draft in Ninoo before recording an ORS ID.")
    item.update({"status": "filed", "ors_confirmation_id": ors_id.strip(), "filed_by": entered_by})
    return item
