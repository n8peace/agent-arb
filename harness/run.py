"""CLI: python3 -m harness.run --provider scripted --cases all --out results/<timestamp>.json"""
import argparse
import datetime
import json
import os
import sys
from typing import List, Optional

from . import config
from .case_loader import list_case_dirs, load_case
from .cost import BudgetExceeded, CostTracker
from .haggle import haggle
from .providers import ProviderError, make_provider, preflight
from .scoring import STRENGTHS, load_answer, score_run, summarize

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DISPUTES = os.path.join(REPO_ROOT, "bench", "disputes")
ARB_LABEL = ("UPPER BOUND, not a real result: arb mechanisms use answer.json (owedCents, lowCents, "
             "highCents) as a perfect fact panel. Offers are sealed and scripted.")
SIM_LABEL = ("SIMULATED PANEL, not a real result: arb mechanisms use answer.json plus simulated "
             "panel error (arb.simpanel). Offers are sealed and scripted.")
MECHANISM_CHOICES = ["haggle", "arb-final", "arb-clamped", "arb"]
MECHANISM_ALIASES = {"arb": "arb-final"}


def run_cases(case_dirs: List[str], provider_kind: str, max_rounds: int, friction_cents: int,
              provider_factory=None, tracker: Optional[CostTracker] = None) -> List[dict]:
    """Run all 4 strength pairings for each case and score them. Deterministic for scripted.

    With a tracker (paid runs), the providers share it and a budget stop or API error ends the
    run early: the pairings finished so far are returned and tracker.stop_reason says why. The
    pairing in progress is dropped (its spend still counts). Without a tracker, errors raise.
    """
    factory = provider_factory or make_provider
    runs: List[dict] = []
    try:
        _run_all(case_dirs, provider_kind, max_rounds, friction_cents, factory, tracker, runs)
    except BudgetExceeded as e:
        if tracker is None:
            raise
        tracker.stop_reason = "budget: %s" % e
    except ProviderError as e:
        if tracker is None:
            raise
        tracker.stop_reason = "error: %s" % e
    return runs


def _run_all(case_dirs, provider_kind, max_rounds, friction_cents, factory, tracker, runs) -> None:
    for case_dir in case_dirs:
        case = load_case(case_dir)
        owed = load_answer(case_dir)["owedCents"]   # scoring only; agents never see this
        for cs in STRENGTHS:
            for rs in STRENGTHS:
                snap = tracker.snapshot() if tracker else None
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
                if tracker:
                    rec["usage"] = tracker.since(snap)
                rec["transcript"] = res.transcript
                runs.append(rec)


