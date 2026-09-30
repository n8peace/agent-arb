"""Tests for the paid-model path. No network, no API key: the SDK client is mocked."""
import contextlib
import io
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace

from harness import config
from harness.case_loader import list_case_dirs, load_case
from harness.cost import BudgetExceeded, CostTracker, cost_usd
from harness.estimate import estimate
from harness.haggle import haggle
from harness.providers import AnthropicProvider, ProviderError
from harness.run import DEFAULT_DISPUTES, REPO_ROOT, main, run_cases

OPUS, HAIKU = "claude-opus-5-5", "claude-haiku-4-5"


class FakeSDK:
    """Stands in for the `anthropic` module: same exception hierarchy as the real one."""
    class APIError(Exception):
        pass

    class APIStatusError(APIError):
        def __init__(self, msg="status", status_code=500):
            super().__init__(msg)
            self.status_code = status_code

    class RateLimitError(APIStatusError):
        pass

    class APIConnectionError(APIError):
        pass


def resp(text="ok\nOFFER: 500", stop="end_turn", inp=100, out=50, blocks=None):
    content = blocks if blocks is not None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(stop_reason=stop, content=content,
                           usage=SimpleNamespace(input_tokens=inp, output_tokens=out))


class FakeClient:
    """client.messages.create(**kw) returns queued responses (or raises queued exceptions)."""
    def __init__(self, *queue):
        self.queue, self.calls = list(queue), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        item = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        if isinstance(item, Exception):
            raise item
        return item


