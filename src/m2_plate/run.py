"""M2 pipeline step for run_all.py: hub plate topology (SIMP + density filter), outline, M1 feedback.

    python run_all.py --only m2              coarse mesh (2 mm, quarter 40 x 40), about a minute
    SKYLINK_M2_MESH=fine python run_all.py   1 mm mesh (quarter 80 x 80), about 30 minutes

Writes (results/)
  m2_sweep.csv              per volume fraction: SIMP compliance, cut-outline compliance, masses, ratios
  m2_design.csv             the chosen plate: vf, mass, compliance ratio, min member, deflection, stress
  m2_verification.csv       element, symmetry, gradient and mesh checks (verification table)
  m2_mbb_benchmark.csv      MBB benchmark: density filter p = 3, no filter, p = 1
  m2_m1_feedback.csv        how far the M1 designs moved with the new plate mass (FM0 and surrogate)
  m2_plate_outline.dxf/.svg waterjet cutting outline (mm)
Figures (figures/)
  m2_mbb_benchmark.png      verification figure (MBB, three cases)
  m2_compliance_vs_mass.png compliance against plate mass for the sweep
  m2_topologies.png         SIMP density for each volume fraction
  m2_outline.png            chosen design: SIMP field, cut outline with 6 mm check, deflection
Returns ctx["m2_plate_mass"] (kg) and re-runs M1 with it (ctx["m1_designs"] is then post-M2).
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import FIGURES, RESULTS
from .analysis import analyse_cut
from .outline import make_outline, write_dxf, write_svg
from .plate import PlateSpec, build, quarter_to_full
from .simp import optimise, grey_index
from . import verify

MESHES = {"coarse": 40, "fine": 80}     # elements along the quarter side (80 mm): 2 mm / 1 mm
R_MIN_MM = 6.0                          # density-filter radius (mm), = the minimum member width
PENAL = 3.0
VF_SWEEP = np.round(np.arange(0.20, 0.501, 0.05), 2)
RATIO_MAX = 2.5                         # brief: compliance <= 2.5 x solid plate
RATIO_DESIGN = 2.4                      # design to 2.4 (4 % margin): the 1 mm-FE ratio moves ~1 % with
                                        # the SIMP mesh and the pixel outline (verification, mesh check)
N_CUT = 80                              # analysis mesh of the cut part (1 mm, quarter)


def heaviest_battery(ctx):
    """Battery mass carried by the plate: the heaviest pack set in the M1 design set (worst case)."""
    from m1_vehicle.catalogue import load_parts
    bats = load_parts()[0].set_index("battery")
    designs = ctx.get("m1_designs")
    if designs is None:
        return 1.5, "default 1.5 kg (M1 designs not available)"
    m = bats.loc[designs.battery, "m_batt"]
    i = int(np.argmax(m.values))
    return float(m.iloc[i]), f"{designs.battery.iloc[i]} of design {designs.design.iloc[i]}"


def solve_vf(spec, n, vf):
    p, k = build(spec, n)
    r = optimise(p, vf, PENAL, R_MIN_MM / p.grid.h)
    rho_full = quarter_to_full(r["rho"].reshape(n, n))
    o = make_outline(rho_full, vf, L=spec.L, corner=spec.corner, w_min=R_MIN_MM)
    a = analyse_cut(spec, o["binary"], N_CUT)
    return r, rho_full, o, a, k


def run(ctx):
    mesh = os.environ.get("SKYLINK_M2_MESH", "coarse")
    n = MESHES[mesh]
    if "m1_designs" not in ctx:
        print("  M1 designs not in ctx: running M1 first (plate 0.25 kg)")
        from m1_vehicle import run as m1run
        ctx = {**ctx, **m1run.run(ctx)}
    m_batt, why = heaviest_battery(ctx)
    spec = PlateSpec(m_batt=m_batt)
    print(f"  plate 160 x 160 x 3 mm 6061-T6, plate bending, quarter model {n} x {n} ({mesh}, "
          f"{spec.L / 2 / n:.1f} mm), filter r = {R_MIN_MM} mm, p = {PENAL}")
    print(f"  load: {spec.n_z:.0f} g x {m_batt:.3f} kg battery ({why}) = {spec.F:.1f} N on the central 40 x 40 mm; "
          f"w = 0 on 10 x 10 mm arm-clamp patches at the corners")

    p0, k0 = build(spec, n)
    c_solid = k0 * p0.compliance(np.ones(p0.grid.nel))

    # ---- volume-fraction sweep (+ refinement around the 2.5 x limit)
    rows, keep = [], {}

    def do(vf):
        r, rho, o, a, k = solve_vf(spec, n, vf)
        keep[vf] = (r, rho, o, a)
        rows.append(dict(vf=vf, mass_nominal_kg=spec.mass(vf), c_simp=k * r["c"], ratio_simp=k * r["c"] / c_solid,
                         grey_index=grey_index(r["rho"]), iterations=r["iters"],
                         vf_cut=o["area_fraction"], mass_cut_kg=spec.mass(o["area_fraction"]),
                         c_cut=a["c_cut"], ratio_cut=a["ratio"], min_member_mm=o["min_width_mm"],
                         holes=len(o["contours"]) - 1))
        print(f"  vf {vf:.2f}: SIMP c/c_solid {rows[-1]['ratio_simp']:.2f} (grey {rows[-1]['grey_index']:.2f}); "
              f"cut part vf {o['area_fraction']:.3f}, {1000 * rows[-1]['mass_cut_kg']:.1f} g, "
              f"c/c_solid {a['ratio']:.2f}, min member {o['min_width_mm']:.2f} mm")

    for vf in VF_SWEEP:
        do(float(vf))
    sweep = pd.DataFrame(rows)
    ok = sweep[sweep.ratio_cut <= RATIO_DESIGN]
    if ok.empty:
        raise RuntimeError(f"no volume fraction in the sweep meets compliance <= {RATIO_DESIGN} x solid")
    hi = ok.vf.min()
    lo = sweep[sweep.vf < hi].vf.max() if (sweep.vf < hi).any() else hi
    for vf in np.round(np.arange(lo + 0.01, hi - 0.005, 0.01), 2):   # refine on a 0.01 grid
        do(float(vf))
    sweep = pd.DataFrame(rows).sort_values("vf").reset_index(drop=True)
    sweep.to_csv(RESULTS / "m2_sweep.csv", index=False, float_format="%.5g")
    feas = sweep[sweep.ratio_cut <= RATIO_DESIGN]
    best = feas.loc[feas.mass_cut_kg.idxmin()]
    vf_star = float(best.vf)
    r, rho, o, a = keep[vf_star]
    m_plate = float(best.mass_cut_kg)
    print(f"  chosen: vf {vf_star:.2f} -> cut part {1000 * m_plate:.1f} g (vf {o['area_fraction']:.3f}), "
          f"compliance {a['ratio']:.2f} x solid, min member {o['min_width_mm']:.2f} mm")

    write_dxf(o["contours"], RESULTS / "m2_plate_outline.dxf")
    write_svg(o["contours"], spec.L, RESULTS / "m2_plate_outline.svg")
    design = dict(mesh=mesh, element_mm=spec.L / 2 / n, filter_radius_mm=R_MIN_MM, penal=PENAL,
                  battery_kg=m_batt, load_N=spec.F, vf_simp=vf_star, vf_cut=o["area_fraction"],
                  plate_mass_kg=m_plate, plate_mass_solid_kg=spec.mass(1.0), plate_mass_M1_before_kg=0.25,
                  compliance_ratio_limit=RATIO_MAX, compliance_ratio_design=RATIO_DESIGN,
                  compliance_ratio_cut=a["ratio"], compliance_ratio_simp=float(best.ratio_simp),
                  compliance_solid_Nmm=a["c_solid"], compliance_cut_Nmm=a["c_cut"],
                  min_member_mm=o["min_width_mm"], area_removed_by_6mm_check_mm2=o["removed_by_check_mm2"],
                  w_max_mm=a["w_max_mm"], w_max_solid_mm=a["w_max_solid_mm"],
                  sigma_vm_max_MPa=a["sigma_vm_max_MPa"], sigma_yield_MPa=spec.sigma_y,
                  threshold=o["threshold"], n_contours=len(o["contours"]))
    pd.Series(design).to_csv(RESULTS / "m2_design.csv", header=["value"])

    # ---- verification
    print("  verification: MBB benchmark (density filter / no filter / p = 1), element, symmetry, gradients")
    mbb, fields = verify.mbb_benchmark()
    mbb.to_csv(RESULTS / "m2_mbb_benchmark.csv", index=False, float_format="%.4g")
    print(mbb.round(3).to_string(index=False))
    checks = [verify.plate_element(), verify.symmetry_check(spec, 20), verify.gradient_check(spec)]
    if mesh == "coarse":   # mesh check: chosen vf on the 1.33 mm mesh
        rf, _, of, af, kf = solve_vf(spec, 60, vf_star)
        checks.append(pd.DataFrame([dict(check=f"mesh: cut-part compliance ratio at vf {vf_star:.2f}, "
                                               f"{spec.L / 2 / n:.2f} mm vs 1.33 mm design mesh",
                                         mesh="40^2 vs 60^2 quarter", value=a["ratio"], reference=af["ratio"],
                                         rel_error_pct=100 * (a["ratio"] / af["ratio"] - 1))]))
    ver = pd.concat(checks, ignore_index=True)
    ver.to_csv(RESULTS / "m2_verification.csv", index=False, float_format="%.6g")
    print(ver[["check", "mesh", "value", "reference", "rel_error_pct"]].round(6).to_string(index=False))

    fig_mbb(fields, mbb, FIGURES / "m2_mbb_benchmark.png")
    fig_sweep(sweep, spec, vf_star, FIGURES / "m2_compliance_vs_mass.png")
    fig_topologies(keep, VF_SWEEP, FIGURES / "m2_topologies.png")
    fig_outline(rho, o, a, spec, vf_star, FIGURES / "m2_outline.png")

    # ---- plate mass back into M1, re-run once
    from .m1_feedback import feedback
    fb, m1out = feedback(ctx, m_plate)
    return {"m2_plate_mass": m_plate, "m2_design": design, "m2_sweep": sweep, "m2_m1_feedback": fb, **m1out}


# ---------------------------------------------------------------- figures
def fig_mbb(fields, tab, path):
    fig, axes = plt.subplots(3, 1, figsize=(6.4, 6.4))
    for ax, (name, f) in zip(axes, fields.items()):
        r = tab.set_index("case").loc[name]
        ax.imshow(1 - f, cmap="gray", origin="lower", vmin=0, vmax=1, interpolation="nearest")
        ax.set_title(f"{name}:  c = {r.compliance:.1f},  grey index {r.grey_index:.2f},  "
                     f"checkerboard {r.checkerboard_pct:.1f} %", fontsize=8.5)
        ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle("MBB half-beam, 60 x 20, vf = 0.5, r_min = 1.5 (top88 inputs)", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_sweep(sweep, spec, vf_star, path):
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    c0 = sweep.c_cut.iloc[0] / sweep.ratio_cut.iloc[0]
    ax.plot(sweep.mass_nominal_kg * 1000, sweep.ratio_simp, "o-", color="C0", label="SIMP optimum (grey, nominal mass)")
    ax.plot(sweep.mass_cut_kg * 1000, sweep.ratio_cut, "s-", color="C3", label="cut outline, 6 mm members (1 mm FE)")
    ax.axhline(RATIO_MAX, color="k", ls="--", lw=1)
    ax.axhline(RATIO_DESIGN, color="k", ls=":", lw=1)
    ax.text(150, RATIO_MAX + 0.08, "brief limit 2.5 x solid", fontsize=8)
    ax.text(150, RATIO_DESIGN - 0.25, "design target 2.4 (mesh margin)", fontsize=8)
    b = sweep.set_index("vf").loc[vf_star]
    ax.plot(b.mass_cut_kg * 1000, b.ratio_cut, "*", ms=16, color="C3", mec="k", label=f"chosen: vf {vf_star:.2f}")
    ax.plot(spec.mass(1.0) * 1000, 1.0, "D", color="grey", label=f"solid plate ({1000 * spec.mass(1.0):.0f} g)")
    ax.axvline(250, color="grey", ls=":", lw=1)
    ax.text(252, 4.5, "0.25 kg used by M1", rotation=90, fontsize=8, color="grey")
    ax.set_xlabel("plate mass incl. 40 g fasteners (g)")
    ax.set_ylabel("compliance / compliance of solid plate")
    ax.set_title(f"Hub plate: compliance against mass (c_solid = {c0:.3g} N mm)", fontsize=10)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_topologies(keep, vfs, path):
    fig, axes = plt.subplots(1, len(vfs), figsize=(2.1 * len(vfs), 2.5))
    for ax, vf in zip(axes, vfs):
        r, rho, o, a = keep[float(vf)]
        ax.imshow(1 - rho, cmap="gray", origin="lower", vmin=0, vmax=1, extent=(0, 160, 0, 160))
        ax.set_title(f"vf {vf:.2f}\nc/c0 {a['ratio']:.2f} (cut)", fontsize=8)
        ax.set_xticks([]); ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_outline(rho, o, a, spec, vf, path):
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(13, 4.4))
    L = spec.L
    a1.imshow(1 - rho, cmap="gray", origin="lower", vmin=0, vmax=1, extent=(0, L, 0, L))
    a1.set_title(f"SIMP density, vf {vf:.2f} (grey index {grey_index(rho):.2f})", fontsize=9)
    b = o["binary"]
    a2.imshow(np.where(b, 0.75, 1.0), cmap="gray", origin="lower", vmin=0, vmax=1, extent=(0, L, 0, L))
    for ln in o["contours"]:
        a2.plot(ln[:, 0], ln[:, 1], "k-", lw=1)
    c = L / 2
    a2.add_patch(plt.Rectangle((c - 20, c - 20), 40, 40, fill=False, ec="C0", ls="--", lw=1))
    for x in (0, L - spec.corner):
        for y in (0, L - spec.corner):
            a2.add_patch(plt.Rectangle((x, y), spec.corner, spec.corner, fc="C2", alpha=0.6))
    a2.add_patch(plt.Circle((12, 150), o["w_min"] / 2, fc="none", ec="C3"))
    a2.text(17, 148, "6 mm disk", color="C3", fontsize=8)
    a2.set_title(f"waterjet outline: {1000 * spec.mass(o['area_fraction']):.0f} g, "
                 f"min member {o['min_width_mm']:.2f} mm\n(green: arm-clamp supports, blue: battery load)", fontsize=9)
    a2.set_xlim(0, L); a2.set_ylim(0, L)
    p = a["problem"]
    n = p.grid.nx
    w = a["u"][0::3].reshape(n + 1, n + 1)
    wf = quarter_to_full(w[:-1, :-1])
    im = a3.imshow(-np.where(quarter_to_full(a["frac"].reshape(n, n)) > 0.5, wf, np.nan), origin="lower",
                   extent=(0, L, 0, L), cmap="viridis")
    fig.colorbar(im, ax=a3, label="deflection (mm)")
    a3.set_title(f"cut part under {spec.F:.0f} N: w_max {a['w_max_mm']:.3f} mm "
                 f"(solid {a['w_max_solid_mm']:.3f}),\npeak bending stress {a['sigma_vm_max_MPa']:.0f} MPa "
                 f"(yield {spec.sigma_y:.0f})", fontsize=9)
    for ax in (a1, a2, a3):
        ax.set_xlabel("x (mm)")
    a1.set_ylabel("y (mm)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
