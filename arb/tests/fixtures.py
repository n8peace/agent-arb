"""Shared builders for the arb tests. Pure data, no randomness."""
import copy

from arb.panel import FixturePanelist, collect_votes

PIE = 200000            # cents: the whole amount in play (for example the deposit held)
A, B = "pty_aaaaaa", "pty_bbbbbb"
PIE_CLAIM = "clm_pie001"
DEDUCTIONS = (("clm_ded001", 30000), ("clm_ded002", 65000), ("clm_ded003", 12000))
SEALED = "2026-01-01T00:00:00Z"


def make_case(a_type="person", b_type="business", deductions=DEDUCTIONS, pie=PIE, a_agent=None, b_agent=None):
    """A is the claimant and claims the whole pie. B is the respondent and claims deductions.
    owed = pie - accepted deductions, so a role swap has an exact mirror (see mirror_case)."""
    pa = {"partyId": A, "role": "claimant", "type": a_type}
    pb = {"partyId": B, "role": "respondent", "type": b_type}
    if a_agent:
        pa["agent"] = dict(a_agent)
    if b_agent:
        pb["agent"] = dict(b_agent)
    claims = [{"claimId": PIE_CLAIM, "partyId": A, "assertion": "The whole amount was held.",
               "amount": pie, "evidenceIds": ["ev_note001"]}]
    for cid, amt in deductions:
        claims.append({"claimId": cid, "partyId": B, "assertion": "A deduction applies.",
                       "amount": amt, "evidenceIds": ["ev_note001"]})
    return {
        "version": "0.1",
        "caseId": "case_fixture000001",
        "kind": "security_deposit",
        "parties": [pa, pb],
        "contract": {"evidenceId": "ev_contr01", "jurisdiction": "US-CA"},
        "claims": claims,
        "evidence": [
            {"evidenceId": "ev_contr01", "partyId": A, "type": "contract", "sha256": "0" * 64,
             "note": "The lease."},
            {"evidenceId": "ev_note001", "partyId": B, "type": "statement", "sha256": "1" * 64,
             "note": "The statement."},
        ],
        "offers": [],
    }


def make_findings(case, verdicts, adverse=()):
    """verdicts: {claimId: 'accepted'|'rejected'|'abstain' | (r1, r2, r3)}. Unlisted claims are
    accepted unanimously. Votes come from three FixturePanelists, the same way a real panel would."""
    votes_for = {}
    for c in case["claims"]:
        v = verdicts.get(c["claimId"], "accepted")
        votes_for[c["claimId"]] = (v, v, v) if isinstance(v, str) else tuple(v)
    panelists = [FixturePanelist("vendorx/model-%d" % i, {cid: r[i] for cid, r in votes_for.items()})
                 for i in range(3)]
    return [{"claimId": c["claimId"],
             "votes": collect_votes(panelists, c, case["evidence"]),
             "adverseInference": c["claimId"] in adverse}
            for c in case["claims"]]


def make_offers(a_amount, b_amount, a_id=A, b_id=B):
    return [{"partyId": a_id, "amount": a_amount, "sealedAt": SEALED},
            {"partyId": b_id, "amount": b_amount, "sealedAt": SEALED}]


def mirror_case(case, pie=PIE):
    """Swap the claimant and respondent roles. Amounts flip through the pie: what a party is owed
    becomes pie minus that. The claimant's "whole pie" claim has no counterpart once roles swap
    (the new respondent has nothing to deduct), so it is dropped; every other claim keeps its
    owner and amount and simply lands on the other side."""
    m = copy.deepcopy(case)
    for p in m["parties"]:
        p["role"] = "respondent" if p["role"] == "claimant" else "claimant"
    m["claims"] = [c for c in m["claims"] if c["claimId"] != PIE_CLAIM]
    return m


def mirror_findings(findings):
    return [copy.deepcopy(f) for f in findings if f["claimId"] != PIE_CLAIM]


def mirror_offers(offers, pie=PIE):
    return [dict(o, amount=pie - o["amount"]) for o in offers]


def raw_view(ruling):
    d = ruling["decision"]
    view = {"mechanism": d["mechanism"], "winningPartyId": d.get("winningPartyId")}
    if d["mechanism"] in ("final_offer", "clamped"):     # human_review awards nothing
        view["outcome"] = d["outcome"]
    view["owed"] = dict(ruling["owed"])
    return view


def expected_mirror_view(ruling, pie=PIE):
    """What the mirrored case's ruling must look like, given the original ruling: same winner and
    mechanism, and outcome/owed replaced by pie minus themselves (low and high swap)."""
    view = raw_view(ruling)
    if "outcome" in view:
        view["outcome"] = pie - view["outcome"]
    o = ruling["owed"]
    view["owed"] = {"low": pie - o["high"], "high": pie - o["low"], "point": pie - o["point"]}
    return view
