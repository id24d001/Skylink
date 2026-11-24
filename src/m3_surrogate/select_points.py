"""Score candidate points and choose oracle request 2 (exploitation / exploration / random / repeat).

Run from the repo root:   python -m m3_surrogate.select_points   (with src/ on the path)
or call choose_request(...) from a notebook.

Every candidate (D, T) on a dense grid gets two scores, both in [0, 1]:

  exploration = W_SIGMA * sigma/sigma_max + W_DISAGREE * |RBF - GP| / max
      "how little we know here": GP uncertainty, plus where the two best models disagree.

  exploitation = closeness_to_M1 * sigma/sigma_max
      closeness_to_M1 = max over the M1 hover points (loaded and empty of designs A-E) of
                        weight_design * exp(-d^2 / (2 * ELL^2)),  d = scaled distance
      "how much M1 needs FM here", multiplied by sigma so spots we already know well score low.

Selection (greedy, one point at a time):
  1. 3 x exploitation: highest exploitation score among candidates at least D_MIN from every
     existing and already-chosen point.
  2. 3 x exploration: same, with the exploration score.
     After every pick, sigma is recomputed with the new point added (GP hyperparameters fixed;
     sigma does not need the unknown FM value), so the next pick moves elsewhere.
  3. 2 x random: uniform over the box with a fixed seed, redrawn if closer than D_MIN to a point.
  4. 1 x repeat of an existing point (default pt 3) to measure oracle noise.
"""
import numpy as np
import pandas as pd

from .data import D_MIN as DLO, D_MAX as DHI, T_MIN as TLO, T_MAX as THI, disk_loading, make_features
from .models import GPSurrogate, RBFSurrogate
from .adaptive import trend_std

# ---- settings (all stated in the decision log) ----
W_SIGMA, W_DISAGREE = 0.7, 0.3          # exploration score weights
ELL = 0.08                              # closeness length scale, scaled units (~0.036 m in D, ~2.6 N in T)
DESIGN_WEIGHT = {"C": 1.0}              # baseline C counts fully ...
OTHER_WEIGHT = 0.6                      # ... other designs A, B, D, E at 0.6
D_MIN = 0.10                            # minimum scaled distance to any existing / chosen point
N_EXPLOIT, N_EXPLORE, N_RANDOM = 3, 3, 2
REPEAT_PTS = (3,)
SEED = 6002


def m1_hover_points(designs):
    """One row per design and state: design letter, state, D, T, weight."""
    rows = []
    for _, r in designs.iterrows():
        k = str(r.design).split()[0]
        w = DESIGN_WEIGHT.get(k, OTHER_WEIGHT)
        rows.append({"design": k, "state": "loaded", "D_m": r.D, "T_N": r.T_hover_loaded, "weight": w})
        rows.append({"design": k, "state": "empty", "D_m": r.D, "T_N": r.T_hover_empty, "weight": w})
    return pd.DataFrame(rows)


def closeness(Xc, hover):
    Xh = make_features(hover.D_m, hover.T_N)
    d2 = ((Xc[:, None, :] - Xh[None, :, :])**2).sum(axis=2)
    k = hover.weight.values[None, :] * np.exp(-d2 / (2 * ELL**2))
    return k.max(axis=1), hover.design.values[k.argmax(axis=1)] + "-" + hover.state.values[k.argmax(axis=1)]


def min_dist(Xc, Xd):
    return np.min(np.linalg.norm(Xc[:, None, :] - Xd[None, :, :], axis=2), axis=1)


