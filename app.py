"""
app.py - the FlytBase CRM web app.

Rules this file follows:
  * Page loads NEVER call the MCP server. They read local files only:
    data/book.json, data/paperwork_<id>.json, data/signals.json,
    data/outbox.json, and the hand-written briefs in briefs/<id>.json.
  * Every account in book.json, plus every account with a saved paperwork
    pack, gets a page automatically. There is no account-specific code here.
  * ONE background thread makes every MCP call, at most one per minute.
    Each minute it does the first of these that applies:
      1. send a note waiting in data/outbox.json (write_note)
      2. run a queued refresh job (get_my_accounts / get_all_paperwork)
      3. every WHOAMI_EVERY minutes: whoami, and if it names an account we
         don't have, queue a book refresh plus that account's paperwork
      4. otherwise get_signals, appending new events to signals.json
  * Fresh start (e.g. on Render, where data/ is empty because it's
    gitignored): the worker fetches its own data, one call a minute:
      minute 1  get_my_accounts  -> data/book.json   (only if it's missing)
      minute 2  whoami           -> every account it names that we can't
                                    show yet is queued
      minute 3+ get_all_paperwork for each of those -> data/paperwork_<id>.json
      then      get_signals every minute (whoami again every 10th minute)
    Until data/book.json exists, pages show "Loading data, refresh in a minute".
    The token comes from the AQ_TOKEN environment variable (.env locally).

Run it with:   python app.py      then open http://127.0.0.1:5000
"""

import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from mcp_client import RateLimited, call_mapped, call_tool, write_note
from paperwork import account_from_pack, load_packs, records as pack_records

DATA_DIR = Path(__file__).parent / "data"
BOOK_FILE = DATA_DIR / "book.json"
SIGNALS_FILE = DATA_DIR / "signals.json"
OUTBOX_FILE = DATA_DIR / "outbox.json"
ME_FILE = DATA_DIR / "me.json"
BRIEFS_DIR = Path(__file__).parent / "briefs"
POLL_SECONDS = 61  # one call per minute per token, plus a second of margin
WHOAMI_EVERY = 10  # check whoami for new accounts every 10th minute

EMPTY_BRIEF = {"story_claims": [], "power_map": {}, "moves": [], "gaps": [], "extra_risks": []}

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


class DataLoading(Exception):
    """data/book.json isn't there yet; the worker is fetching it."""


def load_book():
    book = read_json(BOOK_FILE, None)
    if book is None:
        raise DataLoading()
    return book


@app.errorhandler(DataLoading)
def show_loading(_):
    """While the first fetch runs, show a page that refreshes itself."""
    status = current_status()
    return render_template("loading.html", status=status, steps=status.get("steps", [])), 200


def load_accounts():
    """
    Every account to show: those in book.json, then any that exist only as a
    saved paperwork pack (built by paperwork.py). Returns (book, accounts, packs).
    """
    book = load_book()
    packs = load_packs()
    accounts = list(book["accounts"])
    known = {a["id"] for a in accounts}
    accounts += [account_from_pack(pk) for aid, pk in packs.items() if aid not in known]
    return book, accounts, packs


def find_account(accounts, account_id):
    acct = next((a for a in accounts if a["id"] == account_id), None)
    if acct is None:
        abort(404)
    return acct


