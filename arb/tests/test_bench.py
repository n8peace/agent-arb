import contextlib
import io
import itertools
import json
import os
import tempfile
import unittest

from arb import decide
from arb.bench import LABEL, run_cases_arb, sealed_offer
from harness.case_loader import list_case_dirs, load_case
from harness.prompts import SEALED_KICKOFF, SEALED_MODE_LINE, build_sealed_prompt, is_shared
from harness.providers import ScriptedProvider
from harness.run import DEFAULT_DISPUTES, main
from harness.scoring import load_answer, summarize

THREE = list_case_dirs(DEFAULT_DISPUTES, "1,8,26")
WIDE = list_case_dirs(DEFAULT_DISPUTES, "14,18")          # answer.json ranges wider than the narrow limit
ALL_CASES = list_case_dirs(DEFAULT_DISPUTES, "all")
MECHS = decide.MECHANISMS


class Recorder:
    """Wraps a provider and records every (system, messages) it is asked to answer, plus replies."""
    def __init__(self, inner, log):
        self.inner, self.log, self.name = inner, log, inner.name

    def reply(self, system, messages):
        text = self.inner.reply(system, messages)
        self.log.append((system, [dict(m) for m in messages], text))
        return text


class Fixed:
    """A stand-in opponent that always replies with the same text, whatever it is shown."""
    def __init__(self, text):
        self.text, self.name = text, "fixed"

    def reply(self, system, messages):
        return self.text


OPPONENT_REPLIES = ["OFFER: 0", "OFFER: 1", "OFFER: 999999999", "I refuse.", "ACCEPT",
                    "Pay me.\nOFFER: 50000"]


def factory_for(tested_role, tested_strength, opponent_text, log):
    """run_cases_arb calls factory(claimant strength) then factory(respondent strength) per pairing."""
    calls = itertools.count()

    def factory(strength):
        role = "claimant" if next(calls) % 2 == 0 else "respondent"
        if role == tested_role:
            return Recorder(ScriptedProvider(tested_strength), log)
        return Fixed(opponent_text)
    return factory


class SealedOffers(unittest.TestCase):
    """Offers are truly sealed: a side's offer never depends on what the other side does."""

    def test_a_sides_offer_is_identical_whatever_the_other_side_does(self):
        for mech in MECHS:
            for role in ("claimant", "respondent"):
                for strength in ("weak", "strong"):
                    seen = set()
                    for opp in OPPONENT_REPLIES:
                        log = []
                        runs = run_cases_arb(THREE, 15000, factory_for(role, strength, opp, log), mechanism=mech)
                        offers = tuple(r["%sOfferCents" % role] for r in runs)
                        inputs = tuple((s, json.dumps(m, sort_keys=True), t) for s, m, t in log)
                        seen.add((offers, inputs))
                        for _, msgs, _ in log:     # only the fixed kickoff, never the other side's reply
                            self.assertEqual(msgs, [{"role": "user", "content": SEALED_KICKOFF}])
                    self.assertEqual(len(seen), 1, (mech, role, strength))

    def test_every_pairing_gives_each_side_the_same_offer_as_its_strength_alone_decides(self):
        for mech in MECHS:
            runs = run_cases_arb(ALL_CASES, 15000, mechanism=mech)
            by_case = {}
            for r in runs:
                c = by_case.setdefault(r["caseId"], {"claimant": {}, "respondent": {}})
                c["claimant"].setdefault(r["claimantStrength"], set()).add(r["claimantOfferCents"])
                c["respondent"].setdefault(r["respondentStrength"], set()).add(r["respondentOfferCents"])
            for case_id, sides in by_case.items():
                for role, per_strength in sides.items():
                    for strength, offers in per_strength.items():
                        self.assertEqual(len(offers), 1, (mech, case_id, role, strength))

    def test_sealed_prompts_keep_the_visibility_rule_and_never_leak_the_answer(self):
        for case_dir in ALL_CASES:
            case, ans = load_case(case_dir), load_answer(case_dir)
            for mech in MECHS:
                for role, other in (("claimant", "respondent"), ("respondent", "claimant")):
                    prompt = build_sealed_prompt(case, role, mech)
                    self.assertIn(SEALED_MODE_LINE, prompt)
                    self.assertNotIn("    ACCEPT   ", prompt)     # no accepting: nothing to accept
                    other_pid = case.party_id(other)
                    for ev in case.evidence.values():
                        if ev.party_id == other_pid and not is_shared(case, ev.evidence_id):
                            self.assertNotIn("DOCUMENT %s " % ev.evidence_id, prompt)
                    for c in case.claims:
                        if c.party_id == other_pid:
                            self.assertNotIn("CLAIM %s " % c.claim_id, prompt)
                    for needle in ("answer.json", "owedCents", "lowCents", "highCents", ans["reasoning"],
                                   case.dir_name):
                        self.assertNotIn(needle, prompt)

    def test_malformed_sealed_reply_gets_one_retry_then_counts_as_missing(self):
        class Twice:
            name = "twice"

            def __init__(self, replies):
                self.replies, self.calls = list(replies), []

            def reply(self, system, messages):
                self.calls.append(list(messages))
                return self.replies.pop(0)
        p = Twice(["no number", "fine\nOFFER: 1200"])
        self.assertEqual(sealed_offer(p, "sys")[1], 1200)
        self.assertEqual(len(p.calls), 2)
        self.assertEqual(sealed_offer(Twice(["no", "still no"]), "sys")[1], None)

    def test_missing_offer_is_human_review_and_no_deal(self):
        log = []
        runs = run_cases_arb(THREE, 15000, factory_for("claimant", "weak", "I refuse.", log))
        self.assertTrue(all(r["outcome"] == "no_deal" and r["reason"] == "human_review" for r in runs))


