import builtins
import json
import os
import unittest

from harness import config
from harness.case_loader import list_case_dirs, load_case
from harness.haggle import haggle
from harness.prompts import build_messages, build_system_prompt, is_shared
from harness.protocol import parse_action
from harness.providers import AnthropicProvider, OpenAIProvider, ProviderError, ScriptedProvider
from harness.run import DEFAULT_DISPUTES, run_cases
from harness.scoring import load_answer, score_run, strength_gaps

ALL_CASES = list_case_dirs(DEFAULT_DISPUTES, "all")
THREE = list_case_dirs(DEFAULT_DISPUTES, "1,8,26")


class Recorder:
    """Wraps a provider and records every (system, messages) it is asked to answer."""
    def __init__(self, inner, log):
        self.inner, self.log, self.name = inner, log, inner.name

    def reply(self, system, messages):
        self.log.append((system, list(messages)))
        return self.inner.reply(system, messages)


class NoAnswerKeyLeak(unittest.TestCase):
    def test_answer_json_never_in_any_prompt(self):
        """Build the prompts for every case and haggle over them; check nothing from answer.json shows up."""
        for case_dir in ALL_CASES:
            ans = load_answer(case_dir)
            case = load_case(case_dir)
            seen = []
            for role in ("claimant", "respondent"):
                seen.append(build_system_prompt(case, role, config.MAX_ROUNDS))
            log = []
            haggle(case, Recorder(ScriptedProvider("strong"), log), Recorder(ScriptedProvider("weak"), log), 6)
            for system, msgs in log:
                seen.append(system)
                seen.extend(m["content"] for m in msgs)
            blob = "\n".join(seen)
            for needle in ("answer.json", "owedCents", "lowCents", "highCents", "infoAsymmetry",
                           ans["reasoning"], case.dir_name):
                self.assertNotIn(needle, blob, "%s leaked into a prompt for %s" % (needle[:30], case.dir_name))

    def test_loader_never_opens_answer_json(self):
        opened = []
        real_open = builtins.open

        def spy(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)

        builtins.open = spy
        try:
            for d in THREE:
                case = load_case(d)
                build_system_prompt(case, "claimant", 6)
                build_system_prompt(case, "respondent", 6)
        finally:
            builtins.open = real_open
        self.assertTrue(opened)
        self.assertFalse([p for p in opened if p.endswith("answer.json")])

    def test_private_evidence_stays_private(self):
        for case_dir in ALL_CASES:
            case = load_case(case_dir)
            for role, other in (("claimant", "respondent"), ("respondent", "claimant")):
                prompt = build_system_prompt(case, role, 6)
                other_pid = case.party_id(other)
                for ev in case.evidence.values():
                    if ev.party_id == other_pid and not is_shared(case, ev.evidence_id):
                        self.assertNotIn("DOCUMENT %s " % ev.evidence_id, prompt)
                for c in case.claims:
                    if c.party_id == other_pid:
                        self.assertNotIn("CLAIM %s " % c.claim_id, prompt)
                # the contract is always shared
                self.assertIn("DOCUMENT %s " % case.contract_id, prompt)

    def test_agents_never_see_other_model(self):
        case = load_case(THREE[0])
        blob = build_system_prompt(case, "claimant", 6)
        for word in ("scripted", "weak", "strong", "claude", "gpt"):
            self.assertNotIn(word, blob.lower().replace("strongly", ""))


class OfferParsing(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_action("Fair price.\nOFFER: 95000"), ("offer", 95000))
        self.assertEqual(parse_action("OFFER: 0"), ("offer", 0))
        self.assertEqual(parse_action("ok\nOFFER: 95,000\n\n"), ("offer", 95000))
        self.assertEqual(parse_action("Deal.\nACCEPT"), ("accept", None))
        self.assertEqual(parse_action("  ACCEPT  "), ("accept", None))

    def test_invalid(self):
        for bad in ("", "no line here", "OFFER: $950", "OFFER: 950.50", "OFFER: abc", "offer: 5",
                    "OFFER: 5000\nthanks!", "I ACCEPT", "ACCEPT it", "OFFER:"):
            self.assertIsNone(parse_action(bad), bad)

    def test_only_last_line_counts(self):
        self.assertEqual(parse_action("OFFER: 100\nOFFER: 200"), ("offer", 200))


