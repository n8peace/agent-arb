import contextlib
import io
import json
import os
import tempfile
import unittest

from arb import decide
from arb.bench import SIM_LABEL, run_cases_arb
from arb.simpanel import (BIAS_SCALE_PCT, SimPanel, aggregate, bias_shift_cents, contested_cents,
                          extra_metrics, filed_claims, filing_volume, make_panel)
from harness.case_loader import list_case_dirs, load_case
from harness.prompts import SEALED_KICKOFF
from harness.providers import ScriptedProvider
from harness.run import DEFAULT_DISPUTES, main
from harness.scoring import load_answer, summarize

ALL_CASES = list_case_dirs(DEFAULT_DISPUTES, "all")
FEW = list_case_dirs(DEFAULT_DISPUTES, "1,8,14,18,26")
CASE1 = list_case_dirs(DEFAULT_DISPUTES, "1")[0]
MECHS = decide.MECHANISMS
PANEL_KEYS = ("panel", "panelPointCents", "panelRangeCents")


def strip_panel(runs):
    return [{k: v for k, v in r.items() if k not in PANEL_KEYS} for r in runs]


class PerfectPanelIsUnchanged(unittest.TestCase):
    # Pinned from the perfect-panel runs before simulated panels existed (PLAN.md "Results so far").
    PINNED = {  # mechanism: (claimant gap, respondent gap, total |err| cents, human_review runs)
        decide.MECH_FINAL_OFFER: (-9357.91, -7544.59, 4069691, 3),
        decide.MECH_CLAMPED: (-373.0, 15.0, 22300, 3),
    }

    def test_perfect_panel_reproduces_the_current_numbers_exactly(self):
        for mech in MECHS:
            default = run_cases_arb(ALL_CASES, 15000, mechanism=mech)
            perfect = run_cases_arb(ALL_CASES, 15000, mechanism=mech, panel=make_panel("perfect"))
            self.assertEqual(default, perfect)
            g = summarize(perfect)["strengthGap"]
            got = (round(g["claimant"]["meanCents"], 2), round(g["respondent"]["meanCents"], 2),
                   sum(r["absErrorCents"] for r in perfect),
                   sum(r["reason"] == "human_review" for r in perfect))
            self.assertEqual(got, self.PINNED[mech], mech)

    def test_perfect_ignores_noise_settings_and_zero_noise_equals_perfect(self):
        for mech in MECHS:
            base = run_cases_arb(ALL_CASES, 15000, mechanism=mech)
            self.assertEqual(base, run_cases_arb(ALL_CASES, 15000, mechanism=mech,
                                                 panel=make_panel("perfect", noise_pct=50, seed=9)))
            zero = run_cases_arb(ALL_CASES, 15000, mechanism=mech,
                                 panel=make_panel("noisy", noise_pct=0, seed=3))
            self.assertEqual(base, strip_panel(zero))
            for r in zero:
                self.assertEqual(r["panelPointCents"], r["owedCents"])
                self.assertEqual(r["panelRangeCents"], r["owedRangeCents"])


class MedianAndRangeMath(unittest.TestCase):
    def test_hand_computed_three_panelist_example(self):
        # truth: owed 10,000, range 10,000..12,000. Errors +500, -300, +1,200.
        #   points: 10,500   9,700  11,200  -> sorted 9,700 10,500 11,200 -> median 10,500
        #   lows:   10,500   9,700  11,200  -> min 9,700,  median 10,500
        #   highs:  12,500  11,700  13,200  -> max 13,200, median 12,500
        # margin 200: min-max range 9,500..13,400; median range 10,300..12,700.
        errs = [500, -300, 1200]
        self.assertEqual(aggregate(10000, 10000, 12000, errs, 200), (10500, (9500, 13400)))
        self.assertEqual(aggregate(10000, 10000, 12000, errs, 0), (10500, (9700, 13200)))
        self.assertEqual(aggregate(10000, 10000, 12000, errs, 200, "median"), (10500, (10300, 12700)))
        # order of panelists does not matter
        self.assertEqual(aggregate(10000, 10000, 12000, [1200, 500, -300], 200), (10500, (9500, 13400)))

    def test_two_agreeing_panelists_decide_and_one_outlier_cannot_move_the_point(self):
        # two panelists near the truth, one wildly off: the median stays with the two
        self.assertEqual(aggregate(10000, 10000, 10000, [0, 40, 900000], 0)[0], 10040)
        self.assertEqual(aggregate(10000, 10000, 10000, [-900000, 0, 40], 0)[0], 10000)

    def test_floor_at_zero_rounding_and_point_inside_range(self):
        self.assertEqual(aggregate(1000, 500, 1500, [-2000.4, -600.6, 10.5], 300), (399, (0, 1510 + 300)))
        with self.assertRaises(ValueError):
            aggregate(1, 1, 1, [0, 0], 0)
        with self.assertRaises(ValueError):
            aggregate(1, 1, 1, [0, 0, 0], 0, "mean")
        for mech in MECHS:
            for kind in ("noisy", "biased"):
                for rule in ("minmax", "median"):
                    for r in run_cases_arb(FEW, 15000, mechanism=mech,
                                           panel=make_panel(kind, noise_pct=20, bias=1, margin_pct=3,
                                                            seed=4, range_rule=rule)):
                        low, high = r["panelRangeCents"]
                        self.assertTrue(0 <= low <= r["panelPointCents"] <= high)
                        self.assertTrue(all(isinstance(x, int) for x in (low, high, r["panelPointCents"])))


