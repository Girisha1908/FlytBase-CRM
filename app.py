"""
app.py - the FlytBase CRM web app.

Rules this file follows:
  * Page loads NEVER call the MCP server. They read data/book.json (written
    once by connect.py), data/signals.json and data/outbox.json.
  * ONE background thread makes every MCP call, at most one per minute:
      - if a note is waiting in data/outbox.json, it sends that (write_note)
      - otherwise it calls get_signals and appends new events to signals.json
    So clicking "Save next steps" never adds a second call in the same minute.

Run it with:   python app.py      then open http://127.0.0.1:5000
"""

import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from briefs import BRIEFS
from mcp_client import RateLimited, call_tool, write_note

DATA_DIR = Path(__file__).parent / "data"
BOOK_FILE = DATA_DIR / "book.json"
SIGNALS_FILE = DATA_DIR / "signals.json"
OUTBOX_FILE = DATA_DIR / "outbox.json"
POLL_SECONDS = 61  # one call per minute per token, plus a second of margin

app = Flask(__name__)

# One lock guards every file under data/ that the worker and pages both touch.
data_lock = threading.Lock()

# Worker status, shown on the pages (never includes the token).
worker_status = {"last_check": None, "last_error": None, "new_last_check": 0}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# =========================================================================== #
# Local files                                                                 #
# =========================================================================== #

def read_json(path, default):
    """Return the contents of a JSON file, or `default` if it doesn't exist."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json_atomic(path, data):
    """Write to a temp file, then rename over the real one (never half-written)."""
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_book():
    book = read_json(BOOK_FILE, None)
    if book is None:
        abort(503, "data/book.json is missing. Run: python connect.py")
    return book


def signal_time(s):
    """Best timestamp for sorting a signal, newest first."""
    return s.get("set_off_at") or s.get("_received_at") or s.get("dated") or ""


def load_signals(account=None):
    """All stored signals, newest first, optionally for one account."""
    with data_lock:
        signals = read_json(SIGNALS_FILE, [])
    signals = [s for s in signals if isinstance(s, dict)]
    if account:
        signals = [s for s in signals if s.get("account") == account]
    return sorted(signals, key=signal_time, reverse=True)


def load_outbox(account=None):
    with data_lock:
        notes = read_json(OUTBOX_FILE, [])
    return [n for n in notes if account is None or n["account"] == account]


# =========================================================================== #
# Source ids: everything the brief cites must exist in book.json/signals.json #
# =========================================================================== #

def build_index(book, signals):
    """
    Map every citable id to (kind, record). Kinds: deal, offer, fact, file,
    person (stakeholder), signal. Used to render citation chips and to check
    that briefs.py never cites an id that doesn't exist.
    """
    index = {}
    for a in book["accounts"]:
        for o in a["opportunities"]:
            index[o["id"]] = ("deal", o)
        for o in a["offers"]:
            index[o["id"]] = ("offer", o)
        for f in a["facts"]:
            index[f["id"]] = ("fact", f)
        for s in a["stakeholders"]:
            index[s["id"]] = ("person", s)
    for f in book.get("files", []):
        index[f["id"]] = ("file", f)
    for s in signals:
        if "id" in s:
            index[s["id"]] = ("signal", s)
    return index


def cited_ids(brief):
    """Every id briefs.py cites for one account (srcs, people, deals)."""
    ids = []
    for c in brief.get("story_claims", []):
        ids += [c["claim"], c["counter"]]
    for deal_id, roles in brief.get("power_map", {}).items():
        ids.append(deal_id)
        for cell in roles.values():
            ids += cell["people"] + cell["src"]
    for m in brief.get("moves", []):
        ids += m["src"] + [m["deal"]]
        ids += m["when"].get("src", [])
        if "deadline" in m["when"]:
            ids.append(m["when"]["deadline"])
    for g in brief.get("gaps", []):
        ids += g["src"]
        for w in g["who"]:
            ids += [w["id"]] if "id" in w else w.get("src", [])
    return ids


def check_brief(brief, index):
    """Return the cited ids that don't exist, so the page can flag them."""
    return sorted({i for i in cited_ids(brief) if i not in index})


# =========================================================================== #
# Computed parts of the brief                                                 #
# =========================================================================== #

BACKED_STATUSES = {"won", "agreed"}


