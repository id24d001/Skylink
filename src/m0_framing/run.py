"""M0 pipeline step for run_all.py: data audit and the decision-hierarchy figure.

M0 is a formulation module, so its "verification" is a check that the numbers on the card
come from the data pack. This step
  1. audits every data file and records each check in results/m0_data_checks.csv
     (planted problems are flagged, not silently fixed),
  2. derives the numbers the M0 card quotes (demand totals, road-only baseline cost,
     bays that can charge during an outage) into results/m0_framing_numbers.csv,
  3. draws figures/m0_decision_hierarchy.png.
Returns ctx["hourly_profile"], the hourly profile with each class re-scaled to sum to 1
(the routine column sums to 1.1993 in the file). M5 should use this, not the raw file.
"""
import json

import numpy as np
import pandas as pd

from common import RESULTS, FIGURES, DATA, load_csv, load_costs


def clean_hourly_profile(h):
    """Rescale each class so its 24 hourly fractions sum to 1.

    demand.csv gives the daily totals; the profile only says how a day's orders spread over
    the hours. Rescaling keeps the shape and makes the daily total match demand.csv.
    """
    h = h.copy()
    for col in ("emergency_frac", "routine_frac", "lab_frac"):
        h[col] = h[col] / h[col].sum()
    return h


def audit(tol=0.005):
    """Return a DataFrame of checks: file, check, value, expected, status (ok / FLAG)."""
    rows = []

    def add(file, check, value, expected, ok):
        rows.append(dict(file=file, check=check, value=value, expected=expected,
                         status="ok" if ok else "FLAG"))

    sites = load_csv("sites.csv")
    add("sites.csv", "rows", len(sites), 31, len(sites) == 31)
    add("sites.csv", "PHCs", int((sites.type == "phc").sum()), 24, (sites.type == "phc").sum() == 24)
    cand = sorted(sites.loc[sites.candidate_hub == 1, "site_id"])
    add("sites.csv", "candidate hubs", " ".join(cand), "H0-H6 P09",
        cand == ["H0", "H1", "H2", "H3", "H4", "H5", "H6", "P09"])

    dem = load_csv("demand.csv")
    add("demand.csv", "PHC rows", len(dem), 24, len(dem) == 24)
    add("demand.csv", "negative or missing rates",
        int((dem.select_dtypes("number") < 0).sum().sum() + dem.isna().sum().sum()), 0,
        (dem.select_dtypes("number") < 0).sum().sum() == 0 and dem.isna().sum().sum() == 0)

    h = load_csv("hourly_profile.csv")
    add("hourly_profile.csv", "hours", len(h), 24, len(h) == 24)
    for col in ("emergency_frac", "routine_frac", "lab_frac"):
        s = round(float(h[col].sum()), 4)
        add("hourly_profile.csv", f"sum of {col}", s, 1.0, abs(s - 1) <= tol)

    w = load_csv("wind_days.csv")
    add("wind_days.csv", "rows", len(w), 8760, len(w) == 8760)
    add("wind_days.csv", "duplicate (day, hour)", int(w.duplicated(["day", "hour"]).sum()), 0,
        not w.duplicated(["day", "hour"]).any())
    add("wind_days.csv", "gust below mean wind", int((w.gust_ms < w.mean_wind_ms).sum()), 0,
        not (w.gust_ms < w.mean_wind_ms).any())
    gr = round(100 * float((w.gust_ms >= load_costs()["operations"]["gust_grounding_limit_ms"]).mean()), 1)
    add("wind_days.csv", "hours grounded (gust >= 14 m/s), %", gr, "brief monthly table", True)
    jul = round(100 * float((w[w.month == 7].gust_ms >= 14).mean()), 1)
    add("wind_days.csv", "July hours grounded, %", jul, 24.6, abs(jul - 24.6) < 0.1)

    comp = load_csv("components.csv")
    n_tube = int((comp.category == "tube").sum())
    add("components.csv", "tube sizes", n_tube, 30, n_tube == 30)
    b = comp[comp.category == "battery"]
    e_sp = (b.capacity * b.nominal_voltage_V / 1000 / b.mass_kg)
    add("components.csv", "battery Wh/kg (min-max)", f"{e_sp.min():.1f}-{e_sp.max():.1f}", 150,
        bool((abs(e_sp - 150) < 2).all()))
    t = comp[comp.category == "tube"]
    m_calc = np.pi / 4 * (t.outer_dia_mm**2 - (t.outer_dia_mm - 2 * t.wall_mm)**2) * 1e-6 * 1550
    err = float((abs(t.mass_kg - m_calc) / m_calc).max())
    add("components.csv", "tube mass vs 1550 kg/m3, max rel. error", round(err, 4), 0, err < 0.01)

    nf = json.load(open(DATA / "nofly.geojson"))
    names = [f["properties"].get("name") for f in nf["features"]]
    add("nofly.geojson", "zones", len(names), 2, len(names) == 2)

    return pd.DataFrame(rows)


