"""Put the M2 plate mass back into M1 and re-run once (brief), on both FM paths.

  FM0 path       the full M1 step (m1_vehicle.run.run) is re-run with ctx["m2_plate_mass"], so every
                 M1 output in results/ and figures/ is rewritten with the M2 plate. ctx["m1_designs"]
                 then holds the post-M2 designs for M3 onwards.
  surrogate path the cubic-RBF FM from M3 (fitted here on data/oracle, same as m3_surrogate.run),
                 same five radii, solved with the 0.25 kg and the M2 plate. M3's own re-run later in
                 run_all.py also uses ctx["m2_plate_mass"], so its outputs are post-M2 too.

Writes results/m2_m1_feedback.csv: for each design and path, mass before/after (continuous and
snapped), D, E, V and parts before/after.
"""
import numpy as np
import pandas as pd

from common import ORACLE, RESULTS

PLATE_M1 = 0.25   # kg, the brief's provisional plate mass used by M1 before M2


def _row(path, name, R, before, after):
    return dict(path=path, design=name, R_req_km=R,
                m_cont_before=before["mc"], m_cont_after=after["mc"],
                dm_cont_g=1000 * (after["mc"] - before["mc"]),
                m_snap_before=before["ms"], m_snap_after=after["ms"],
                dm_snap_g=1000 * (after["ms"] - before["ms"]),
                D_before=before["D"], D_after=after["D"], E_before=before["E"], E_after=after["E"],
                V_before=before["V"], V_after=after["V"],
                parts_before=before["parts"], parts_after=after["parts"],
                parts_changed=before["parts"] != after["parts"])


def _parts(s):
    return f"{s['battery']}, {s['motor']}, {s['tube']}, {int(s['prop_in'])} in"


def fm0_path(ctx, m_new):
    from m1_vehicle import run as m1run
    cont0 = pd.read_csv(RESULTS / "m1_designs_continuous.csv").set_index("design")
    snap0 = ctx["m1_designs"].set_index("design")
    old_plate = PLATE_M1
    print(f"  re-running M1 (FM0) with plate {m_new:.4f} kg (was {old_plate:.3f} kg)")
    out = m1run.run({**ctx, "m2_plate_mass": m_new})
    cont1 = pd.read_csv(RESULTS / "m1_designs_continuous.csv").set_index("design")
    snap1 = out["m1_designs"].set_index("design")
    rows = []
    for name in snap0.index:
        b = dict(mc=cont0.loc[name, "m"], ms=snap0.loc[name, "m"], D=snap0.loc[name, "D"],
                 E=snap0.loc[name, "E"], V=snap0.loc[name, "V"], parts=_parts(snap0.loc[name]))
        a = dict(mc=cont1.loc[name, "m"], ms=snap1.loc[name, "m"], D=snap1.loc[name, "D"],
                 E=snap1.loc[name, "E"], V=snap1.loc[name, "V"], parts=_parts(snap1.loc[name]))
        rows.append(_row("FM0 = 0.65", name, snap0.loc[name, "R_req_km"], b, a))
    return rows, out


def surrogate_path(radii, m_old, m_new, n_starts=6):
    try:
        from m3_surrogate import load_oracle, average_repeats, make_features, RBFSurrogate, FMFunction, FMDomain
        raw = load_oracle(ORACLE)
    except Exception as e:      # no oracle data yet: skip this path
        print(f"  surrogate path skipped ({e})")
        return []
    from m1_vehicle.catalogue import snap
    from m1_vehicle.mdo import Problem, solve_mdf
    from m1_vehicle.model import Params
    from m1_vehicle.run import SIZING_WIND
    df, _ = average_repeats(raw)
    fm = FMFunction(RBFSurrogate("cubic", 0.0).fit(make_features(df.D_m, df.T_N), df.FM.values), "RBF cubic")
    dom = FMDomain(df.D_m.values, df.T_N.values)
    rows = []
    for name, R in radii.items():
        res = {}
        for tag, mp in (("before", m_old), ("after", m_new)):
            prob = Problem(R, SIZING_WIND, Params(m_plate=mp, fm=fm, complex_safe=False), fm_domain=dom)
            try:
                best, _ = solve_mdf(prob, n_starts=n_starts)
                s = snap(prob)
            except RuntimeError as e:
                print(f"  surrogate {name} ({tag}): {e}")
                break
            if s is None:
                break
            x = prob.to_x(best.x)
            res[tag] = dict(mc=10 * best.fun, ms=s["m"], D=s["D"], E=s["E"], V=s["V"], parts=_parts(s))
        if len(res) == 2:
            rows.append(_row("surrogate FM (cubic RBF)", name, R, res["before"], res["after"]))
    return rows


def feedback(ctx, m_new):
    rows, out = fm0_path(ctx, m_new)
    radii = dict(zip(out["m1_designs"].design, out["m1_designs"].R_req_km))
    rows += surrogate_path(radii, PLATE_M1, m_new)
    tab = pd.DataFrame(rows)
    tab.to_csv(RESULTS / "m2_m1_feedback.csv", index=False, float_format="%.5g")
    show = tab[["path", "design", "m_cont_before", "m_cont_after", "dm_cont_g", "m_snap_before",
                "m_snap_after", "dm_snap_g", "parts_changed"]]
    print(show.round(4).to_string(index=False))
    return tab, out
