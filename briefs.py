"""
briefs.py - the hand-written parts of each account's Monday brief.

Some parts of the brief can be computed straight from book.json (book worth,
what could go wrong). Others need judgement: who decides, which moves come
first, what we don't know. Those live here.

Rules this file follows (and app.py checks at startup):
  * Every entry cites its sources in "src" using ids that exist in
    data/book.json (deal, offer, fact, file or stakeholder ids) or in
    data/signals.json (live signal ids).
  * People are referenced by stakeholder id, so name and relationship
    always come from book.json. Anyone the data doesn't name is described
    in plain words and marked "not named".
  * No dates are made up. "when" is either a deadline from book.json
    ({"deadline": <deal id>}) or text that quotes a date found in a cited
    signal or fact.

Power map shape, per deal:
  role -> {"people": [stakeholder ids], "note": str, "src": [ids]}
  An empty "people" list renders as "unknown".
"""

BRIEFS = {
    # ------------------------------------------------------------------ #
    # A: Eastern Counties Police Alliance                                 #
    # ------------------------------------------------------------------ #
    "A": {
        # Phrases in official_story that are really someone's unverified claim.
        "story_claims": [],

        "power_map": {
            "a_renewal": {
                "decides": {"people": [], "note": "Not named. Rhys Calder leads the programme but leaves in January.",
                            "src": ["rhys_calder", "champion_leaves"]},
                "pays": {"people": [], "note": "Each force pays from its own budget, and its chief finance officer approves the call-off. None is named.",
                         "src": ["rc_funding_2", "hd_calloff_2"]},
                "uses": {"people": ["karen_mbeki"], "note": "Westmere and Metro Central fly operationally. Metro Central's users aren't named.",
                         "src": ["km_usage_1", "rc_appetite_2"]},
                "blocks": {"people": [], "note": "Not named. Open blocker: nobody can show which forces use the docks.",
                           "src": ["a_renewal"]},
            },
            "a_tarn": {
                "decides": {"people": [], "note": "Not named.", "src": []},
                "pays": {"people": [], "note": "Each force's chief finance officer. Not named.", "src": ["rc_funding_2"]},
                "uses": {"people": [], "note": "Tarn Valley and Norwold officers, not named. Tarn Valley has never flown a live mission; Norwold only trains.",
                         "src": ["rc_appetite_2"]},
                "blocks": {"people": ["fatima_osei"], "note": "Signs the DPIA. Norwold's chief constable (not named) is cautious after a privacy row.",
                           "src": ["fo_dpia_2", "fo_needs_3", "km_others_2"]},
            },
            "a_westmere": {
                "decides": {"people": [], "note": "Not named.", "src": []},
                "pays": {"people": [], "note": "Westmere's chief finance officer. Not named.", "src": ["rc_funding_2"]},
                "uses": {"people": ["karen_mbeki"], "note": "Her unit: six officers who also do other duties.", "src": ["km_staff_2"]},
                "blocks": {"people": [], "note": "The superintendent (not named) thinks 30 docks is unrealistic. Nobody has asked her.",
                           "src": ["kb_stadium_view"]},
            },
            "a_tender": {
                "decides": {"people": [], "note": "Scorers not named. Hannah Doyle leads alliance procurement and runs the clarification window.",
                            "src": ["hannah_doyle", "hd_window_2"]},
                "pays": {"people": [], "note": "No pooled budget. Each force's finance signs its own call-off.",
                         "src": ["rc_funding_2", "hd_calloff_2"]},
                "uses": {"people": [], "note": "Nobody has agreed who would fly 300 docks.", "src": ["rc_tender_2"]},
                "blocks": {"people": [], "note": "Not named.", "src": []},
            },
        },

        "moves": [
            {
                "do": "Reply to Karen Mbeki in writing: how many more officers can be trained before the event and when, "
                      "and who answers, how fast, if a dock fails on the day. She bought operator training and priority "
                      "support; this is where both get tested.",
                "owner": "You (CSM)",
                "when": {"text": "This week, before the 5 December public event", "src": ["urgent_request"]},
                "deal": "a_westmere",
                "src": ["urgent_request", "a_training", "a_priority_support", "km_staff_2"],
            },
            {
                "do": "Send Hannah Doyle a written clarification request on cost per flight and the operating model, "
                      "using the clarification template. Together they carry 40% of the score, and the spec has neither.",
                "owner": "You (CSM)",
                "when": {"deadline": "a_tender"},
                "deal": "a_tender",
                "src": ["hd_window_2", "hd_weights_1", "rc_tender_2", "clarification_template"],
            },
            {
                "do": "Ask Rhys Calder who owns the renewal after him, and send per-force usage from the flight log so "
                      "the renewal rests on measured flying, not on \"all four forces are keen\".",
                "owner": "You (CSM)",
                "when": {"text": "Before Rhys Calder hands over in January", "src": ["champion_leaves"]},
                "deal": "a_renewal",
                "src": ["champion_leaves", "flight_log_force_month", "km_usage_1", "rc_appetite_0"],
            },
        ],

        "gaps": [
            {"gap": "Which forces actually fly, dock by dock",
             "why": "It is the open blocker on the renewal.",
             "who": [{"id": "karen_mbeki", "note": "for Westmere"}, {"text": "The force flight log file", "src": ["flight_log_force_month"]}],
             "src": ["a_renewal", "km_usage_1", "rc_appetite_2"]},
            {"gap": "Cost per flight against the helicopter contract",
             "why": "Cost per flight and the operating model carry 40% of the tender score. Nobody has compared them.",
             "who": [{"id": "karen_mbeki", "note": "flight and overtime figures"}, {"id": "hannah_doyle", "note": "the comparator"}],
             "src": ["hd_weights_1", "km_value_2", "km_staff_2"]},
            {"gap": "Who would fly 300 docks",
             "why": "The spec has no costed operating model, which is the tender's open blocker.",
             "who": [{"id": "karen_mbeki", "note": "what scaling took at Westmere"}],
             "src": ["rc_tender_2", "a_tender", "km_staff_2"]},
            {"gap": "Rhys Calder's successor",
             "why": "Every call on file was with him alone, and he leaves in January.",
             "who": [{"id": "rhys_calder"}],
             "src": ["champion_leaves"]},
            {"gap": "Who signs the renewal",
             "why": "No decision-maker is named for the largest near-term deal.",
             "who": [{"id": "rhys_calder"}],
             "src": ["a_renewal"]},
            {"gap": "The superintendent's view on 30 docks at Westmere",
             "why": "The 30-dock figure is Rhys Calder's unverified claim, and she thinks it's unrealistic.",
             "who": [{"text": "The superintendent (not named); Karen Mbeki raised it", "src": ["kb_stadium_view"]}],
             "src": ["rc_stadium_0", "kb_stadium_view"]},
            {"gap": "Whether all four forces are keen",
             "why": "Only an unverified claim. Two of the four don't fly operationally.",
             "who": [{"text": "Each force's lead (not named)", "src": []}],
             "src": ["rc_appetite_0", "rc_appetite_2"]},
        ],
    },

    # ------------------------------------------------------------------ #
    # B: Aldermoor Industrial Group                                       #
    # ------------------------------------------------------------------ #
    "B": {
        "story_claims": [
            {"phrase": "ROI proven.", "claim": "sr_roi_0", "counter": "sr_roi_2"},
            {"phrase": "13 flying.", "claim": "sr_roll_0", "counter": "sr_roll_2"},
        ],

        "power_map": {
            "b_renewal": {
                "decides": {"people": [], "note": "Not named. Nearest: Stefan Roy, group head of security.", "src": ["stefan_roy"]},
                "pays": {"people": [], "note": "The plants, through an annual security allocation. Several plant managers resent paying.",
                         "src": ["sr_plants_2", "nr_cost_2"]},
                "uses": {"people": [], "note": "Four plants fly regularly. Their operators aren't named.", "src": ["sr_roll_2"]},
                "blocks": {"people": ["nadia_rahman"], "note": "Sees added cost and no guard contract reduction.",
                           "src": ["nr_cost_2", "b_renewal"]},
            },
            "b_idle": {
                "decides": {"people": [], "note": "Not named.", "src": []},
                "pays": {"people": [], "note": "The plants, through the allocation.", "src": ["sr_plants_2"]},
                "uses": {"people": [], "note": "Nine plants only log test flights. Their managers aren't named.", "src": ["sr_roll_2"]},
                "blocks": {"people": [], "note": "The network project will cut off the docks; its owner isn't named.",
                           "src": ["b_idle"]},
            },
            "b_insurance": {
                "decides": {"people": [], "note": "The insurer (not named). Julian Tate reported the cut.", "src": ["jt_prem_2"]},
                "pays": {"people": [], "note": "Not named.", "src": []},
                "uses": {"people": [], "note": "Not named.", "src": []},
                "blocks": {"people": [], "note": "Not named.", "src": []},
            },
            "b_sister": {
                "decides": {"people": [], "note": "Nobody at the sister company is named.", "src": []},
                "pays": {"people": [], "note": "Not Nadia Rahman's budget. The payer isn't named.", "src": ["nr_sister_1"]},
                "uses": {"people": [], "note": "Not named.", "src": []},
                "blocks": {"people": [], "note": "Not named. Every number rests on Stefan Roy's unverified spreadsheet.",
                           "src": ["b_sister", "sr_case_3", "roi_spreadsheet"]},
            },
        },

        "moves": [
            {
                "do": "Get the rollout template written down with Stefan Roy's deputy. Today it lives in the deputy's "
                      "head and nothing is on paper.",
                "owner": "You (CSM), with Stefan Roy",
                "when": {"deadline": "b_idle"},
                "deal": "b_idle",
                "src": ["sr_repl_2", "b_idle"],
            },
            {
                "do": "Take the insurer's 6% premium cut (about 180,000 euros a year across nine plants) to Nadia Rahman "
                      "as the first measured benefit. She has said she'd accept the premium as evidence.",
                "owner": "You (CSM), with Julian Tate",
                "when": {"text": "Before the three guard contract renewals (due within four months)", "src": ["nr_evidence_2"]},
                "deal": "b_renewal",
                "src": ["jt_prem_2", "nr_evidence_2", "insurer_premium_letter"],
            },
            {
                "do": "Rebuild the ROI case on measured changes only (guard contract renewals, the premium, incident "
                      "losses) and show it to finance before any of it goes into the sister-company case study.",
                "owner": "You (CSM), with Stefan Roy and Nadia Rahman",
                "when": {"text": "Before the three guard contract renewals (due within four months)", "src": ["nr_evidence_2"]},
                "deal": "b_sister",
                "src": ["sr_roi_2", "sr_case_3", "nr_evidence_2", "roi_spreadsheet", "allocation_guard_summary"],
            },
        ],

        "gaps": [
            {"gap": "Any saving finance has booked",
             "why": "No guard contract has been reduced and finance has booked nothing. It is the renewal's open blocker.",
             "who": [{"id": "nadia_rahman"}],
             "src": ["sr_roi_2", "nr_cost_2", "b_renewal"]},
            {"gap": "Incident losses before and after the drones",
             "why": "Finance would accept them as evidence of ROI.",
             "who": [{"id": "julian_tate", "note": "by role, not confirmed"}],
             "src": ["nr_evidence_2"]},
            {"gap": "Who owns the network project, and when it cuts the docks off",
             "why": "It is the open blocker on bringing the idle plants into patrol.",
             "who": [{"id": "owen_fitch", "note": "by role, not confirmed"}],
             "src": ["b_idle"]},
            {"gap": "Why nine plants only log test flights",
             "why": "The idle-plants deal depends on the answer.",
             "who": [{"text": "Plant managers (not named)", "src": ["sr_roll_2"]},
                     {"id": "beatriz_alonso", "note": "the only plant manager named"}],
             "src": ["sr_roll_2", "b_idle"]},
            {"gap": "Who decides for the sister company",
             "why": "The largest deal in the book has no named buyer or budget.",
             "who": [{"id": "stefan_roy", "note": "the case study's numbers are his"}],
             "src": ["b_sister", "nr_sister_1", "sr_case_3"]},
            {"gap": "When the deputy leaves",
             "why": "The book gives moves left, not a date.",
             "who": [{"id": "stefan_roy"}],
             "src": ["b_idle", "sr_repl_2"]},
        ],
    },
}
