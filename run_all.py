"""SkyLink (ED6002): run the whole chain M0 -> M7 from one script.

    python run_all.py              # every implemented module, in order
    python run_all.py --only m3    # one or more modules, e.g. --only m1 m3
    python run_all.py --fast       # small search budgets (M6) for a quick smoke run

A module is "implemented" when src/<module>/run.py exists and defines run(ctx).
Modules without it are skipped with a note, so the chain always runs end to end.
Each run(ctx) gets the shared dict ctx (outputs of earlier modules) and returns a
dict that is merged into it, e.g. M3 returns ctx["fm_surrogate"] for the M1 re-run.
"""
import argparse
import importlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from common import RESULTS, FIGURES  # noqa: E402

MODULES = [
    ("m0", "m0_framing", "Problem framing"),
    ("m1", "m1_vehicle", "Vehicle MDO"),
    ("m2", "m2_plate", "Hub plate topology"),
    ("m3", "m3_surrogate", "Rotor surrogate from scarce data"),
    ("m4", "m4_network", "Hub network and fleet"),
    ("m5", "m5_simulation", "Discrete-event simulator"),
    ("m6", "m6_moo", "Simulation-based multi-objective optimisation"),
    ("m7", "m7_decision", "Decision and defense"),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="+", metavar="mN",
                    help="run only these modules, e.g. --only m3")
    ap.add_argument("--fast", action="store_true",
                    help="small optimisation budgets (M6) for a quick end-to-end check")
    args = ap.parse_args(argv)
    only = {m.lower() for m in args.only} if args.only else None

    RESULTS.mkdir(exist_ok=True)
    FIGURES.mkdir(exist_ok=True)
    ctx, status = {"fast": args.fast}, []
    for key, pkg, title in MODULES:
        label = f"{key.upper()} {title}"
        if only and key not in only:
            continue
        if not (ROOT / "src" / pkg / "run.py").exists():
            print(f"[skip] {label}: not implemented yet")
            status.append((label, "skipped"))
            continue
        print(f"[run ] {label}")
        t0 = time.time()
        out = importlib.import_module(f"{pkg}.run").run(ctx) or {}
        ctx.update(out)
        status.append((label, f"done in {time.time() - t0:.1f} s"))

    print("\nSummary")
    for label, s in status:
        print(f"  {label:<55} {s}")
    print(f"Outputs: {RESULTS.relative_to(ROOT)}/ and {FIGURES.relative_to(ROOT)}/")
    return ctx


if __name__ == "__main__":
    main()