def choose_request(df, designs, n_grid=91):
    """df: oracle data (load_oracle). designs: results/m1_designs.csv as a DataFrame.
    Returns (plan, grid) where grid holds the initial score maps for plotting."""
    X, y = make_features(df.D_m, df.T_N), df.FM.values
    gp = GPSurrogate().fit(X, y)
    rbf = RBFSurrogate("cubic").fit(X, y)

    Dg, Tg = np.meshgrid(np.linspace(DLO, DHI, n_grid), np.linspace(TLO, THI, n_grid))
    cand = pd.DataFrame({"D_m": Dg.ravel(), "T_N": Tg.ravel()})
    cand["DL"] = disk_loading(cand.D_m, cand.T_N)
    Xc = make_features(cand.D_m, cand.T_N)

    disagree = np.abs(gp.predict(Xc) - rbf.predict(Xc))
    dis_n = disagree / disagree.max()
    hover = m1_hover_points(designs)
    close, near_design = closeness(Xc, hover)

    def scores(X_design):
        sd = trend_std(gp, X_design, Xc)
        sd_n = sd / sd0.max()                      # normalised by the request-1 maximum
        return sd, close * sd_n, W_SIGMA * sd_n + W_DISAGREE * dis_n

    sd0 = trend_std(gp, X, Xc)
    _, exploit0, explore0 = scores(X)
    grid = dict(D=Dg, T=Tg, sigma=sd0.reshape(Dg.shape), exploit=exploit0.reshape(Dg.shape),
                explore=explore0.reshape(Dg.shape), closeness=close.reshape(Dg.shape), hover=hover)

    X_design = X.copy()
    rows = []

    def add(k, category, why):
        nonlocal X_design
        sd, ex, ep = scores(X_design)
        rows.append({"category": category, "D_m": cand.D_m[k], "T_N": cand.T_N[k], "DL": cand.DL[k],
                     "exploit_score": ex[k], "explore_score": ep[k], "sigma": sd[k],
                     "dist_existing": min_dist(Xc[k:k + 1], X)[0],
                     "dist_any": min_dist(Xc[k:k + 1], X_design)[0],
                     "near_M1": near_design[k] if close[k] > 0.05 else "", "why": why})
        X_design = np.vstack([X_design, Xc[k]])

    for category, n, which in (("exploitation", N_EXPLOIT, 1), ("exploration", N_EXPLORE, 2)):
        for _ in range(n):
            s = scores(X_design)[which]
            ok = min_dist(Xc, X_design) >= D_MIN
            k = np.flatnonzero(ok)[np.argmax(s[ok])]
            why = ("highest exploitation score: close to M1 hover point and still uncertain"
                   if which == 1 else "highest exploration score: largest uncertainty / model disagreement")
            add(k, category, why)

    rng = np.random.default_rng(SEED)
    for _ in range(N_RANDOM):
        while True:
            D, T = rng.uniform(DLO, DHI), rng.uniform(TLO, THI)
            D, T = round(D, 3), round(T, 2)
            xq = make_features(D, T)
            if min_dist(xq, X_design)[0] >= D_MIN:
                break
        cand.loc[len(cand)] = [D, T, float(disk_loading(D, T))]
        Xc = np.vstack([Xc, xq]); close = np.append(close, closeness(xq, hover)[0])
        near_design = np.append(near_design, closeness(xq, hover)[1])
        dis_n = np.append(dis_n, np.abs(gp.predict(xq) - rbf.predict(xq)) / disagree.max())
        add(len(cand) - 1, "random", f"uniform random (seed {SEED}): unbiased check of the surrogate")

    for p in REPEAT_PTS:
        D, T = df.loc[p, "D_m"], df.loc[p, "T_N"]
        xq = make_features(D, T)
        cand.loc[len(cand)] = [D, T, float(disk_loading(D, T))]
        Xc = np.vstack([Xc, xq]); close = np.append(close, closeness(xq, hover)[0])
        near_design = np.append(near_design, closeness(xq, hover)[1])
        dis_n = np.append(dis_n, np.abs(gp.predict(xq) - rbf.predict(xq)) / disagree.max())
        add(len(cand) - 1, "repeat", f"repeat of pt {p}: difference between the two FM values = oracle noise")

    plan = pd.DataFrame(rows)
    plan["D_m"] = plan.D_m.round(3); plan["T_N"] = plan.T_N.round(2)
    plan["DL"] = disk_loading(plan.D_m, plan.T_N)
    plan.index = [f"R2-{i}" for i in range(1, len(plan) + 1)]
    plan.index.name = "point"
    Xp = make_features(plan.D_m, plan.T_N)
    plan["FM_GP"], plan["sd_GP"] = gp.predict(Xp, return_std=True)
    plan["FM_RBF"] = rbf.predict(Xp)
    return plan, grid


