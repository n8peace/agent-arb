"""CLI: python3 -m harness.run --provider scripted --cases all --out results/<timestamp>.json"""
import argparse
import datetime
import json
import os
import sys
from typing import List, Optional

from . import config
from .case_loader import list_case_dirs, load_case
from .haggle import haggle
from .providers import ProviderError, make_provider
from .scoring import STRENGTHS, load_answer, score_run, summarize

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DISPUTES = os.path.join(REPO_ROOT, "bench", "disputes")
ARB_LABEL = ("UPPER BOUND, not a real result: arb mechanisms use answer.json (owedCents, lowCents, "
             "highCents) as a perfect fact panel. Offers are sealed and scripted.")
MECHANISM_CHOICES = ["haggle", "arb-final", "arb-clamped", "arb"]
MECHANISM_ALIASES = {"arb": "arb-final"}


def run_cases(case_dirs: List[str], provider_kind: str, max_rounds: int, friction_cents: int,
              provider_factory=None) -> List[dict]:
    """Run all 4 strength pairings for each case and score them. Deterministic for scripted."""
    factory = provider_factory or make_provider
    runs: List[dict] = []
    for case_dir in case_dirs:
        case = load_case(case_dir)
        owed = load_answer(case_dir)["owedCents"]   # scoring only; agents never see this
        for cs in STRENGTHS:
            for rs in STRENGTHS:
                claimant, respondent = factory(provider_kind, cs), factory(provider_kind, rs)
                res = haggle(case, claimant, respondent, max_rounds)
                rec = {
                    "caseId": case.case_id,
                    "caseDir": case.dir_name,
                    "kind": case.kind,
                    "claimantStrength": cs,
                    "respondentStrength": rs,
                    "claimantModel": claimant.name,
                    "respondentModel": respondent.name,
                    "owedCents": owed,
                    "outcome": res.outcome,
                    "reason": res.reason,
                    "acceptedBy": res.accepted_by,
                    "turns": res.turns,
                }
                rec.update(score_run(owed, res.deal_cents, friction_cents))
                rec["transcript"] = res.transcript
                runs.append(rec)
    return runs


def format_summary(summary: dict, n_cases: int, friction_cents: int) -> str:
    def usd(c):
        return "%+.2f" % (c / 100.0)
    lines = []
    lines.append("Strength-gap summary: %d cases x 4 pairings (friction per side: $%.2f)" % (
        n_cases, friction_cents / 100.0))
    lines.append("")
    lines.append("%-16s %5s %9s %12s %12s %14s %14s" % (
        "claimant/resp.", "runs", "deal rate", "mean err $", "mean |err| $", "claimant gain", "resp. gain"))
    for key, row in summary["pairings"].items():
        lines.append("%-16s %5d %8.0f%% %12s %12.2f %14s %14s" % (
            key, row["runs"], row["dealRate"] * 100, usd(row["meanErrorCents"]),
            row["meanAbsErrorCents"] / 100.0, usd(row["meanClaimantGainCents"]),
            usd(row["meanRespondentGainCents"])))
    g = summary["strengthGap"]
    lines.append("")
    lines.append("Strength gap (avg swing in that side's favor when its agent goes weak -> strong, $):")
    for side in ("claimant", "respondent"):
        v = g[side]
        lines.append("  %-10s %9s   (vs weak opponent %s, vs strong opponent %s, n=%d)" % (
            side, usd(v["meanCents"]), usd(v["vsWeakOpponentCents"]), usd(v["vsStrongOpponentCents"]), v["n"]))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m harness.run", description=__doc__)
    ap.add_argument("--provider", choices=["scripted", "anthropic", "openai"], default="scripted")
    ap.add_argument("--cases", default="all", help="'all', or e.g. '1,7,12-15'")
    ap.add_argument("--out", default=None, help="output JSON path (default results/<timestamp>.json)")
    ap.add_argument("--mechanism", choices=MECHANISM_CHOICES, default="haggle",
                    help="haggle = open agent-vs-agent baseline (default). arb-final = Agent Arb final-offer "
                         "rule; arb-clamped = final-offer clamped to the panel's range, panel amount when the "
                         "range is narrow. arb is an alias for arb-final. Both arb modes use sealed offers and "
                         "a perfect-panel stand-in; scripted only, an UPPER BOUND, not a real result")
    ap.add_argument("--rounds", type=int, default=config.MAX_ROUNDS)
    ap.add_argument("--friction-cents", type=int, default=config.COURT_FRICTION_CENTS)
    ap.add_argument("--disputes-dir", default=DEFAULT_DISPUTES)
    ap.add_argument(config.PAID_CONFIRM_FLAG, dest="confirm_paid", action="store_true",
                    help="required for anthropic/openai: acknowledges that real runs cost money")
    args = ap.parse_args(argv)
    args.mechanism = MECHANISM_ALIASES.get(args.mechanism, args.mechanism)
    is_arb = args.mechanism != "haggle"

    if args.provider != "scripted" and not args.confirm_paid:
        print("Refusing to run: --provider %s makes paid API calls. Add %s to confirm, "
              "or use --provider scripted (free, offline)." % (args.provider, config.PAID_CONFIRM_FLAG),
              file=sys.stderr)
        return 2

    if is_arb and args.provider != "scripted":
        print("Refusing to run: --mechanism %s is scripted-only for now (it uses answer.json as a "
              "stand-in panel). Use --provider scripted." % args.mechanism, file=sys.stderr)
        return 2

    case_dirs = list_case_dirs(args.disputes_dir, args.cases)
    try:
        if is_arb:
            from arb.bench import HARNESS_MECHANISMS, run_cases_arb
            runs = run_cases_arb(case_dirs, args.friction_cents,
                                 mechanism=HARNESS_MECHANISMS[args.mechanism])
        else:
            runs = run_cases(case_dirs, args.provider, args.rounds, args.friction_cents)
    except ProviderError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2

    summary = summarize(runs)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.out or os.path.join(REPO_ROOT, "results", "%s.json" % stamp)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    doc = {
        "meta": {
            "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
            "provider": args.provider,
            "mechanism": args.mechanism,
            "notARealResult": ARB_LABEL if is_arb else None,
            "cases": len(case_dirs),
            "maxRounds": args.rounds,
            "courtFrictionCents": args.friction_cents,
            "models": config.MODELS if args.provider != "scripted" else "scripted",
        },
        "summary": summary,
        "runs": runs,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    if is_arb:
        print("*** %s ***\n" % ARB_LABEL)
    print(format_summary(summary, len(case_dirs), args.friction_cents))
    if is_arb:
        print("\n*** %s ***" % ARB_LABEL)
    print("\nWrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
