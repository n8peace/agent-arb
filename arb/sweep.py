"""Seed-averaged sweep of the arb modes over simulated panels (scripted, offline, no model calls).

    python3 -m arb.sweep [--seeds 5] [--cases all] [--variants]

For each mechanism (arb-clamped, arb-final), noise % (0/5/10/20) and bias factor (0/0.5/1):
bias 0 uses the noisy panel, bias > 0 the biased panel. Each cell is the mean over seeds
0..N-1 of: claimant strength gap, respondent strength gap, mean |award - owedCents| on awarded
runs, and the human-review rate. Open haggling is printed once as the reference (no panel).
--variants adds rows for candidate protections (2-of-3 median range, a 2-claim filing cap,
equal platform-run filing, and median range + equal filing).

SIMULATED PANEL, NOT A REAL RESULT: see arb/simpanel.py.
"""
import argparse
import sys
from typing import List, Optional

from harness.case_loader import list_case_dirs
from harness.run import DEFAULT_DISPUTES, run_cases
from harness.scoring import summarize

from . import decide
from .bench import SIM_LABEL, run_cases_arb
from .simpanel import extra_metrics, make_panel

NOISES = (0, 5, 10, 20)
BIASES = (0, 0.5, 1)
MECHS = (("arb-clamped", decide.MECH_CLAMPED), ("arb-final", decide.MECH_FINAL_OFFER))
VARIANTS = (("median range", {"range_rule": "median"}),
            ("filing cap 2", {"filing_cap": 2}),
            ("equal filing", {"equal_filing": True}),
            ("median + equal filing", {"range_rule": "median", "equal_filing": True}))


def cell(case_dirs, mechanism, noise, bias, seeds, friction_cents=15000, **opts) -> dict:
    kind = "biased" if bias else "noisy"
    acc = {"claimantGap": 0.0, "respondentGap": 0.0, "meanAbsErr": 0.0, "reviewRate": 0.0}
    for seed in seeds:
        panel = make_panel(kind, noise_pct=noise, bias=bias, seed=seed, **opts)
        runs = run_cases_arb(case_dirs, friction_cents, mechanism=mechanism, panel=panel)
        g, m = summarize(runs)["strengthGap"], extra_metrics(runs)
        acc["claimantGap"] += g["claimant"]["meanCents"]
        acc["respondentGap"] += g["respondent"]["meanCents"]
        acc["meanAbsErr"] += m["meanAbsErrorAwardedCents"]
        acc["reviewRate"] += m["humanReviewRate"]
    return {k: v / len(seeds) for k, v in acc.items()}


def _row(label, noise, bias, c) -> str:
    return "%-26s %5s %4s %+9.2f %+9.2f %9.2f %6.1f%%" % (
        label, "%g%%" % noise if noise is not None else "-", "%g" % bias if bias is not None else "-",
        c["claimantGap"] / 100.0, c["respondentGap"] / 100.0, c["meanAbsErr"] / 100.0,
        c["reviewRate"] * 100)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m arb.sweep", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--cases", default="all")
    ap.add_argument("--variants", action="store_true", help="also test candidate protections")
    args = ap.parse_args(argv)
    case_dirs = list_case_dirs(DEFAULT_DISPUTES, args.cases)
    seeds = list(range(args.seeds))

    print("*** %s ***" % SIM_LABEL)
    print("%d cases x 4 pairings, mean over %d seeds. Gaps: avg swing in that side's favor when its "
          "agent goes weak -> strong. |err| on awarded runs only.\n" % (len(case_dirs), len(seeds)))
    print("%-26s %5s %4s %9s %9s %9s %7s" % ("mode", "noise", "bias", "clm gap $", "rsp gap $",
                                           "|err| $", "review"))
    h = run_cases(case_dirs, "scripted", 8, 15000)
    g, m = summarize(h)["strengthGap"], extra_metrics(h)
    print(_row("haggle (reference)", None, None, {
        "claimantGap": g["claimant"]["meanCents"], "respondentGap": g["respondent"]["meanCents"],
        "meanAbsErr": m["meanAbsErrorAwardedCents"], "reviewRate": 0.0}))
    for name, mech in MECHS:
        for noise in NOISES:
            for bias in BIASES:
                print(_row(name, noise, bias, cell(case_dirs, mech, noise, bias, seeds)))
    if args.variants:
        print("\nCandidate protections (arb-clamped):")
        for label, opts in VARIANTS:
            for noise in (0, 10):
                for bias in BIASES:
                    print(_row(label, noise, bias, cell(case_dirs, decide.MECH_CLAMPED, noise, bias,
                                                        seeds, **opts)))
    print("\n*** %s ***" % SIM_LABEL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
