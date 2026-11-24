# SkyLink: Designing a Medical Drone Delivery Network (ED6002)

Course project in eight modules (M0–M7). Each module's output feeds the next, and one
script, `run_all.py`, runs the whole chain.

## Install

```bash
git clone https://github.com/id24d001/skylink.git
cd skylink
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Python 3.10 or newer.

## Run

```bash
python run_all.py            # every implemented module, M0 -> M7
python run_all.py --only m3  # just one (or several) modules
```

Outputs go to `results/` (tables, CSV/JSON) and `figures/` (verification plots).
The full chain takes about 6 minutes; M2 (about 3 min) and M5 (about 1.5 min, mostly the Erlang C runs and the live-view GIF) are most of that. For M2 on a 1 mm mesh instead of the
default 2 mm one, run `SKYLINK_M2_MESH=fine python run_all.py --only m1 m2` (about 30 minutes; it gave the same ratios within 2 % at vf 0.20 and 0.25).
Running M2 also re-runs M1 with the new plate mass, so M1's files in `results/` are post-M2.
Modules that are not implemented yet are skipped with a note, so the script always runs end to end.

### Google Colab

```python
!git clone https://github.com/id24d001/skylink.git   # private repo: see note below
%cd skylink
!pip install -q -r requirements.txt
!python run_all.py
```

If the repo is private, Colab needs a GitHub personal access token (fine-grained, read-only, this
repo only): `!git clone https://<TOKEN>@github.com/id24d001/skylink.git`. Keep the token in Colab
Secrets (key icon), not in the notebook. To open a notebook directly: Colab → File → Open notebook →
GitHub tab → search `id24d001/skylink` (tick "Include private repos" if private). Notebooks find
`src/` by walking up to the repo root, so run them from inside the cloned folder.

## Module status

| Module | Folder | Status | Verification output |
|---|---|---|---|
| M0 Problem framing | `src/m0_framing/` | ✅ data audit, card drafted | `figures/m0_decision_hierarchy.png`, `results/m0_data_checks.csv` |
| M1 Vehicle MDO | `src/m1_vehicle/` | ✅ FM0 = 0.65; 5 snapped designs | `figures/m1_mda_convergence.png`, `figures/m1_gradient_check.png`, `results/m1_mdf_vs_idf.csv`, `results/m1_designs.csv` |
| M2 Hub plate topology | `src/m2_plate/` | ✅ plate-bending SIMP, density filter; vf 0.32 → 108.7 g cut part, 2.31× solid compliance, ≥ 6 mm members; M1 re-run with it | `figures/m2_mbb_benchmark.png`, `results/m2_verification.csv`, `figures/m2_compliance_vs_mass.png`, `figures/m2_outline.png`, `results/m2_plate_outline.dxf`, `results/m2_m1_feedback.csv` |
| M3 Rotor surrogate | `src/m3_surrogate/` | ✅ 30 runs fitted and verified (LOO, 5-fold, hold-out); cubic RBF; M1 re-run with it | `results/m3_loo_table.csv`, `figures/m3_loo_parity.png`, `results/m1_fm_comparison.csv`, `figures/m1_fm_rerun.png` |
| M4 Hub network and fleet | `src/m4_network/` | ✅ MILP (HiGHS) per design, brief's rules (every PHC served, still-air reach with reserve, 70 % on mean demand); D cheapest ₹1.69 cr/yr (4 hubs, 8 drones); A, B infeasible; brute force matches | `results/m4_cost_vs_design.csv`, `results/m4_bruteforce.csv`, `results/m4_lp_gap.csv`, `figures/m4_networks.png` |
| M5 Discrete-event simulator | `src/m5_simulation/` | ✅ SimPy day simulator on the M4 networks; Erlang C check 8/8, 73 days for ±3 pp; D as M4: S45 0.851 ± 0.025, wind ceiling 0.88–0.89; live view GIF | `figures/m5_mmc_check.png`, `results/m5_mmc_check.csv`, `results/m5_days_ci.csv`, `results/m5_kpis.csv`, `figures/m5_results.png`, `figures/m5_live_day.gif` |
| M6 Multi-objective optimisation | `src/m6_moo/` | ⬜ | |
| M7 Decision and defense | `src/m7_decision/` | ⬜ | |

## Structure

```
run_all.py               one script for the whole chain
requirements.txt
data/                    given data pack, unchanged (components, costs, demand, hourly_profile,
│                        nofly, sites, wind_days)
└── oracle/              FM oracle results: result_1.csv (request_/result_ 2 and 3 to come)
src/
├── common/              repo paths and data loaders shared by all modules
├── m0_framing/ … m7_decision/
│   └── run.py           run(ctx) -> dict; its presence plugs the module into run_all.py
notebooks/               exploration and step-by-step working (M1_vehicle_MDO, M2_hub_plate_topology, M3_*)
results/  figures/       written by run_all.py
report/
├── formulation_cards/   M0.md … M7.md (one page each)
├── verification_appendix.md
└── decision_log.md      decisions and AI suggestions accepted/rejected
```

## Adding a module

Create `src/mN_name/run.py` with

```python
def run(ctx):
    # ctx holds outputs of earlier modules, e.g. ctx["fm_surrogate"] from M3
    ...
    return {"my_output": value}   # merged into ctx for later modules
```

Write its verification figure/table to `figures/`/`results/`, then update the status table above,
`report/verification_appendix.md` and `report/decision_log.md`.