def format_spend(spend: dict) -> str:
    lines = ["Spend: $%.4f of $%.2f budget (%d calls, %s input tokens, %s output tokens, %d malformed replies)" % (
        spend["spentUsd"], spend["maxUsd"], spend["calls"], format(spend["inputTokens"], ","),
        format(spend["outputTokens"], ","), spend["malformedReplies"])]
    for model, m in spend["byModel"].items():
        lines.append("  %-20s %5d calls  $%.4f" % (model, m["calls"], m["costUsd"]))
    if spend["stoppedEarly"]:
        lines.append("STOPPED EARLY, results are partial: %s" % spend["stoppedEarly"])
    return "\n".join(lines)


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
    if summary.get("spend"):
        lines.append("")
        lines.append(format_spend(summary["spend"]))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m harness.run", description=__doc__)
    ap.add_argument("--provider", choices=["scripted", "anthropic", "openai"], default=None,
                    help="default scripted (free, offline); default anthropic with --estimate")
    ap.add_argument("--cases", default="all", help="'all', or e.g. '1,7,12-15'")
    ap.add_argument("--out", default=None, help="output JSON path (default results/<timestamp>.json)")
    ap.add_argument("--mechanism", choices=MECHANISM_CHOICES, default="haggle",
                    help="haggle = open agent-vs-agent baseline (default). arb-final = Agent Arb final-offer "
                         "rule; arb-clamped = final-offer clamped to the panel's range, panel amount when the "
                         "range is narrow. arb is an alias for arb-final. Both arb modes use sealed offers and "
                         "a perfect-panel stand-in; scripted only, an UPPER BOUND, not a real result")
    ap.add_argument("--panel", choices=["perfect", "noisy", "biased"], default="perfect",
                    help="arb modes only. perfect = answer.json as the panel (default). noisy = three "
                         "simulated panelists with seeded noise, median point, min-max range. biased = "
                         "noisy plus a pull toward the side that filed more claims/evidence. See arb/simpanel.py")
    ap.add_argument("--noise-pct", type=float, default=None,
                    help="noisy/biased: panelist noise sd as %% of the contested amount (default 10)")
    ap.add_argument("--bias", type=float, default=None,
                    help="biased: persuasion factor toward the side that filed more (default 0.5)")
    ap.add_argument("--margin-pct", type=float, default=None,
                    help="noisy/biased: widen the panel range by this %% of the contested amount (default 0)")
    ap.add_argument("--seed", type=int, default=None, help="noisy/biased: panel noise seed (default 0)")
    ap.add_argument("--rounds", type=int, default=config.MAX_ROUNDS)
    ap.add_argument("--friction-cents", type=int, default=config.COURT_FRICTION_CENTS)
    ap.add_argument("--disputes-dir", default=DEFAULT_DISPUTES)
    ap.add_argument(config.PAID_CONFIRM_FLAG, dest="confirm_paid", action="store_true",
                    help="required for anthropic/openai: acknowledges that real runs cost money")
    ap.add_argument("--max-usd", type=float, default=None, metavar="N",
                    help="required for paid providers: hard spend limit in USD. The run stops cleanly "
                         "before any call that could push the spend past N and saves partial results")
    ap.add_argument("--estimate", action="store_true",
                    help="print the projected cost of this run (cases x pairings x rounds x sides) and "
                         "exit. Offline: no network, no key, no SDK needed")
    args = ap.parse_args(argv)
    args.provider = args.provider or ("anthropic" if args.estimate else "scripted")
    args.mechanism = MECHANISM_ALIASES.get(args.mechanism, args.mechanism)
    is_arb = args.mechanism != "haggle"

    if args.estimate:
        from .estimate import estimate, format_estimate
        if args.provider != "anthropic":
            print("--estimate has prices for --provider anthropic only.", file=sys.stderr)
            return 2
        case_dirs = list_case_dirs(args.disputes_dir, args.cases)
        print(format_estimate(estimate(case_dirs, args.rounds, args.provider), args.provider, args.max_usd))
        return 0

    if args.provider != "scripted" and not args.confirm_paid:
        print("Refusing to run: --provider %s makes paid API calls. Add %s to confirm, "
              "or use --provider scripted (free, offline)." % (args.provider, config.PAID_CONFIRM_FLAG),
              file=sys.stderr)
        return 2

    if args.provider == "openai":
        print("Refusing to run: the OpenAI provider is untested and has no cost tracking, so a hard "
              "--max-usd budget cannot be enforced. Use --provider anthropic.", file=sys.stderr)
        return 2

    if args.provider != "scripted" and (args.max_usd is None or args.max_usd <= 0):
        print("Refusing to run: --provider %s needs a hard budget. Add --max-usd N (N > 0), e.g. "
              "--max-usd 10. Use --estimate first to see the projected cost." % args.provider,
              file=sys.stderr)
        return 2

    if args.provider == "scripted" and args.max_usd is not None:
        print("Note: --max-usd has no effect on scripted runs (they cost nothing).", file=sys.stderr)

    if is_arb and args.provider != "scripted":
        print("Refusing to run: --mechanism %s is scripted-only for now (it uses answer.json as a "
              "stand-in panel). Use --provider scripted." % args.mechanism, file=sys.stderr)
        return 2

    try:
        preflight(args.provider)
    except ProviderError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2

    panel_opts = {k: v for k, v in (("noise_pct", args.noise_pct), ("bias", args.bias),
                                     ("margin_pct", args.margin_pct), ("seed", args.seed)) if v is not None}
    if not is_arb and (args.panel != "perfect" or panel_opts):
        print("Refusing to run: --panel and its options apply to arb mechanisms only "
              "(haggle has no fact panel).", file=sys.stderr)
        return 2
    panel = None
    if is_arb:
        from arb.simpanel import make_panel
        try:
            panel = make_panel(args.panel, **panel_opts)
        except ValueError as e:
            print("error: %s" % e, file=sys.stderr)
            return 2
    label = ARB_LABEL if args.panel == "perfect" else SIM_LABEL

    case_dirs = list_case_dirs(args.disputes_dir, args.cases)
    tracker = CostTracker(args.max_usd) if args.provider != "scripted" else None
    try:
        if is_arb:
            from arb.bench import HARNESS_MECHANISMS, run_cases_arb
            runs = run_cases_arb(case_dirs, args.friction_cents,
                                 mechanism=HARNESS_MECHANISMS[args.mechanism], panel=panel)
        else:
            factory = (lambda kind, strength: make_provider(kind, strength, tracker=tracker)) if tracker else None
            runs = run_cases(case_dirs, args.provider, args.rounds, args.friction_cents,
                             provider_factory=factory, tracker=tracker)
    except ProviderError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2

    summary = summarize(runs)
    if tracker:
        summary["spend"] = tracker.report()
    if is_arb:
        from arb.simpanel import extra_metrics
        summary["panelMetrics"] = extra_metrics(runs)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.out or os.path.join(REPO_ROOT, "results", "%s.json" % stamp)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    doc = {
        "meta": {
            "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
            "provider": args.provider,
            "mechanism": args.mechanism,
            "notARealResult": label if is_arb else None,
            "panel": panel.describe() if is_arb else None,
            "cases": len(case_dirs),
            "plannedRuns": len(case_dirs) * 4,
            "completedRuns": len(runs),
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
        print("*** %s ***\n" % label)
    print(format_summary(summary, len({r["caseId"] for r in runs}) if tracker else len(case_dirs),
                         args.friction_cents))
    if is_arb:
        pm = summary["panelMetrics"]
        print("\nPanel: %s. Human review rate: %.1f%%. Mean |award - owed| on awarded runs: $%.2f" % (
            args.panel, pm["humanReviewRate"] * 100, pm["meanAbsErrorAwardedCents"] / 100.0))
        print("\n*** %s ***" % label)
    print("\nWrote %s" % out)
    if tracker and tracker.stop_reason and tracker.stop_reason.startswith("error"):
        return 2       # a budget stop is a clean finish; an API error is not
    return 0


if __name__ == "__main__":
    sys.exit(main())