def book_worth(acct):
    """
    One row per deal and offer.
      backed     = value of anything won/agreed (cites its id)
      not_backed = "worth up to" / open offer value, with the reason
      Declined offers are listed but count toward neither total.
    """
    rows = []
    for o in acct["opportunities"]:
        value = o["worth_up_to"]
        blocker = o.get("blocker") or {}
        row = {"id": o["id"], "kind": "Deal", "title": o["title"], "stage": o["stage"],
               "backed": None, "not_backed": None, "reason": "", "blocker_open": False,
               "declined_value": None}
        if o["stage"] in BACKED_STATUSES:
            row["backed"] = value
        else:
            row["not_backed"] = value
            if blocker.get("status") == "open":
                row["reason"], row["blocker_open"] = blocker["text"], True
            elif blocker.get("status") == "cleared":
                row["reason"] = f"Blocker cleared ({blocker['text'].rstrip('.')}), but not won yet."
            else:
                row["reason"] = f"No blocker recorded. Stage: {o['stage']}."
            if value is None:
                row["reason"] = "Not sized in the book. " + row["reason"]
        rows.append(row)

    for x in acct["offers"]:
        row = {"id": x["id"], "kind": "Offer", "title": x["title"], "stage": x["status"],
               "backed": None, "not_backed": None, "reason": "", "blocker_open": False,
               "declined_value": None}
        if x["status"] in BACKED_STATUSES:
            row["backed"] = x["value"]
            row["reason"] = f"{x['status'].capitalize()} with {x['pitch_to']}."
        elif x["status"] == "declined":
            row["declined_value"] = x["value"]
            row["reason"] = f"Declined by {x['pitch_to']}. Not counted."
        else:
            row["not_backed"] = x["value"]
            row["reason"] = f"Offer {x['status']} with {x['pitch_to']}, not agreed."
        rows.append(row)

    backed_ids = [r["id"] for r in rows if r["backed"] is not None]
    nb_ids = [r["id"] for r in rows if r["not_backed"] is not None]
    unsized = [r["id"] for r in rows if r["kind"] == "Deal" and r["backed"] is None and r["not_backed"] is None]
    return {
        "rows": rows,
        "backed_total": sum(r["backed"] for r in rows if r["backed"] is not None),
        "backed_ids": backed_ids,
        "not_backed_total": sum(r["not_backed"] for r in rows if r["not_backed"] is not None),
        "not_backed_ids": nb_ids,
        "unsized_ids": unsized,
    }


def deadline_name(text):
    """A deadline's name is the first sentence of its text in book.json."""
    return re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0].rstrip(".")


def risks(acct, signals):
    """What could go wrong, computed from book.json and live signals."""
    deadlines = []
    seen_texts = set()
    for o in acct["opportunities"]:
        d = o.get("deadline")
        if d and not d.get("passed"):
            deadlines.append({"name": deadline_name(d["text"]), "text": d["text"],
                              "moves_left": d.get("moves_left"), "deal": o["id"]})
            seen_texts.add(d["text"])
    for d in acct["deadlines"]:
        if not d.get("passed") and d["text"] not in seen_texts:
            deadlines.append({"name": deadline_name(d["text"]), "text": d["text"],
                              "moves_left": None, "deal": None})

    return {
        "deadlines": deadlines,
        "people": [s for s in acct["stakeholders"] if s["relationship"] in ("neutral", "guarded")],
        "blockers": [o for o in acct["opportunities"]
                     if (o.get("blocker") or {}).get("status") == "open"],
        "declined": [x for x in acct["offers"] if x["status"] == "declined"],
        "signals": signals,
    }


def story_segments(acct, brief, index):
    """
    Split official_story into plain text and claim segments, so phrases that
    are only someone's unverified claim are never shown as fact.
    """
    segments = [{"text": acct["official_story"]}]
    for c in brief.get("story_claims", []):
        out = []
        for seg in segments:
            if "claim" in seg or c["phrase"] not in seg["text"]:
                out.append(seg)
                continue
            before, after = seg["text"].split(c["phrase"], 1)
            claim = index.get(c["claim"], (None, {}))[1]
            out += [{"text": before},
                    {"text": c["phrase"], "claim": c["claim"], "counter": c["counter"],
                     "said_by": claim.get("said_by", "unknown")},
                    {"text": after}]
        segments = out
    return [s for s in segments if s["text"].strip() or "claim" in s]