CATEGORY_COLORS = {"exploitation": "cyan", "exploration": "orange", "random": "lime", "repeat": "red"}


def plot_scores(df, plan, grid, path=None):
    """Two panels: exploitation and exploration score maps (before any pick), with request-1 points,
    M1 hover points A-E, the DL gap 151-217 hatched, and the chosen points as numbered stars."""
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    D, T = grid["D"], grid["T"]
    DL = disk_loading(D, T)
    fig, axes = plt.subplots(1, 2, figsize=(16, 7.2), sharey=True)
    panels = [(axes[0], "exploit", "Exploitation score = closeness to M1 hover points × σ", "viridis"),
              (axes[1], "explore", f"Exploration score = {W_SIGMA}·σ + {W_DISAGREE}·|RBF−GP| (normalised)", "magma")]
    for ax, key, title, cmap in panels:
        cf = ax.contourf(D, T, grid[key], levels=20, cmap=cmap)
        fig.colorbar(cf, ax=ax, shrink=0.8, label="score (before any pick)")
        ax.contourf(D, T, DL, levels=[151, 217], colors="none", hatches=["//"])
        cs = ax.contour(D, T, DL, levels=[25, 50, 100, 151, 217, 300], colors="w", linewidths=0.7, linestyles="--")
        ax.clabel(cs, fmt="DL=%d", fontsize=7)
        h = grid["hover"]
        for k, sub in h.groupby("design"):
            ax.plot(sub.D_m, sub.T_N, "-", color="w", lw=1.5, alpha=0.8)
            top = sub[sub.state == "loaded"].iloc[0]
            ax.annotate(k, (top.D_m, top.T_N), xytext=(-12, 3), textcoords="offset points",
                        color="w", fontsize=9, fontweight="bold")
        ax.scatter(h.D_m, h.T_N, marker="s", s=25, c="w", edgecolor="k", zorder=4)
        ax.scatter(df.D_m, df.T_N, c="w", edgecolor="k", s=45, zorder=5)
        for i in df.index:
            ax.annotate(str(i), (df.D_m[i], df.T_N[i]), xytext=(4, -10), textcoords="offset points",
                        fontsize=8, color="w")
        for lbl, r in plan.iterrows():
            c = CATEGORY_COLORS[r.category]
            ax.scatter(r.D_m, r.T_N, marker="*", s=330, c=c, edgecolor="k", zorder=6)
            ax.annotate(lbl.replace("R2-", ""), (r.D_m, r.T_N), xytext=(6, 5), textcoords="offset points",
                        fontsize=11, fontweight="bold", color=c,
                        path_effects=[pe.withStroke(linewidth=2.5, foreground="k")])
        ax.set_xlim(DLO - 0.005, DHI + 0.005); ax.set_ylim(TLO - 0.5, THI + 0.5)
        ax.set_xlabel("D (m)"); ax.set_title(title, fontsize=11)
    axes[0].set_ylabel("T per rotor (N)")
    handles = [Line2D([], [], marker="o", ls="", mfc="w", mec="k", label="request 1 (numbered)"),
               Line2D([], [], marker="s", ls="", mfc="w", mec="k", label="M1 hover points A–E (loaded/empty)"),
               Patch(facecolor="none", edgecolor="k", hatch="//", label="DL gap 151–217")]
    handles += [Line2D([], [], marker="*", ls="", mfc=c, mec="k", ms=15, label=f"R2 {k}")
                for k, c in CATEGORY_COLORS.items()]
    fig.legend(handles=handles, loc="lower center", ncol=7, fontsize=9, bbox_to_anchor=(0.5, 0.0))
    fig.suptitle("Oracle request 2: score maps and the chosen points (numbers = R2-n)", fontsize=13)
    fig.tight_layout(rect=(0, 0.06, 1, 0.97))
    if path is not None:
        fig.savefig(path, dpi=150)
    return fig