def load_brief(account_id):
    """The hand-written parts of an account's brief (briefs/<id>.json), or an empty brief."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", account_id):
        return dict(EMPTY_BRIEF)
    return {**EMPTY_BRIEF, **read_json(BRIEFS_DIR / f"{account_id}.json", {})}


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

def build_index(accounts, files, signals, packs):
    """
    Map every citable id to (kind, record). Kinds: deal, offer, fact, file,
    person (stakeholder), signal, paper (a paperwork record path). Used to
    render source links and to check that briefs never cite an id that
    doesn't exist.
    """
    index = {}
    for pack in packs.values():
        for rid, rec in pack_records(pack):
            index[rid] = ("paper", rec)
    for a in accounts:
        for o in a["opportunities"]:
            index[o["id"]] = ("deal", o)
        for o in a["offers"]:
            index[o["id"]] = ("offer", o)
        for f in a["facts"]:
            index[f["id"]] = ("fact", f)
        for s in a["stakeholders"]:
            index[s["id"]] = ("person", s)
    for f in files:
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
    for r in brief.get("extra_risks", []):
        ids += r["src"]
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

BACKED_STATUSES = {"won", "agreed", "paid"}


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
            elif o.get("basis"):
                row["reason"] = o["basis"]
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
            row["reason"] = x.get("note") or f"{x['status'].capitalize()} with {x['pitch_to']}."
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


def risks(acct, signals, brief):
    """What could go wrong: computed from the account, live signals, and any
    extra sourced risks the brief lists."""
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
        "extra": brief.get("extra_risks", []),
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


# Refresh jobs waiting for a free minute: ("book",) or ("paperwork", account_id).
refresh_jobs = []
# Accounts whose paperwork fetch failed this run, so we don't retry every 10 min.
paperwork_failed = set()
# whoami runs as soon as the queue is empty after startup, then every 10 min.
whoami_state = {"due": True, "done_once": False}
# When this run last saved book.json (a whoami right after doesn't refetch it).
book_saved_at = [0.0]


def publish_status():
    """
    Share the worker's status with every process: gunicorn may run several,
    but only one runs the worker, so pages read this file instead of memory.
    """
    try:
        write_json_atomic(DATA_DIR / ".worker_status.json",
                          {**worker_status, "steps": loading_steps(), "pid": os.getpid()})
    except OSError as e:
        print(f"[worker] could not write status: {e}")


def current_status():
    """The worker's latest status, from whichever process runs it."""
    shared = read_json(DATA_DIR / ".worker_status.json", None)
    return shared if shared is not None else {**worker_status, "steps": loading_steps()}


def loading_steps():
    """What the worker still has to fetch, for the loading page and home page."""
    steps = []
    if not BOOK_FILE.exists() and ("book",) not in refresh_jobs:
        steps.append("The account book (get_my_accounts)")
    for job in refresh_jobs:
        steps.append("The account book (get_my_accounts)" if job[0] == "book"
                     else f"Paperwork for account {job[1]} (get_all_paperwork)")
    if not whoami_state["done_once"]:
        steps.append("New accounts (whoami)")
    return steps


def known_account_ids():
    """Accounts the app can already show: book.json plus saved paperwork."""
    book = read_json(BOOK_FILE, {}) or {}
    return {a["id"] for a in book.get("accounts", [])} | set(load_packs())


def check_whoami():
    """
    whoami (one call). If it names an account we can't show yet, queue a book
    refresh, then that account's paperwork, for the following minutes.
    """
    me = call_tool("whoami")
    with data_lock:
        write_json_atomic(ME_FILE, me)
    named = {a.get("id") for a in me.get("new_accounts", []) if isinstance(a, dict)}
    named |= {p.get("account") for p in me.get("pack", []) if isinstance(p, dict)}
    new = sorted(i for i in named - known_account_ids() if i and i not in paperwork_failed)
    queued = {j[1] for j in refresh_jobs if j[0] == "paperwork"}
    new = [i for i in new if i not in queued]
    if new:
        # Refresh the book too, unless this run saved it in the last 10 minutes
        # (on a fresh start it was fetched one minute ago).
        if time.time() - book_saved_at[0] > WHOAMI_EVERY * POLL_SECONDS and ("book",) not in refresh_jobs:
            refresh_jobs.append(("book",))
        refresh_jobs.extend(("paperwork", i) for i in new)
        print(f"[whoami] new account(s) {new}: queued {refresh_jobs}")
    else:
        print("[whoami] no new accounts")