class ScoringMath(unittest.TestCase):
    def run_rec(self, case_id, cs, rs, deal, owed=100000, friction=10000):
        r = {"caseId": case_id, "claimantStrength": cs, "respondentStrength": rs}
        r.update(score_run(owed, deal, friction))
        return r

    def test_score_run(self):
        s = score_run(100000, 110000, 10000)
        self.assertEqual((s["errorCents"], s["gainer"], s["claimantGainCents"], s["respondentGainCents"]),
                         (10000, "claimant", 10000, -10000))
        s = score_run(100000, 80000, 10000)
        self.assertEqual((s["errorCents"], s["gainer"]), (-20000, "respondent"))
        self.assertEqual(score_run(100000, 100000, 10000)["gainer"], "even")
        n = score_run(100000, None, 10000)   # court: error 0, both pay friction
        self.assertEqual((n["errorCents"], n["gainer"], n["claimantGainCents"], n["respondentGainCents"]),
                         (0, "none", -10000, -10000))

    def test_strength_gap_hand_computed(self):
        # owed 100000, friction 10000. Case A deals: (c,r) -> deal
        #   (weak,weak) 90000, (strong,weak) 110000, (weak,strong) 80000, (strong,strong) 95000
        # claimant gains (= deal - owed):  WW -10000, SW +10000, WS -20000, SS -5000
        #   claimant gap vs weak resp  = +10000 - -10000 = 20000
        #   claimant gap vs strong resp= -5000 - -20000  = 15000
        # respondent gains (= -(deal - owed)): WW +10000, SW -10000, WS +20000, SS +5000
        #   respondent gap vs weak claimant   = WS - WW = 20000 - 10000 = 10000
        #   respondent gap vs strong claimant = SS - SW = 5000 - -10000 = 15000
        # Case B: only (strong,strong) has no deal; others deal at owed.
        #   claimant gains: WW 0, SW 0, WS 0, SS -10000 (friction)
        #   claimant gap vs weak = 0, vs strong = -10000 - 0 = -10000
        #   respondent gains: WW 0, SW 0, WS 0, SS -10000
        #   respondent gap vs weak claimant = 0, vs strong claimant = SS - SW = -10000
        runs = []
        for cs, rs, deal in (("weak", "weak", 90000), ("strong", "weak", 110000),
                             ("weak", "strong", 80000), ("strong", "strong", 95000)):
            runs.append(self.run_rec("A", cs, rs, deal))
        for cs, rs, deal in (("weak", "weak", 100000), ("strong", "weak", 100000),
                             ("weak", "strong", 100000), ("strong", "strong", None)):
            runs.append(self.run_rec("B", cs, rs, deal))
        g = strength_gaps(runs)
        # claimant: mean of (20000, 15000, 0, -10000) = 6250; vs weak = 10000; vs strong = 2500
        self.assertEqual(g["claimant"]["meanCents"], 6250)
        self.assertEqual(g["claimant"]["vsWeakOpponentCents"], 10000)
        self.assertEqual(g["claimant"]["vsStrongOpponentCents"], 2500)
        # respondent: mean of (10000, 15000, 0, -10000) = 3750; vs weak = 5000; vs strong = 2500
        self.assertEqual(g["respondent"]["meanCents"], 3750)
        self.assertEqual(g["respondent"]["vsWeakOpponentCents"], 5000)
        self.assertEqual(g["respondent"]["vsStrongOpponentCents"], 2500)
        self.assertEqual(g["claimant"]["n"], 4)