class ArbMechanismInHarness(unittest.TestCase):
    def test_records_are_scored_and_flagged_as_an_upper_bound(self):
        for mech, name in ((decide.MECH_FINAL_OFFER, "arb-final"), (decide.MECH_CLAMPED, "arb-clamped")):
            runs = run_cases_arb(THREE + WIDE, 15000, mechanism=mech)
            self.assertEqual(len(runs), 20)
            for r in runs:
                self.assertTrue(r["upperBoundNotARealResult"])
                self.assertEqual(r["mechanism"], name)
                low, high = r["owedRangeCents"]
                if r["outcome"] != "deal":
                    continue
                if mech == decide.MECH_FINAL_OFFER:     # the winning offer is one of the two sealed offers
                    self.assertIn(r["dealCents"], (r["claimantOfferCents"], r["respondentOfferCents"]))
                else:                                   # clamped: always inside the panel's range
                    self.assertTrue(low <= r["dealCents"] <= high)
                    if r["winningRole"] is None:
                        self.assertEqual(r["dealCents"], r["owedCents"])
            self.assertIn("pairings", summarize(runs))

    def test_same_run_twice_is_identical(self):
        for mech in MECHS:
            self.assertEqual(run_cases_arb(THREE, 15000, mechanism=mech),
                             run_cases_arb(THREE, 15000, mechanism=mech))

    def test_the_closer_offer_to_owed_won_every_time(self):
        for r in run_cases_arb(THREE, 15000):
            if r["outcome"] == "deal":
                dc = abs(r["claimantOfferCents"] - r["owedCents"])
                dr = abs(r["respondentOfferCents"] - r["owedCents"])
                self.assertLessEqual(abs(r["dealCents"] - r["owedCents"]), min(dc, dr))

    def test_clamped_uses_the_answer_range_and_awards_point_when_narrow(self):
        runs = run_cases_arb(THREE, 15000, mechanism=decide.MECH_CLAMPED)   # all three have exact answers
        for r in runs:
            self.assertEqual((r["outcome"], r["dealCents"], r["winningRole"]), ("deal", r["owedCents"], None))

    def test_unknown_mechanism_is_refused(self):
        with self.assertRaises(ValueError):
            run_cases_arb(THREE, 15000, mechanism="haggle")

    def test_cli_labels_output_and_json(self):
        for flag, meta in (("arb", "arb-final"), ("arb-final", "arb-final"), ("arb-clamped", "arb-clamped")):
            with tempfile.TemporaryDirectory() as d:
                out = os.path.join(d, "arb.json")
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    code = main(["--mechanism", flag, "--cases", "1,8", "--out", out])
                self.assertEqual(code, 0)
                self.assertIn("UPPER BOUND, not a real result", buf.getvalue())
                with open(out) as f:
                    doc = json.load(f)
                self.assertEqual(doc["meta"]["mechanism"], meta)
                self.assertEqual(doc["meta"]["notARealResult"], LABEL)
                self.assertEqual({r["mechanism"] for r in doc["runs"]}, {meta})

    def test_cli_arb_refuses_paid_providers_and_default_is_unchanged(self):
        for flag in ("arb", "arb-final", "arb-clamped"):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--mechanism", flag, "--provider", "anthropic",
                                       "--i-understand-this-costs-money"]), 2)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "h.json")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--cases", "1", "--out", out]), 0)
            with open(out) as f:
                doc = json.load(f)
            self.assertEqual(doc["meta"]["mechanism"], "haggle")
            self.assertIsNone(doc["meta"]["notARealResult"])


if __name__ == "__main__":
    unittest.main()