def plain_summary(segments):
    """
    The home-page summary: the official story without its claim phrases, so an
    unverified claim is never shown as fact. (The brief page shows the claims.)
    Leftover fragments are tidied: "14 docks installed," -> "14 docks installed."
    """
    parts = []
    for s in segments:
        if "claim" in s:
            continue
        text = s["text"].strip().rstrip(",").strip()
        if text:
            parts.append(text if text[-1] in ".!?" else text + ".")
    return " ".join(parts)


def resolve_when(when, acct):
    """Turn a move's "when" into display text, using only book/signal data."""
    if "deadline" in when:
        opp = next((o for o in acct["opportunities"] if o["id"] == when["deadline"]), None)
        if opp and opp.get("deadline"):
            d = opp["deadline"]
            return {"text": f"Before: {deadline_name(d['text'])}",
                    "moves_left": d.get("moves_left"), "src": [opp["id"]]}
        return {"text": "Deadline not found in book", "src": [when["deadline"]], "moves_left": None}
    return {"text": when["text"], "src": when.get("src", []), "moves_left": None}


def next_steps_text(acct, brief):
    """The note body sent by "Save next steps": the three ranked moves."""
    lines = [f"Next steps for {acct['name']} ({acct['id']}), from the Monday brief:"]
    for n, m in enumerate(brief.get("moves", []), 1):
        when = resolve_when(m["when"], acct)
        lines.append(f"{n}. {m['do']}\n   Owner: {m['owner']}. When: {when['text']}. "
                     f"Deal: {m['deal']}. Sources: {', '.join(m['src'])}.")
    return "\n".join(lines)


# =========================================================================== #
# Background worker: the only code that calls the MCP server                  #
# =========================================================================== #

def event_key(event):
    """Stable identity for a signal: its "id", or a hash of its contents."""
    if isinstance(event, dict):
        if "id" in event:
            return f"id:{event['id']}"
        event = {k: v for k, v in event.items() if not k.startswith("_")}
    return "h:" + hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()


def extract_events(result):
    """get_signals may return a list or a dict wrapping a list; get the list."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for name in ("signals", "events", "items"):
            if isinstance(result.get(name), list):
                return result[name]
        lists = [v for v in result.values() if isinstance(v, list)]
        return lists[0] if lists else ([result] if result else [])
    return [result] if result else []


def poll_signals_once():
    """One get_signals call; append any events we haven't stored yet."""
    events = extract_events(call_tool("get_signals"))
    now = now_iso()
    with data_lock:
        stored = read_json(SIGNALS_FILE, [])
        seen = {event_key(e) for e in stored}
        new = []
        for e in events:
            if event_key(e) not in seen:
                seen.add(event_key(e))
                new.append({**e, "_received_at": now} if isinstance(e, dict) else e)
        if new:
            write_json_atomic(SIGNALS_FILE, stored + new)
    worker_status.update(last_check=now, last_error=None, new_last_check=len(new))
    return len(new)


def update_note(note_id, **changes):
    with data_lock:
        notes = read_json(OUTBOX_FILE, [])
        for n in notes:
            if n["id"] == note_id:
                n.update(changes)
        write_json_atomic(OUTBOX_FILE, notes)


def send_next_note():
    """If a note is queued, send it with write_note. Returns True if it tried."""
    queued = [n for n in load_outbox() if n["status"] == "queued"]
    if not queued:
        return False
    note = queued[0]
    try:
        result, args = write_note(note["account"], note["kind"], note["text"])
        update_note(note["id"], status="sent", sent_at=now_iso(), error=None,
                    sent_as=list(args), result=result)
        print(f"[notes] sent {note['kind']} for account {note['account']}")
    except RateLimited as e:
        update_note(note["id"], error=f"Rate limited, will retry: {e}")  # stays queued
        print("[notes] rate limited, will retry next minute")
    except Exception as e:
        update_note(note["id"], status="failed", error=str(e))
        print(f"[notes] failed: {e}")
    return True


def worker():
    """Forever: one MCP call (a queued note first, else get_signals), then wait."""
    while True:
        try:
            if not send_next_note():
                n = poll_signals_once()
                print(f"[signals] checked, {n} new event(s)")
        except RateLimited as e:
            worker_status["last_error"] = f"Rate limited: {e}"
            print("[signals] rate limited, will retry next minute")
        except Exception as e:  # keep the thread alive on any network error
            worker_status["last_error"] = str(e)
            print(f"[signals] error: {e}")
        time.sleep(POLL_SECONDS)


def start_worker():
    threading.Thread(target=worker, name="mcp-worker", daemon=True).start()


