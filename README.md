# FlytBase CRM: Monday brief for two customer accounts

A live CRM for a FlytBase customer success manager who has just inherited two accounts:

- **Eastern Counties Police Alliance**: four police forces, 35 docks, a 300-dock framework tender in preparation
- **Aldermoor Industrial Group**: 15 plants, 14 docks, a case study planned for a 40-plant sister company

It reads the account data live from the Questathon MCP server and turns it into the brief I would hand my manager on Monday.

**Live CRM:** [add your hosted link]
**Walkthrough video:** [add your Google Drive link]

---

## What it answers, for each account

| # | Question | What the page shows |
|---|---|---|
| 1 | **What is the book worth?** | Every deal and offer, split into **Backed** (won or agreed, with its id) and **Not yet backed** ("worth up to" values, each with the reason it is not backed) |
| 2 | **Who holds the power?** | For each deal: who decides, who pays, who uses it, who could block it, and where each person stands with us. If the data does not name someone, it says **Unknown** |
| 3 | **What do I do first?** | Three moves, ranked, each with an owner, a date or deadline from the data, and the deal it moves |
| 4 | **What is about to go wrong?** | Deadlines with moves left, key people who are neutral or guarded, deals with open blockers, and declined offers |
| 5 | **What do I still not know?** | Each gap, why it matters, and who could fill it |

Below the brief, the raw evidence (facts, files, stakeholders, offers) sits in collapsible sections.

---

## How it decides: the rules

1. **Every number cites its source.** Each value, quote and move links to the id it came from in the data (deal, offer, fact or file id). Click **ⓘ sources** to see them.
2. **Nothing is invented.** No id, date, fact or person is made up. If the data does not say, the page shows **Unknown** and names who could fill the gap.
3. **Claims are not facts.** Anything a person asserted without proof (data status `claim`) is shown as an **unverified claim**, for example "ROI proven, 1.2 million euros", which is Stefan Roy's own spreadsheet and not booked by finance.
4. **Backed means agreed.** Only won or agreed offers count as backed value. "Worth up to" figures are shown separately and never added to the backed total.
5. **First moves are ranked by deadline and risk.** A move tied to a deadline that is close (for example, Stefan's deputy leaving with the rollout know-how) ranks above one with more time.

---

## How it stays live

- The full account book is fetched **once** with `get_my_accounts` and stored locally, because each token gets **one call a minute**.
- A background job calls `get_signals` **every 60 seconds** and adds new live events to the top of each account page, with the time received. No code change or reload is needed.
- The **Save next steps** button writes the three first moves back to the game through `write_note` (kind `next_step`).

---

## How it works

```
Questathon MCP server
        │  (token sent from the server only)
        ▼
connect / fetch layer ──► data/book.json      (fetched once)
        │             ──► data/signals.json   (polled every 60 s)
        ▼
Flask app ──► builds the five answers from the stored data
        │
        ▼
Account pages (Monday brief + evidence)  ──► write_note back to the MCP
```

All MCP calls are JSON-RPC `tools/call` requests to:

```
https://questathon.aihackerscollective.com/api/public/mcp
```

Tools used: `whoami`, `get_my_accounts`, `get_signals`, `write_note`.

---

## Run it locally

**Requirements:** Python 3.10 or newer.

1. Clone the repository:
   ```bash
   git clone [your repo URL]
   cd [repo folder]
   ```
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Create a file called `.env` in the project folder with your own token:
   ```
   AQ_TOKEN=your-token-here
   ```
4. Fetch your data once:
   ```bash
   python connect.py
   ```
5. Start the app:
   ```bash
   python app.py
   ```
6. Open `http://127.0.0.1:5000` in your browser.

---

## Deploy (Render)

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn app:app` (or `python app.py`, which reads Render's `PORT`)
- **Environment:** set `AQ_TOKEN` to your token. The `.env` file isn't deployed.

`data/` is gitignored, so a fresh deploy starts empty and the app fetches its own data, one call a minute:

1. `get_my_accounts` saves `data/book.json`.
2. `whoami` lists any new accounts (for example Kestrel, K).
3. `get_all_paperwork` saves `data/paperwork_<id>.json` for each new account.
4. After that, `get_signals` runs every minute, and `whoami` every 10 minutes.

Until the book is saved, every page shows "Loading data, refresh in a minute". Render's disk is wiped on each deploy or restart, so this happens again each time (about three minutes).

## What it does not do yet

- **The account book is a snapshot.** Live events refresh every minute, but the full book is only re-fetched on demand, so a change outside the live events needs a manual refresh.
- **Totals can overlap.** An offer can be part of a deal, and the data does not say which, so the "Not yet backed" total may double-count.
- **The power map is only as good as what people said.** Where nobody named the decider or payer, it shows Unknown instead of guessing.
- **No currency conversion.** Amounts are shown as given in the data, and the Aldermoor insurance saving is in euros.

---

## How I used AI

- I used **Claude Code** to write the code: the MCP connection, the Flask app and the page layout.
- I set the rules it had to follow: every number cites an id, nothing is invented, claims are shown as claims, and backed value means agreed only.
- I checked the numbers on the page against the facts and files, and asked what each one meant before keeping it.
- The account findings (which claims held up, which did not) come from my own discovery in the morning game.

---

## Project structure

```
[repo folder]/
├── app.py            # Flask app: builds the five answers and serves the pages
├── connect.py        # Fetches the account book once and saves it
├── templates/        # Page templates
├── static/           # Styles
├── requirements.txt
├── .gitignore        # Keeps .env and data/ out of the repository
└── README.md
```

Adjust this list to match your actual files.