def run_refresh_job():
    """Run the next queued refresh job (one call). Returns True if it ran one."""
    if not refresh_jobs:
        return False
    job = refresh_jobs[0]
    if job[0] == "book":
        book = call_tool("get_my_accounts")
        with data_lock:
            if BOOK_FILE.exists():
                write_json_atomic(DATA_DIR / "book.prev.json", read_json(BOOK_FILE, {}))
            write_json_atomic(BOOK_FILE, book)
        book_saved_at[0] = time.time()
        print("[refresh] saved data/book.json")
    else:
        account_id = job[1]
        try:
            pack, _ = call_mapped("get_all_paperwork", account=account_id)
        except RateLimited:
            raise  # stays queued for next minute
        except Exception as e:
            paperwork_failed.add(account_id)
            refresh_jobs.pop(0)
            raise RuntimeError(f"paperwork for {account_id} failed: {e}") from e
        if not (isinstance(pack, dict) and pack.get("account") == account_id):
            paperwork_failed.add(account_id)
            refresh_jobs.pop(0)
            raise RuntimeError(f"paperwork for {account_id} came back in an unexpected shape")
        with data_lock:
            write_json_atomic(DATA_DIR / f"paperwork_{account_id}.json", pack)
        print(f"[refresh] saved data/paperwork_{account_id}.json")
    refresh_jobs.pop(0)  # only after success; a rate limit leaves it queued
    return True


def worker():
    """
    Forever: one MCP call per minute, then wait 61s. Priority: a queued note,
    a queued fetch, whoami (right after startup, then every 10 minutes),
    else signals.
    """
    if not BOOK_FILE.exists():
        refresh_jobs.insert(0, ("book",))  # fresh start: fetch the book first
        print("[startup] data/book.json missing: fetching it first")
    publish_status()
    tick = 0
    while True:
        tick += 1
        try:
            if send_next_note() or run_refresh_job():
                pass
            elif whoami_state["due"]:
                check_whoami()  # if this is rate limited, it stays due
                whoami_state.update(due=False, done_once=True, next_tick=tick + WHOAMI_EVERY)
            else:
                if tick >= whoami_state.get("next_tick", 0) and whoami_state["done_once"]:
                    whoami_state["due"] = True  # whoami takes the next free minute
                n = poll_signals_once()
                print(f"[signals] checked, {n} new event(s)")
        except RateLimited as e:
            worker_status["last_error"] = f"Rate limited: {e}"
            print("[worker] rate limited, will retry next minute")
        except Exception as e:  # keep the thread alive on any network error
            worker_status["last_error"] = str(e)
            print(f"[worker] error: {e}")
        publish_status()
        time.sleep(POLL_SECONDS)