def provider(model, client, tracker=None, sleeps=None):
    return AnthropicProvider("strong", model=model, tracker=tracker, client=client, sdk=FakeSDK,
                             sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


MSGS = [{"role": "user", "content": "Begin the negotiation. You speak first."}]


class RequestShape(unittest.TestCase):
    FORBIDDEN = ("thinking", "temperature", "top_p", "top_k", "budget_tokens")

    def test_opus_request(self):
        c = FakeClient(resp())
        provider(OPUS, c).reply("SYS", MSGS)
        kw = c.calls[0]
        for k in self.FORBIDDEN:
            self.assertNotIn(k, kw)
        self.assertNotIn("budget_tokens", str(kw))
        self.assertEqual(kw["output_config"], {"effort": "low"})
        self.assertEqual(kw["max_tokens"], 4000)
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual(kw["messages"][-1]["role"], "user")      # no assistant prefill

    def test_haiku_request_has_no_effort(self):
        c = FakeClient(resp())
        provider(HAIKU, c).reply("SYS", MSGS)
        kw = c.calls[0]
        for k in self.FORBIDDEN + ("output_config", "effort"):
            self.assertNotIn(k, kw)
        self.assertEqual(kw["max_tokens"], 700)
        self.assertEqual(kw["model"], "claude-haiku-4-5")
        self.assertEqual(kw["messages"][-1]["role"], "user")

    def test_default_models_and_prices(self):
        self.assertEqual(config.MODELS["anthropic"]["weak"],
                         os.environ.get("ARB_ANTHROPIC_WEAK_MODEL", "claude-haiku-4-5"))
        self.assertEqual(config.MODELS["anthropic"]["strong"],
                         os.environ.get("ARB_ANTHROPIC_STRONG_MODEL", "claude-opus-5-5"))
        self.assertEqual(config.PRICES_USD_PER_MTOK[OPUS], {"input": 4.00, "output": 20.00})
        self.assertEqual(config.PRICES_USD_PER_MTOK[HAIKU], {"input": 1.00, "output": 5.00})
        self.assertAlmostEqual(cost_usd(OPUS, 1_000_000, 1_000_000), 24.0)
        self.assertAlmostEqual(cost_usd(HAIKU, 1_000_000, 1_000_000), 6.0)

    def test_unknown_model_is_refused(self):
        with self.assertRaises(ProviderError):
            AnthropicProvider("strong", model="claude-mystery-9", client=FakeClient(resp()), sdk=FakeSDK)

    def test_only_text_blocks_are_read(self):
        blocks = [SimpleNamespace(type="thinking", thinking="hmm OFFER: 1"),
                  SimpleNamespace(type="text", text="fine.\n"),
                  SimpleNamespace(type="text", text="OFFER: 750")]
        self.assertEqual(provider(OPUS, FakeClient(resp(blocks=blocks))).reply("S", MSGS), "fine.\nOFFER: 750")


class Malformed(unittest.TestCase):
    def setUp(self):
        self.case = load_case(list_case_dirs(DEFAULT_DISPUTES, "1")[0])
        self.accept = SimpleNamespace(name="acc", reply=lambda s, m: "fine\nACCEPT")

    def test_refusal_and_max_tokens_are_malformed_then_retried(self):
        for stop in ("refusal", "max_tokens"):
            t = CostTracker(10)
            c = FakeClient(resp("I cannot help with that.", stop=stop), resp("OFFER: 500"))
            res = haggle(self.case, provider(HAIKU, c, t), self.accept, 3)
            self.assertEqual((res.outcome, res.deal_cents), ("deal", 500), stop)
            self.assertEqual(len(c.calls), 2, stop)                  # exactly one retry
            self.assertEqual(t.malformed, 1)
            self.assertEqual(t.calls, 2)                             # the bad reply was still paid for

    def test_truncated_offer_does_not_parse(self):
        # max_tokens cut the reply off mid-number: "OFFER: 5" must NOT be read as an offer
        c = FakeClient(resp("thinking...\nOFFER: 5", stop="max_tokens"),
                       resp("still cut\nOFFER: 9", stop="max_tokens"))
        res = haggle(self.case, provider(OPUS, c), self.accept, 3)
        self.assertEqual((res.outcome, res.reason), ("no_deal", "invalid_reply"))

    def test_refusal_text_returns_empty(self):
        self.assertEqual(provider(OPUS, FakeClient(resp("no", stop="refusal"))).reply("S", MSGS), "")


class Errors(unittest.TestCase):
    def test_rate_limit_sleeps_and_retries(self):
        sleeps = []
        c = FakeClient(FakeSDK.RateLimitError("429", 429), FakeSDK.RateLimitError("429", 429), resp())
        out = provider(HAIKU, c, sleeps=sleeps).reply("S", MSGS)
        self.assertEqual(out, "ok\nOFFER: 500")
        self.assertEqual(len(c.calls), 3)
        self.assertEqual(len(sleeps), 2)
        self.assertTrue(all(s > 0 for s in sleeps))

    def test_rate_limit_gives_up_after_three_retries(self):
        sleeps = []
        c = FakeClient(FakeSDK.RateLimitError("429", 429))
        with self.assertRaises(ProviderError):
            provider(HAIKU, c, sleeps=sleeps).reply("S", MSGS)
        self.assertEqual(len(c.calls), 4)     # first try + 3 retries
        self.assertEqual(len(sleeps), 3)

    def test_status_and_connection_errors_are_not_retried(self):
        for exc, word in ((FakeSDK.APIStatusError("bad request", 400), "400"),
                          (FakeSDK.APIConnectionError("dns"), "network")):
            c = FakeClient(exc)
            with self.assertRaises(ProviderError) as cm:
                provider(OPUS, c).reply("S", MSGS)
            self.assertIn(word, str(cm.exception))
            self.assertEqual(len(c.calls), 1)

    def test_real_sdk_exception_order(self):
        try:
            import anthropic
        except ImportError:
            self.skipTest("anthropic not installed (pip install -r harness/requirements.txt)")
        self.assertTrue(issubclass(anthropic.RateLimitError, anthropic.APIStatusError))
        self.assertFalse(issubclass(anthropic.APIConnectionError, anthropic.APIStatusError))


class Budget(unittest.TestCase):
    def test_stops_before_the_call_that_would_exceed(self):
        # call 1 costs exactly $0.25; the limit leaves less room than call 2's worst case
        first = cost_usd(HAIKU, 100_000, 30_000)
        worst_next = cost_usd(HAIKU, 1, config.MODEL_PARAMS[HAIKU]["max_tokens"])
        limit = first + worst_next / 2
        t = CostTracker(limit)
        c = FakeClient(resp(inp=100_000, out=30_000))
        p = provider(HAIKU, c, t)
        p.reply("S", MSGS)
        self.assertAlmostEqual(t.spent_usd, first)
        with self.assertRaises(BudgetExceeded):
            p.reply("S", MSGS)
        self.assertEqual(len(c.calls), 1)                 # the second request was never sent
        self.assertAlmostEqual(t.spent_usd, first)        # and nothing more was spent

    def test_call_that_fits_is_allowed(self):
        t = CostTracker(1.00)
        c = FakeClient(resp(inp=1000, out=100))
        provider(OPUS, c, t).reply("S", MSGS)
        self.assertEqual(len(c.calls), 1)
        self.assertAlmostEqual(t.spent_usd, cost_usd(OPUS, 1000, 100))

    def test_run_stops_cleanly_and_keeps_partial_results(self):
        cases = list_case_dirs(DEFAULT_DISPUTES, "1-2")
        client = FakeClient(resp("hi\nOFFER: 500", inp=2000, out=200))
        tracker = CostTracker(0.05)
        calls_done = lambda: len(client.calls)

        def factory(kind, strength):
            return provider(HAIKU, client, tracker)

        runs = run_cases(cases, "anthropic", 2, config.COURT_FRICTION_CENTS,
                         provider_factory=factory, tracker=tracker)
        self.assertTrue(tracker.stop_reason.startswith("budget"))
        self.assertLess(len(runs), 8)                     # 2 cases x 4 pairings would be 8
        self.assertLessEqual(tracker.spent_usd, 0.05)     # never past the limit
        self.assertEqual(calls_done(), tracker.calls)
        for r in runs:
            self.assertIn("usage", r)
        self.assertEqual(tracker.report()["stoppedEarly"], tracker.stop_reason)

    def test_provider_error_saves_partial_when_tracked(self):
        cases = list_case_dirs(DEFAULT_DISPUTES, "1")
        tracker = CostTracker(5)
        client = FakeClient(FakeSDK.APIStatusError("boom", 500))
        runs = run_cases(cases, "anthropic", 2, 15000, tracker=tracker,
                         provider_factory=lambda k, s: provider(HAIKU, client, tracker))
        self.assertEqual(runs, [])
        self.assertTrue(tracker.stop_reason.startswith("error"))


class CliAndEstimate(unittest.TestCase):
    def test_paid_run_needs_max_usd(self):
        self.assertEqual(main(["--provider", "anthropic", "--cases", "1",
                               config.PAID_CONFIRM_FLAG]), 2)
        self.assertEqual(main(["--provider", "anthropic", "--cases", "1", "--max-usd", "0",
                               config.PAID_CONFIRM_FLAG]), 2)

    def test_missing_key_fails_before_any_output_file(self):
        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        out = os.path.join(REPO_ROOT, "results", "_should_not_exist.json")
        try:
            self.assertEqual(main(["--provider", "anthropic", "--cases", "1", "--max-usd", "5",
                                   config.PAID_CONFIRM_FLAG, "--out", out]), 2)
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved
        self.assertFalse(os.path.exists(out))

    def test_openai_is_refused(self):
        self.assertEqual(main(["--provider", "openai", "--cases", "1", "--max-usd", "5",
                               config.PAID_CONFIRM_FLAG]), 2)

    def test_estimate_shape(self):
        cases = list_case_dirs(DEFAULT_DISPUTES, "1-3")
        e = estimate(cases, 6)
        self.assertEqual(e["worst"]["calls"], 3 * 4 * 6 * 2)
        self.assertGreater(e["worst"]["usd"], e["typical"]["usd"])
        # output tokens are exactly the caps: 4 pairings x 6 rounds x 3 cases, one turn per side
        n = 3 * 6
        opus_calls = n * 4                      # each pairing has 2 sides; half the sides are strong
        self.assertEqual(e["worst"]["outputTokens"], opus_calls * 4000 + opus_calls * 700)
        self.assertEqual(e["typical"]["outputTokens"], opus_calls * 1600 + opus_calls * 280)
        self.assertEqual(set(e["worst"]["byModel"]), {OPUS, HAIKU})

    def test_estimate_cli_needs_no_key_and_no_flags(self):
        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["--estimate", "--cases", "1"]), 0)
            self.assertIn("Worst case", out.getvalue())
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved


class ScriptedNeedsNoSDK(unittest.TestCase):
    def test_scripted_and_estimate_never_import_anthropic(self):
        code = (
            "import sys\n"
            "sys.modules['anthropic'] = None          # any 'import anthropic' now raises ImportError\n"
            "from harness.run import main\n"
            "assert main(['--provider','scripted','--cases','1-2','--out','%s']) == 0\n"
            "assert main(['--estimate','--cases','1']) == 0\n"
            "assert sys.modules['anthropic'] is None\n"
            "print('OK')\n"
        ) % os.path.join(REPO_ROOT, "results", "_nosdk_test.json")
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
