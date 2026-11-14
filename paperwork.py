"""
paperwork.py - turn an account's paperwork pack into the same shape as a
book.json account, so the app can show accounts that exist only as paperwork.

A pack (data/paperwork_<id>.json, from get_all_paperwork) looks like:
  {"account": "K", "records": [{"path": "profile.json", "kind": "profile",
                                "data": {...}} or {"text": "..."}, ...]}

Every value we pull out keeps a citable id built from the record's path,
e.g. "K/invoices.json#INV-26-0921" or "K/profile.json#renewal", so the brief
can cite exactly which record a number came from. Nothing here is specific to
one account: it only reads the standard records (profile, deal history,
invoices). Anything a pack doesn't contain is simply left out.
"""

import json
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def load_packs():
    """All saved paperwork packs, keyed by account id."""
    packs = {}
    for path in sorted(DATA_DIR.glob("paperwork_*.json")):
        try:
            pack = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(pack, dict) and pack.get("account"):
            packs[pack["account"]] = pack
    return packs


def record_id(account, path, key=None):
    """Citable id for a paperwork record, or for one item inside it."""
    return f"{account}/{path}" + (f"#{key}" if key else "")


def records(pack):
    """(id, record) for every record in a pack, for the evidence section."""
    out = []
    for r in pack.get("records", []):
        body = r.get("text")
        if body is None and r.get("data") is not None:
            body = json.dumps(r["data"], indent=2)
        out.append((record_id(pack["account"], r["path"]),
                    {"path": r["path"], "kind": r.get("kind"), "title": r["path"], "text": body or ""}))
    return out


def _data(pack, path):
    rec = next((r for r in pack.get("records", []) if r.get("path") == path), None)
    return (rec or {}).get("data") or {}


def _long_date(iso):
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso
    return f"{d.day} {d.strftime('%B %Y')}"


def account_from_pack(pack):
    """Build a book.json-shaped account from a paperwork pack."""
    acct_id = pack["account"]
    profile = _data(pack, "profile.json")
    deals = _data(pack, "deal_history.json")
    invoices = _data(pack, "invoices.json")

    # People: only contacts the paperwork names. Their relationship to us is
    # not in the paperwork, so it is "not on file".
    stakeholders = [
        {"id": c["id"], "name": c["name"], "role": c.get("role", ""),
         "relationship": "not on file", "available": None,
         "src": record_id(acct_id, "profile.json")}
        for c in profile.get("contacts_on_file", []) if c.get("id") and c.get("name")
    ]

    # Money already agreed: each invoice is its own row.
    offers = [
        {"id": record_id(acct_id, "invoices.json", inv["number"]),
         "title": f"{inv.get('what', 'Invoice')} ({inv['number']})",
         "value": inv.get("amount"), "pitch_to": "", "status": str(inv.get("status", "")).lower(),
         "note": inv.get("note") or ""}
        for inv in invoices.get("invoices", []) if inv.get("number")
    ]

    # Money not agreed yet: what was open at handover, and the renewal.
    opportunities, deadlines = [], []
    for i, item in enumerate(deals.get("on_file_as_open_at_handover", [])):
        opportunities.append({
            "id": record_id(acct_id, "deal_history.json", f"on_file_as_open_at_handover[{i}]"),
            "title": item.get("what", "Open item"), "stage": "open at handover",
            "worth_up_to": item.get("value"), "blocker": None, "basis": item.get("basis"),
            "evidence": [], "deadline": None,
        })
    renewal = profile.get("renewal") or {}
    if renewal.get("term_ends"):
        deadline = {"text": f"The term ends on {_long_date(renewal['term_ends'])}.",
                    "passed": date.fromisoformat(renewal["term_ends"]) < date.today()
                    if len(renewal["term_ends"]) == 10 else False,
                    "moves_left": None}
        opportunities.append({
            "id": record_id(acct_id, "profile.json", "renewal"),
            "title": "Renewal when the term ends", "stage": "not agreed",
            "worth_up_to": renewal.get("annual_value"), "blocker": None,
            "basis": "Not agreed. The annual value on file if it renews.",
            "evidence": [], "deadline": deadline,
        })
        deadlines.append(deadline)

    story = " ".join(s for s in (
        (profile.get("sector", "") + ".") if profile.get("sector") else "",
        profile.get("what_they_bought", ""),
    ) if s)

    return {
        "id": acct_id,
        "name": profile.get("name", acct_id),
        "official_story": story,
        "currency": profile.get("currency") or deals.get("currency") or invoices.get("currency"),
        "source": "paperwork",
        "stakeholders": stakeholders,
        "facts": [],
        "opportunities": opportunities,
        "offers": offers,
        "deadlines": deadlines,
    }
