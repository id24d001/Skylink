"""M3 pipeline step for run_all.py: fit the surrogates on all oracle data and verify them.

Writes
  results/m3_loo_table.csv      LOOCV error table (verification table for M3)
  results/m3_gp_hyperparameters.csv
  figures/m3_loo_parity.png     LOO parity plot (verification figure for M3)
  + M1 re-run with the chosen surrogate (cubic RBF, user's choice by cross-validation):
    results/m1_fm_*.csv, figures/m1_fm_rerun.png (see m1_vehicle/fm_rerun.py)
Returns the cubic RBF as ctx["fm_surrogate"] (FM for M1 and later modules) and the GP as
ctx["fm_gp"], kept only for its uncertainty (sigma map, adaptive sampling).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import ORACLE, RESULTS, FIGURES
from . import (load_oracle, average_repeats, make_features, QuadraticSurrogate, RBFSurrogate,
               GPSurrogate, loo_predictions, loo_table)


def candidate_models():
    return {
        "quadratic": QuadraticSurrogate(),
        "RBF thin-plate exact": RBFSurrogate("thin_plate_spline", 0.0),
        "RBF thin-plate s=1e-3": RBFSurrogate("thin_plate_spline", 1e-3),
        "RBF cubic": RBFSurrogate("cubic", 0.0),
        "GP (ARD + nugget)": GPSurrogate(),
    }


def run(ctx):
    raw = load_oracle(ORACLE)
    df, repeats = average_repeats(raw)        # repeated (D, T) -> one point with mean FM
    X, y = make_features(df.D_m, df.T_N), df.FM.values
    print(f"  oracle points: {len(raw)} from {sorted(raw.batch.unique())}, {len(df)} unique")
    if len(repeats):
        print(f"  repeats (noise check):\n{repeats.to_string(index=False)}")

    loo = {name: loo_predictions(m, X, y) for name, m in candidate_models().items()}
    table = loo_table(loo, y)
    table.to_csv(RESULTS / "m3_loo_table.csv", float_format="%.4f")
    print(table.round(4).to_string())

    gp = GPSurrogate().fit(X, y)
    pd.Series(gp.hyperparameters()).to_csv(RESULTS / "m3_gp_hyperparameters.csv",
                                           header=["value"])

    n = len(loo)
    fig, axes = plt.subplots(1, n, figsize=(3.2 * n, 3.4), sharex=True, sharey=True)
    lo, hi = y.min() - 0.03, y.max() + 0.03
    for ax, (name, (mu, sd)) in zip(axes, loo.items()):
        if np.all(np.isfinite(sd)):
            ax.errorbar(y, mu, yerr=1.96 * sd, fmt="o", ms=4, capsize=2)
        else:
            ax.plot(y, mu, "o", ms=4)
        ax.plot([lo, hi], [lo, hi], "k--", lw=1)
        ax.set_title(f"{name}\nRMSE {table.loc[name, 'LOO RMSE']:.3f}", fontsize=9)
        ax.set_xlabel("oracle FM")
    axes[0].set_ylabel("LOO prediction")
    axes[0].set_xlim(lo, hi); axes[0].set_ylim(lo, hi)
    fig.tight_layout()
    fig.savefig(FIGURES / "m3_loo_parity.png", dpi=150)
    plt.close(fig)

    # ---- chosen surrogate: cubic RBF (best LOO / 5-fold / hold-out); GP kept for sigma only
    from . import FMFunction, FMDomain
    rbf = RBFSurrogate("cubic", 0.0).fit(X, y)
    fm = FMFunction(rbf, "RBF cubic")
    domain = FMDomain(df.D_m.values, df.T_N.values)
    print("  re-running M1 with FM(D, T) = cubic RBF (hover points kept inside the sampled hull)")
    from m1_vehicle.fm_rerun import rerun
    out = rerun(ctx, fm, domain, gp=gp)

    return {"fm_surrogate": fm, "fm_model": rbf, "fm_gp": gp, "fm_domain": domain,
            "loo_table": table, **out}
