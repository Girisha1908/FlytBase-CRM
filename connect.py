"""
connect.py - one-time setup: check the token works and download your book.

What it does:
  1. Calls whoami          -> saves data/me.json
  2. Waits ~61 seconds     (the server allows one call per minute per token)
  3. Calls get_my_accounts -> saves data/book.json
  4. Prints a short summary

Run it with:   python connect.py

After this, the Flask app reads data/book.json instead of calling the server.
"""

import json
import time
from pathlib import Path

from mcp_client import RateLimited, call_tool

DATA_DIR = Path(__file__).parent / "data"
ME_FILE = DATA_DIR / "me.json"
BOOK_FILE = DATA_DIR / "book.json"

WAIT_SECONDS = 61  # one call per minute, plus a second of margin


def save_json(path, data):
    """Write data to a JSON file, creating the data/ folder if needed."""
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def find_accounts(book):
    """
    get_my_accounts might return a list, or a dict wrapping a list
    (e.g. {"accounts": [...]}). Return the list either way.
    """
    if isinstance(book, list):
        return book
    if isinstance(book, dict):
        for value in book.values():
            if isinstance(value, list):
                return value
    return []


def main():
    # --- 1. whoami: proves the token is valid -------------------------------
    if ME_FILE.exists():
        # Already done on a previous run; don't spend a call on it again.
        me = json.loads(ME_FILE.read_text(encoding="utf-8"))
        print(f"Using saved identity from {ME_FILE.name}")
    else:
        print("Calling whoami ...")
        me = call_tool("whoami")
        save_json(ME_FILE, me)
        print(f"Saved {ME_FILE}")

        # We just used this minute's call, so wait before the next one.
        print(f"Waiting {WAIT_SECONDS}s for the rate limit ...")
        time.sleep(WAIT_SECONDS)

    # --- 2. get_my_accounts: the book of accounts -----------------------------
    print("Calling get_my_accounts ...")
    try:
        book = call_tool("get_my_accounts")
    except RateLimited as e:
        print(f"Rate limited: {e}\nWait a minute and run connect.py again "
              "(whoami is saved, so it won't be called twice).")
        return
    save_json(BOOK_FILE, book)
    print(f"Saved {BOOK_FILE}")

    # --- 3. Summary -----------------------------------------------------------
    accounts = find_accounts(book)
    print("\n=== Summary ===")
    print("You are:", json.dumps(me) if not isinstance(me, str) else me)
    print(f"Accounts in your book: {len(accounts)}")
    for acct in accounts[:5]:
        # Show a name if we can find one, else the raw record.
        label = acct.get("name") or acct.get("account_name") if isinstance(acct, dict) else None
        print("  -", label or acct)
    if len(accounts) > 5:
        print(f"  ... and {len(accounts) - 5} more (see data/book.json)")


if __name__ == "__main__":
    main()