# =========================================================================== #
# Template helpers                                                            #
# =========================================================================== #

@app.template_filter("num")
def num(value):
    """Format an amount. book.json gives no currency, so we don't add one."""
    return "—" if value is None else f"{value:,.0f}"


@app.template_filter("money")
def money(value):
    """A money amount with a dollar sign, e.g. 350000 -> $350,000."""
    return "—" if value is None else f"${value:,.0f}"


@app.template_filter("when")
def fmt_time(iso):
    """'2026-11-14T09:30:38.755Z' -> '14 Nov 2026, 09:30 UTC'."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return dt.strftime("%d %b %Y, %H:%M UTC") if "T" in iso else dt.strftime("%d %b %Y")


# =========================================================================== #
# Pages (only read local files)                                               #
# =========================================================================== #

@app.route("/")
def index():
    book = load_book()
    signals = load_signals()
    idx = build_index(book, signals)
    cards = []
    for a in book["accounts"]:
        segments = story_segments(a, BRIEFS.get(a["id"], {}), idx)
        cards.append({
            "acct": a,
            # Same totals as the Monday brief's "Book worth" section.
            "worth": book_worth(a),
            "summary": plain_summary(segments),
            "claims": [s["claim"] for s in segments if "claim" in s],
        })
    return render_template("index.html", book=book, signal_count=len(signals), cards=cards, idx=idx)


@app.route("/account/<account_id>")
def account(account_id):
    book = load_book()
    acct = next((a for a in book["accounts"] if a["id"] == account_id), None)
    if acct is None:
        abort(404)
    signals = load_signals(account_id)
    idx = build_index(book, load_signals())
    brief = BRIEFS.get(account_id, {"power_map": {}, "moves": [], "gaps": [], "story_claims": []})
    moves = [{**m, "when_r": resolve_when(m["when"], acct)} for m in brief["moves"]]
    return render_template(
        "account.html", book=book, acct=acct, brief=brief, moves=moves, idx=idx,
        worth=book_worth(acct), risks=risks(acct, signals), signals=signals,
        story=story_segments(acct, brief, idx), missing=check_brief(brief, idx),
        files=[f for f in book.get("files", []) if f.get("account") == account_id],
        notes=list(reversed(load_outbox(account_id))), status=worker_status,
    )


@app.route("/account/<account_id>/next-steps", methods=["POST"])
def save_next_steps(account_id):
    """Queue a write_note(kind="next_step"). The worker sends it within a minute."""
    book = load_book()
    acct = next((a for a in book["accounts"] if a["id"] == account_id), None)
    if acct is None or account_id not in BRIEFS:
        abort(404)
    text = next_steps_text(acct, BRIEFS[account_id])
    with data_lock:
        notes = read_json(OUTBOX_FILE, [])
        # Don't queue the same text twice while one is still waiting.
        if not any(n["account"] == account_id and n["status"] == "queued" and n["text"] == text
                   for n in notes):
            notes.append({"id": uuid.uuid4().hex, "account": account_id, "kind": "next_step",
                          "text": text, "status": "queued", "created": now_iso(), "error": None})
            write_json_atomic(OUTBOX_FILE, notes)
    return redirect(url_for("account", account_id=account_id) + "#next-steps")


@app.route("/signals")
def signals_page():
    return render_template("signals.html", signals=load_signals(), status=worker_status)


@app.route("/api/signals")
def signals_api():
    """Local signals, newest first. Optional ?account=A. Never calls the MCP."""
    return jsonify(signals=load_signals(request.args.get("account")), status=worker_status)


@app.route("/api/notes/<account_id>")
def notes_api(account_id):
    notes = [{k: n.get(k) for k in ("id", "kind", "status", "created", "sent_at", "error")}
             for n in reversed(load_outbox(account_id))]
    return jsonify(notes=notes)


def warn_about_briefs():
    """Print any cited id that doesn't exist, at startup."""
    book = read_json(BOOK_FILE, None)
    if not book:
        return
    idx = build_index(book, read_json(SIGNALS_FILE, []))
    for acct_id, brief in BRIEFS.items():
        missing = check_brief(brief, idx)
        if missing:
            print(f"[briefs] account {acct_id} cites ids not in book/signals: {missing}")


if __name__ == "__main__":
    warn_about_briefs()
    start_worker()
    # use_reloader=False: the reloader runs this file twice, which would start
    # two workers and break the one-call-a-minute limit.
    app.run(debug=True, use_reloader=False)