def _take_worker_lock():
    """
    Only one process may run the worker, or two of them would share one
    call a minute. Gunicorn can run several processes, so the first one to
    lock data/.worker.lock wins. The OS releases the lock if it dies.
    """
    DATA_DIR.mkdir(exist_ok=True)
    handle = open(DATA_DIR / ".worker.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle  # keep it open for the life of the process


_worker_lock = None


def start_worker_once():
    """Start the background worker, unless another process already runs it."""
    global _worker_lock
    if _worker_lock is not None:
        return
    _worker_lock = _take_worker_lock()
    if _worker_lock is None:
        print(f"[worker] another process runs the worker; pid {os.getpid()} only serves pages")
        return
    print(f"[worker] started in pid {os.getpid()}")
    start_worker()


def start_worker():
    threading.Thread(target=worker, name="mcp-worker", daemon=True).start()


# =========================================================================== #
# Template helpers                                                            #
# =========================================================================== #

@app.template_filter("num")
def num(value):
    """Format an amount. book.json gives no currency, so we don't add one."""
    return "—" if value is None else f"{value:,.0f}"


CURRENCY_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$"}


@app.template_filter("money")
def money(value, currency=None):
    """
    A money amount with its currency symbol, e.g. 350000 -> $350,000.
    Uses the account's stated currency when its records state one (Kestrel's
    paperwork states GBP). book.json states none, so those amounts show "$".
    """
    if value is None:
        return "—"
    symbol = CURRENCY_SYMBOLS.get(currency or "USD", f"{currency} ")
    return f"{symbol}{value:,.0f}"


@app.template_filter("slug")
def slug(value):
    """An id made safe for an HTML anchor: "K/profile.json#renewal" -> "K-profile-json-renewal"."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(value)).strip("-")


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
    book, accounts, packs = load_accounts()
    signals = load_signals()
    idx = build_index(accounts, book.get("files", []), signals, packs)
    cards = []
    for a in accounts:
        segments = story_segments(a, load_brief(a["id"]), idx)
        cards.append({
            "acct": a,
            # Same totals as the Monday brief's "Book worth" section.
            "worth": book_worth(a),
            "summary": plain_summary(segments),
            "claims": [s["claim"] for s in segments if "claim" in s],
        })
    return render_template("index.html", book=book, signal_count=len(signals), cards=cards, idx=idx,
                           steps=current_status().get("steps", []))


@app.route("/account/<account_id>")
def account(account_id):
    book, accounts, packs = load_accounts()
    acct = find_account(accounts, account_id)
    signals = load_signals(account_id)
    idx = build_index(accounts, book.get("files", []), load_signals(), packs)
    brief = load_brief(account_id)
    moves = [{**m, "when_r": resolve_when(m["when"], acct)} for m in brief["moves"]]
    paper = pack_records(packs[account_id]) if account_id in packs else []
    return render_template(
        "account.html", book=book, acct=acct, brief=brief, moves=moves, idx=idx,
        worth=book_worth(acct), risks=risks(acct, signals, brief), signals=signals,
        story=story_segments(acct, brief, idx), missing=check_brief(brief, idx),
        files=[f for f in book.get("files", []) if f.get("account") == account_id],
        paper=paper, paperwork_only=acct.get("source") == "paperwork",
        notes=list(reversed(load_outbox(account_id))), status=current_status(),
    )


@app.route("/account/<account_id>/next-steps", methods=["POST"])
def save_next_steps(account_id):
    """Queue a write_note(kind="next_step"). The worker sends it within a minute."""
    _, accounts, _ = load_accounts()
    acct = find_account(accounts, account_id)
    brief = load_brief(account_id)
    if not brief["moves"]:
        abort(400, "This account's brief has no moves to save yet.")
    text = next_steps_text(acct, brief)
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
    return render_template("signals.html", signals=load_signals(), status=current_status())


@app.route("/api/signals")
def signals_api():
    """Local signals, newest first. Optional ?account=A. Never calls the MCP."""
    status = {k: v for k, v in current_status().items() if k in ("last_check", "last_error", "new_last_check")}
    return jsonify(signals=load_signals(request.args.get("account")), status=status)


@app.route("/api/notes/<account_id>")
def notes_api(account_id):
    notes = [{k: n.get(k) for k in ("id", "kind", "status", "created", "sent_at", "error")}
             for n in reversed(load_outbox(account_id))]
    return jsonify(notes=notes)


def warn_about_briefs():
    """Print any cited id that doesn't exist, at startup."""
    if not BOOK_FILE.exists():
        return
    with app.test_request_context():
        book, accounts, packs = load_accounts()
    idx = build_index(accounts, book.get("files", []), read_json(SIGNALS_FILE, []), packs)
    for acct in accounts:
        acct_id = acct["id"]
        missing = check_brief(load_brief(acct_id), idx)
        if missing:
            print(f"[briefs] account {acct_id} cites ids not in book/signals: {missing}")


# Under gunicorn (Render: "gunicorn app:app") this file is imported, not run,
# so start the worker on import there. Plain imports (tests, scripts) don't.
if "gunicorn" in os.path.basename(sys.argv[0] if sys.argv else ""):
    warn_about_briefs()
    start_worker_once()


if __name__ == "__main__":
    warn_about_briefs()
    start_worker_once()
    # Render (or any host) passes PORT; locally it's 127.0.0.1:5000.
    # use_reloader=False: the reloader runs this file twice, which would start
    # two workers and break the one-call-a-minute limit.
    port = os.environ.get("PORT")
    app.run(host="0.0.0.0" if port else "127.0.0.1", port=int(port or 5000),
            debug=not port, use_reloader=False)