class HaggleLoop(unittest.TestCase):
    class Fixed:
        def __init__(self, *replies):
            self.replies, self.name, self.i = list(replies), "fixed", 0

        def reply(self, system, messages):
            r = self.replies[min(self.i, len(self.replies) - 1)]
            self.i += 1
            return r

    def setUp(self):
        self.case = load_case(THREE[0])

    def test_accept_ends_with_last_offer(self):
        res = haggle(self.case, self.Fixed("hi\nOFFER: 90000"), self.Fixed("fine\nACCEPT"), 6)
        self.assertEqual((res.outcome, res.deal_cents, res.accepted_by, res.turns), ("deal", 90000, "respondent", 2))

    def test_round_cap_is_no_deal(self):
        res = haggle(self.case, self.Fixed("OFFER: 90000"), self.Fixed("OFFER: 10000"), 3)
        self.assertEqual((res.outcome, res.reason, res.turns), ("no_deal", "round_cap", 6))

    def test_accept_with_nothing_on_table_is_invalid(self):
        res = haggle(self.case, self.Fixed("ACCEPT"), self.Fixed("OFFER: 1"), 3)
        self.assertEqual((res.outcome, res.reason), ("no_deal", "invalid_accept"))

    def test_malformed_reply_gets_one_retry(self):
        res = haggle(self.case, self.Fixed("no line", "OFFER: 500"), self.Fixed("ACCEPT"), 3)
        self.assertEqual((res.outcome, res.deal_cents), ("deal", 500))
        res = haggle(self.case, self.Fixed("bad", "still bad"), self.Fixed("ACCEPT"), 3)
        self.assertEqual((res.outcome, res.reason), ("no_deal", "invalid_reply"))

    def test_messages_alternate_and_start_with_user(self):
        t = [{"role": "claimant", "text": "a\nOFFER: 1"}, {"role": "respondent", "text": "b\nOFFER: 2"}]
        for role in ("claimant", "respondent"):
            msgs = build_messages(role, t if role == "claimant" else t[:1])
            self.assertEqual(msgs[0]["role"], "user")
            for a, b in zip(msgs, msgs[1:]):
                self.assertNotEqual(a["role"], b["role"])


class Providers(unittest.TestCase):
    def test_missing_keys_raise_clear_error(self):
        saved = {v: os.environ.pop(v, None) for v in config.API_KEY_ENV.values()}
        try:
            for cls, var in ((AnthropicProvider, "ANTHROPIC_API_KEY"), (OpenAIProvider, "OPENAI_API_KEY")):
                with self.assertRaises(ProviderError) as cm:
                    cls("strong")
                self.assertIn(var, str(cm.exception))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_default_models(self):
        self.assertEqual(config.MODELS["anthropic"]["strong"], os.environ.get("ARB_ANTHROPIC_STRONG_MODEL", "claude-opus-5-5"))
        self.assertEqual(config.MODELS["anthropic"]["weak"], os.environ.get("ARB_ANTHROPIC_WEAK_MODEL", "claude-haiku-4-5"))


class FullScriptedRun(unittest.TestCase):
    def test_three_cases_deterministic(self):
        a = run_cases(THREE, "scripted", config.MAX_ROUNDS, config.COURT_FRICTION_CENTS)
        b = run_cases(THREE, "scripted", config.MAX_ROUNDS, config.COURT_FRICTION_CENTS)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(len(a), 12)   # 3 cases x 4 pairings
        self.assertEqual({(r["claimantStrength"], r["respondentStrength"]) for r in a},
                         {("weak", "weak"), ("weak", "strong"), ("strong", "weak"), ("strong", "strong")})
        for r in a:
            self.assertIn(r["outcome"], ("deal", "no_deal"))
            if r["outcome"] == "deal":
                self.assertEqual(r["errorCents"], r["dealCents"] - r["owedCents"])

    def test_cli_refuses_paid_provider_without_flag(self):
        from harness.run import main
        self.assertEqual(main(["--provider", "anthropic", "--cases", "1"]), 2)


if __name__ == "__main__":
    unittest.main()