def framing_numbers():
    """Numbers quoted on the M0 card, derived from the data so the card cannot drift."""
    c, dem = load_costs(), load_csv("demand.csv")
    per_day = {k: float(dem[f"{k}_per_day"].sum()) for k in ("emergency", "routine", "lab")}
    road = c["road_fallback"]["cost_per_trip"]
    road_only = 365 * sum(per_day[k] * road[k] for k in per_day)
    bays_outage = int(c["grid_outage"]["genset_kW_per_hub"] // c["charging_bay"]["power_kW"])
    r = c["discount_rate"]
    crf = lambda n: r * (1 + r)**n / ((1 + r)**n - 1)
    hub_year = c["hub"]["capex"] * crf(c["hub"]["life_years"]) + c["hub"]["opex_per_year"]
    staff_min = c["staff"]["shifts_per_day"] * c["staff"]["relief_factor"] * c["staff"]["salary_per_person_year"]
    rows = [
        ("emergency orders per day", per_day["emergency"], "demand.csv"),
        ("routine orders per day", per_day["routine"], "demand.csv"),
        ("lab orders per day", per_day["lab"], "demand.csv"),
        ("road-only cost, INR per year", road_only, "all orders by road, costs.yaml"),
        ("bays that can charge in an outage", bays_outage, "3 kW genset / 1.5 kW bay"),
        ("one hub, INR per year (CRF capex + opex)", hub_year, "costs.yaml, 10 %, 10 y"),
        ("minimum staff per open hub, INR per year", staff_min, "1 operator x 3 shifts x 1.2"),
    ]
    return pd.DataFrame(rows, columns=["quantity", "value", "source"])


def draw_hierarchy(path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    levels = [
        ("1  Vehicle  (M1, M2, M3)",
         "rotor dia. D, battery E, tube d and t, cruise V ........ continuous\n"
         "plate density field, volume fraction (M2) ................ continuous\n"
         "battery pack type, motor, tube size ..................... categorical\n"
         "battery pack count, prop size (whole inch) ................. integer",
         "5 designs A-E (radius 10-24 km)"),
        ("2  Hub network  (M4)",
         "open hub j?  y_j, 8 candidate sites ........................ binary\n"
         "PHC i served by hub j?  x_ij, 24 x 8 ....................... binary\n"
         "drones n_j, charging bays b_j (first sizing) ................ integer\n"
         "vehicle design used (solved once per design) ............ categorical",
         "open hubs + PHC assignment"),
        ("3  Fleet and policy  (M5, M6)",
         "vehicle design from A-E .................................. categorical\n"
         "drones, bays, spare batteries per open hub .................. integer\n"
         "emergency reserve fraction of idle drones .............. continuous [0, 1]\n"
         "dispatch rule (emergencies first, given) ............... categorical, fixed",
         "Pareto front: cost / S45 / road share"),
        ("4  Recommendation  (M7)",
         "one system from the Pareto front ......................... categorical\n"
         "decision rule (knee, minimax regret, best within budget) . categorical",
         "memo to the DHO"),
    ]
    fig, ax = plt.subplots(figsize=(9, 7.6))
    ax.set_xlim(0, 10); ax.set_ylim(-0.1, 8.5); ax.axis("off")
    colors = ["#dbe9f6", "#e3f1de", "#fbeedb", "#ece3f3"]
    y, h = 8.35, 1.62
    for i, (title, body, out) in enumerate(levels):
        y0 = y - h
        ax.add_patch(FancyBboxPatch((0.2, y0), 9.6, h - 0.1, boxstyle="round,pad=0.02,rounding_size=0.12",
                                    fc=colors[i], ec="#444", lw=1.0))
        ax.text(0.4, y - 0.3, title, fontsize=11.5, weight="bold", va="center")
        ax.text(0.4, y - 0.62, body, fontsize=8.6, family="monospace", va="top", linespacing=1.45)
        if i < len(levels) - 1:
            ax.annotate("", xy=(5, y0 - 0.48), xytext=(5, y0 - 0.02),
                        arrowprops=dict(arrowstyle="-|>", lw=1.6, color="#333"))
            ax.text(5.15, y0 - 0.26, "passes down: " + out, fontsize=8.6, style="italic", va="center")
        else:
            ax.text(9.6, y0 + 0.2, "output: " + out, fontsize=8.6, style="italic", ha="right")
        y = y0 - 0.55
    ax.text(0.2, -0.05, "Feedback upward: M4 cost per design and M6 front decide which vehicle survives; "
            "M3 FM and M2 plate mass re-run M1.", fontsize=8, color="#555")
    fig.suptitle("SkyLink decision hierarchy (M0): each level fixes the inputs of the next",
                 fontsize=12, y=0.99)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def run(ctx):
    checks = audit()
    checks.to_csv(RESULTS / "m0_data_checks.csv", index=False)
    flags = checks[checks.status == "FLAG"]
    print(f"  data audit: {len(checks)} checks, {len(flags)} flagged")
    for _, r in flags.iterrows():
        print(f"    FLAG {r.file}: {r.check} = {r.value} (expected {r.expected})")

    nums = framing_numbers()
    nums.to_csv(RESULTS / "m0_framing_numbers.csv", index=False)
    for _, r in nums.iterrows():
        print(f"  {r.quantity}: {r.value:,.2f}")

    draw_hierarchy(FIGURES / "m0_decision_hierarchy.png")
    hp = clean_hourly_profile(load_csv("hourly_profile.csv"))
    return {"hourly_profile": hp, "m0_data_checks": checks}
