"""Offline cost estimate for a paid run. No network, no API key, no SDK.

Model of a run: every case x every pairing (weak/strong x weak/strong) x rounds x 2 sides is one
API call. Nobody accepts early and there are no retries, so this is the full-length case.
  - Prompt size: the real system prompt built for that case and side, plus the growing chat
    history, at config.CHARS_PER_TOKEN characters per token.
  - Output size: every reply is `share` of that model's max_tokens cap. Worst case share = 1.0.
    Typical share = config.TYPICAL_OUTPUT_SHARE. Replies in the history are counted at the same
    size (thinking tokens are not sent back, so this over-counts the history a little).
"""
from typing import List

from . import config
from .case_loader import load_case
from .cost import cost_usd
from .prompts import KICKOFF, build_system_prompt
from .scoring import STRENGTHS


def _tok(text: str) -> int:
    return -(-len(text) // config.CHARS_PER_TOKEN)


def estimate(case_dirs: List[str], rounds: int, provider: str = "anthropic") -> dict:
    models = config.MODELS[provider]
    if any(models[s] not in config.PRICES_USD_PER_MTOK or models[s] not in config.MODEL_PARAMS
           for s in STRENGTHS):
        raise ValueError("no price/max_tokens configured for every %s model" % provider)
    out = {}
    for label, share in (("worst", 1.0), ("typical", config.TYPICAL_OUTPUT_SHARE)):
        tot = {"calls": 0, "inputTokens": 0, "outputTokens": 0, "usd": 0.0, "byModel": {}}
        for case_dir in case_dirs:
            case = load_case(case_dir)
            sys_tok = {r: _tok(build_system_prompt(case, r, rounds)) for r in ("claimant", "respondent")}
            for cs in STRENGTHS:
                for rs in STRENGTHS:
                    model_of = {"claimant": models[cs], "respondent": models[rs]}
                    history = 0                                   # tokens of replies so far
                    for _ in range(rounds):
                        for role in ("claimant", "respondent"):
                            model = model_of[role]
                            n_in = sys_tok[role] + history + (_tok(KICKOFF) if role == "claimant" else 0)
                            n_out = int(config.MODEL_PARAMS[model]["max_tokens"] * share)
                            c = cost_usd(model, n_in, n_out)
                            tot["calls"] += 1
                            tot["inputTokens"] += n_in
                            tot["outputTokens"] += n_out
                            tot["usd"] += c
                            m = tot["byModel"].setdefault(model, {"calls": 0, "usd": 0.0})
                            m["calls"] += 1
                            m["usd"] += c
                            history += n_out
        out[label] = tot
    out["cases"] = len(case_dirs)
    out["rounds"] = rounds
    return out


def format_estimate(est: dict, provider: str, max_usd=None) -> str:
    n = est["cases"]
    lines = ["Cost estimate (%s): %d cases x 4 pairings x %d rounds x 2 sides = %d calls, no retries, "
             "no early accepts. Offline; nothing was sent." % (provider, n, est["rounds"], est["worst"]["calls"])]
    for label, title in (("worst", "Worst case (every reply at its max_tokens cap)"),
                         ("typical", "Typical (every reply at %d%% of its cap)" % round(config.TYPICAL_OUTPUT_SHARE * 100))):
        e = est[label]
        parts = ", ".join("%s $%.2f" % (k, v["usd"]) for k, v in e["byModel"].items())
        lines.append("  %-46s $%8.2f   (in %s tok, out %s tok; %s)" % (
            title + ":", e["usd"], format(e["inputTokens"], ","), format(e["outputTokens"], ","), parts))
    if max_usd is not None:
        lines.append("  --max-usd $%.2f %s the worst case." % (
            max_usd, "covers" if max_usd >= est["worst"]["usd"] else "is BELOW"))
    lines.append("  Early accepts lower the real spend; retries and a real tokenizer that counts more than "
                 "%d chars per token raise it." % config.CHARS_PER_TOKEN)
    return "\n".join(lines)