class SeededAndDeterministic(unittest.TestCase):
    def test_same_seed_same_runs_different_seed_different_panel(self):
        for kind in ("noisy", "biased"):
            a = run_cases_arb(FEW, 15000, mechanism=decide.MECH_CLAMPED, panel=make_panel(kind, noise_pct=10, seed=7))
            b = run_cases_arb(FEW, 15000, mechanism=decide.MECH_CLAMPED, panel=make_panel(kind, noise_pct=10, seed=7))
            c = run_cases_arb(FEW, 15000, mechanism=decide.MECH_CLAMPED, panel=make_panel(kind, noise_pct=10, seed=8))
            self.assertEqual(a, b)
            self.assertNotEqual([r["panelPointCents"] for r in a], [r["panelPointCents"] for r in c])

    def test_noise_depends_on_seed_and_case_only_not_on_order_or_pairing(self):
        p = make_panel("noisy", noise_pct=10, seed=5)
        forward = run_cases_arb(FEW, 15000, panel=p)
        backward = run_cases_arb(list(reversed(FEW)), 15000, panel=p)
        key = lambda r: (r["caseId"], r["claimantStrength"], r["respondentStrength"])
        self.assertEqual(sorted(forward, key=key), sorted(backward, key=key))
        by_case = {}
        for r in forward:   # the noisy panel does not look at who is strong
            by_case.setdefault(r["caseId"], set()).add((r["panelPointCents"], tuple(r["panelRangeCents"])))
        self.assertTrue(all(len(v) == 1 for v in by_case.values()))


class FilingAndBias(unittest.TestCase):
    def test_weak_side_drops_its_weakest_claims_hand_example(self):
        case = load_case(CASE1)
        # claimant: k01(1 ev), k02(1 ev), k03(2 ev), k04(1 ev, $1,070). Weak keeps 2: k03 then k04.
        self.assertEqual([c.claim_id for c in filed_claims(case, "claimant", "weak")], ["clm_c01k03", "clm_c01k04"])
        self.assertEqual(len(filed_claims(case, "claimant", "strong")), 4)
        # respondent: k05(1, $650), k06(1, $300), k07(2, $120). Weak keeps 2: k07 then k05 (file order).
        self.assertEqual([c.claim_id for c in filed_claims(case, "respondent", "weak")], ["clm_c01k05", "clm_c01k07"])
        # volume = claims + distinct evidence cited
        self.assertEqual(filing_volume(filed_claims(case, "claimant", "strong")), 4 + 3)
        self.assertEqual(filing_volume(filed_claims(case, "claimant", "weak")), 2 + 3)
        self.assertEqual(filing_volume(filed_claims(case, "respondent", "weak")), 2 + 2)
        self.assertEqual(filing_volume(filed_claims(case, "respondent", "strong")), 3 + 2)
        self.assertEqual(len(filed_claims(case, "claimant", "strong", cap=1)), 1)
        self.assertEqual(contested_cents(case, load_answer(CASE1)), 107000)

    def test_bias_shift_hand_example_and_direction(self):
        self.assertEqual(BIAS_SCALE_PCT, 20)
        # strong claimant (7) vs weak respondent (4), bias 1, contested $1,070:
        # (7 - 4) / 11 * 20% * 107,000 = 5,836.36 cents toward the claimant
        self.assertAlmostEqual(bias_shift_cents(7, 4, 1.0, 107000), 5836.3636, places=3)
        self.assertEqual(bias_shift_cents(4, 4, 1.0, 107000), 0.0)
        self.assertEqual(bias_shift_cents(0, 0, 1.0, 107000), 0.0)
        self.assertLess(bias_shift_cents(2, 6, 0.5, 100000), 0)
        case, ans = load_case(CASE1), load_answer(CASE1)
        p = make_panel("biased", noise_pct=0, bias=1.0)
        self.assertEqual(p.find(case, ans, "strong", "weak"), (95000 + 5836, (95000 + 5836, 95000 + 5836)))
        self.assertEqual(make_panel("biased", noise_pct=0, bias=0).find(case, ans, "strong", "weak"),
                         (95000, (95000, 95000)))
        # equal filing: strength no longer changes the pull
        eq = make_panel("biased", noise_pct=0, bias=1.0, equal_filing=True)
        self.assertEqual(eq.find(case, ans, "strong", "weak"), eq.find(case, ans, "weak", "strong"))

    def test_noisy_panel_ignores_bias_and_bad_settings_are_refused(self):
        self.assertEqual(make_panel("noisy", bias=1.0).bias, 0.0)
        for kw in ({"noise_pct": -1}, {"bias": -0.5}, {"margin_pct": -1}, {"range_rule": "x"},
                   {"filing_cap": 0}):
            with self.assertRaises(ValueError):
                make_panel("biased", **kw)
        with self.assertRaises(ValueError):
            SimPanel("wise")


