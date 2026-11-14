"""
refresh.py - re-download the account book, and optionally an account's paperwork.

  python refresh.py                  re-fetch get_my_accounts -> data/book.json
  python refresh.py --paperwork K    ...then, a minute later, get_all_paperwork
                                     for account K -> data/paperwork_K.json

Respects the one-call-a-minute limit: waits 61s between its own calls, and if
the running app has just used the minute, waits as long as the server says.
The previous book is kept as data/book.prev.json.
"""

import argparse
import json
import shutil
import time
from pathlib import Path

from mcp_client import call_patiently

DATA_DIR = Path(__file__).parent / "data"
BOOK_FILE = DATA_DIR / "book.json"


def save_json(path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def refresh_book():
    """get_my_accounts -> data/book.json (old copy kept as book.prev.json)."""
    print("Calling get_my_accounts ...", flush=True)
    book = call_patiently("get_my_accounts")
    if BOOK_FILE.exists():
        shutil.copy(BOOK_FILE, DATA_DIR / "book.prev.json")
    save_json(BOOK_FILE, book)
    ids = [a["id"] for a in book.get("accounts", [])]
    print(f"Saved {BOOK_FILE} with accounts: {', '.join(ids)}")
    return book


def fetch_paperwork(account):
    """get_all_paperwork(account) -> data/paperwork_<account>.json."""
    print(f"Calling get_all_paperwork for {account} ...", flush=True)
    papers = call_patiently("get_all_paperwork", account=account)
    path = DATA_DIR / f"paperwork_{account}.json"
    save_json(path, papers)
    print(f"Saved {path}")
    return papers


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--paperwork", metavar="ACCOUNT", help="also fetch this account's paperwork")
    args = parser.parse_args()

    refresh_book()
    if args.paperwork:
        print("Waiting 61s (one call a minute) ...", flush=True)
        time.sleep(61)
        fetch_paperwork(args.paperwork)
