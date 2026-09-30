import contextlib
import io
import json
import os
import tempfile
import unittest

from arb.bench import LABEL, run_cases_arb
from harness.case_loader import list_case_dirs
from harness.run import DEFAULT_DISPUTES, main
from harness.scoring import summarize

THREE = list_case_dirs(DEFAULT_DISPUTES, "1,8,26")


class ArbMechanismInHarness(unittest.TestCase):
    def test_records_are_scored_and_flagged_as_an_upper_bound(self):
        runs = run_cases_arb(THREE, 15000)
        self.assertEqual(len(runs), 12)
        for r in runs:
            self.assertTrue(r["upperBoundNotARealResult"])
            self.assertEqual(r["mechanism"], "arb")
            if r["outcome"] == "deal":       # the winning offer is one of the two sealed offers
                self.assertIn(r["dealCents"], (r["claimantOfferCents"], r["respondentOfferCents"]))
        self.assertIn("pairings", summarize(runs))

    def test_same_run_twice_is_identical(self):
        self.assertEqual(run_cases_arb(THREE, 15000), run_cases_arb(THREE, 15000))

    def test_the_closer_offer_to_owed_won_every_time(self):
        for r in run_cases_arb(THREE, 15000):
            if r["outcome"] == "deal":
                dc = abs(r["claimantOfferCents"] - r["owedCents"])
                dr = abs(r["respondentOfferCents"] - r["owedCents"])
                self.assertLessEqual(abs(r["dealCents"] - r["owedCents"]), min(dc, dr))

    def test_cli_labels_output_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "arb.json")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main(["--mechanism", "arb", "--cases", "1,8", "--out", out])
            self.assertEqual(code, 0)
            self.assertIn("UPPER BOUND, not a real result", buf.getvalue())
            with open(out) as f:
                doc = json.load(f)
            self.assertEqual(doc["meta"]["mechanism"], "arb")
            self.assertEqual(doc["meta"]["notARealResult"][:11], LABEL[:11])

    def test_cli_arb_refuses_paid_providers_and_default_is_unchanged(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--mechanism", "arb", "--provider", "anthropic",
                                   "--i-understand-this-costs-money"]), 2)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "h.json")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--cases", "1", "--out", out]), 0)
            with open(out) as f:
                self.assertEqual(json.load(f)["meta"]["mechanism"], "haggle")


if __name__ == "__main__":
    unittest.main()