class Recorder:
    def __init__(self, inner, log):
        self.inner, self.log, self.name = inner, log, inner.name

    def reply(self, system, messages):
        text = self.inner.reply(system, messages)
        self.log.append((system, json.dumps(messages, sort_keys=True), text))
        return text


class PanelNeverReachesPrompts(unittest.TestCase):
    def test_prompts_and_offers_are_identical_under_every_panel(self):
        """Panel noise is harness-side scoring only: every prompt, message and offer is the same
        whatever the panel, so nothing from it (or from answer.json) can reach an agent."""
        panels = [None, make_panel("noisy", noise_pct=20, seed=1), make_panel("noisy", noise_pct=5, seed=2),
                  make_panel("biased", noise_pct=10, bias=1, seed=3, margin_pct=5, range_rule="median")]
        for mech in MECHS:
            seen = set()
            for panel in panels:
                log = []
                runs = run_cases_arb(ALL_CASES, 15000, lambda s: Recorder(ScriptedProvider(s), log),
                                     mechanism=mech, panel=panel)
                offers = tuple((r["claimantOfferCents"], r["respondentOfferCents"]) for r in runs)
                seen.add((tuple(log), offers))
                for system, msgs, _ in log:
                    self.assertEqual(json.loads(msgs), [{"role": "user", "content": SEALED_KICKOFF}])
                    for needle in ("answer.json", "owedCents", "lowCents", "highCents", "panelPoint",
                                   "noise", "seed"):
                        self.assertNotIn(needle, system)
            self.assertEqual(len(seen), 1, mech)


class Metrics(unittest.TestCase):
    def test_extra_metrics(self):
        runs = [{"outcome": "deal", "reason": "clamped", "absErrorCents": 100},
                {"outcome": "deal", "reason": "clamped", "absErrorCents": 300},
                {"outcome": "no_deal", "reason": "human_review", "absErrorCents": 0},
                {"outcome": "no_deal", "reason": "human_review", "absErrorCents": 0}]
        self.assertEqual(extra_metrics(runs), {"runs": 4, "humanReviewRate": 0.5,
                                               "meanAbsErrorAwardedCents": 200.0})
        self.assertEqual(extra_metrics([])["humanReviewRate"], 0.0)


class Cli(unittest.TestCase):
    def test_cli_simulated_panel_labels_and_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "n.json")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main(["--mechanism", "arb-clamped", "--panel", "biased", "--noise-pct", "5",
                             "--bias", "1", "--seed", "2", "--cases", "1,14", "--out", out])
            self.assertEqual(code, 0)
            self.assertIn("SIMULATED PANEL, not a real result", buf.getvalue())
            self.assertIn("Human review rate", buf.getvalue())
            with open(out) as f:
                doc = json.load(f)
            self.assertEqual(doc["meta"]["notARealResult"], SIM_LABEL)
            self.assertEqual(doc["meta"]["panel"]["kind"], "biased")
            self.assertEqual((doc["meta"]["panel"]["noisePct"], doc["meta"]["panel"]["bias"],
                              doc["meta"]["panel"]["seed"]), (5.0, 1.0, 2))
            self.assertIn("humanReviewRate", doc["summary"]["panelMetrics"])
            self.assertTrue(all(r["panel"] == "biased" for r in doc["runs"]))

    def test_cli_default_panel_is_perfect_and_haggle_refuses_panel_flags(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "p.json")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--mechanism", "arb-clamped", "--cases", "1", "--out", out]), 0)
            with open(out) as f:
                doc = json.load(f)
            self.assertEqual(doc["meta"]["panel"]["kind"], "perfect")
            self.assertTrue(all("panel" not in r for r in doc["runs"]))
        for argv in (["--panel", "noisy"], ["--seed", "3"], ["--mechanism", "haggle", "--bias", "1"]):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv + ["--cases", "1"]), 2)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--mechanism", "arb-final", "--panel", "noisy", "--noise-pct", "-1",
                                   "--cases", "1"]), 2)


if __name__ == "__main__":
    unittest.main()
